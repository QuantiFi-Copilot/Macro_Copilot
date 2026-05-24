"""
test_get_otr_history_compute.py — Unit tests for the get_otr_history primitive
==============================================================================

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly (PR7 + PR12).
  2. compute() runs end-to-end against synthetic input with the bundled
     config and returns a well-formed OtrHistoryOutput.
  3. Convention overrides actually change behaviour (default_lookback_days
     wired into the Pydantic-default lookup path).
  4. NotImplementedError guards on the two categorical conventions
     (window_boundary_semantics, transition_sort_order) — PR11 + PR14.
  5. Honest absence (P5 + P6): empty otr_history result returns
     current_metrics with identity fields ``None`` and ``transitions=[]``,
     NOT an ``{"error": ...}`` envelope.
  6. Schema-layer invariants: country/tenor required, lookback_days bounded.
  7. Three import paths resolve to the same Pydantic class (per the
     yield_levels migration pattern).
  8. methodology_note surfaces the TD #27 disclosure verbatim.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.get_otr_history import (
    CONFIG_PATH,
    OtrHistoryInput,
    get_otr_history,
)
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


class _FrozenDate(date):
    _frozen_value: date = date(2026, 5, 24)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _two_window_rows() -> list[dict]:
    """Synthetic otr_history rows: one closed window + one open window."""
    return [
        {
            "effective_from": date(2025, 8, 15),
            "effective_to": date(2025, 11, 14),
            "otr_instrument_id": 101,
            "cusip": "91282CKZ4",
            "isin": "US91282CKZ40",
            "vendor_ticker": "/cusip/91282CKZ4",
            "maturity_date": date(2034, 8, 15),
        },
        {
            "effective_from": date(2025, 11, 15),
            "effective_to": None,
            "otr_instrument_id": 102,
            "cusip": "91282CLB6",
            "isin": "US91282CLB60",
            "vendor_ticker": "/cusip/91282CLB6",
            "maturity_date": date(2034, 11, 15),
        },
    ]


def _build_engine_mock(rows: list[dict]) -> MagicMock:
    """Build a mock SQLAlchemy ``Engine`` whose ``.connect()`` context
    manager yields a connection returning ``rows`` from
    ``execute().mappings().all()``.  Mirrors the live shape in
    ``shared.analytics.rates_fetch.fetch_otr_transitions``."""
    mock_engine = MagicMock(name="engine")
    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = rows
    mock_conn.execute.return_value = mock_result
    mock_engine.connect.return_value.__enter__.return_value = mock_conn
    return mock_engine


def _custom_config(**overrides) -> ToolConfig:
    """Build a custom ToolConfig with overridable conventions, modelled on
    the curve_spread / yield_levels test pattern."""
    defaults = {
        "default_lookback_days": 365,
        "window_boundary_semantics": "range_intersection",
        "transition_sort_order": "ascending",
    }
    defaults.update(overrides)

    valid_ranges = {
        "default_lookback_days": [30, 3650],
    }
    return ToolConfig(
        tool=ToolMeta(
            name="t",
            domain="d",
            description="x",
            category="desk_invariant_primitive",
        ),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(
                value=v,
                source="test",
                rationale="test",
                valid_range=valid_ranges.get(k),
            )
            for k, v in defaults.items()
        },
    )


# ===========================================================================
# 1. Bundled config.yaml — PR7 + PR12 + PR13
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "get_otr_history_tool"
        assert cfg.tool.domain == "sovereign_bonds"
        assert cfg.tool.category == "desk_invariant_primitive"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "default_lookback_days",
            "window_boundary_semantics",
            "transition_sort_order",
            "tenor_canonicalisation",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_tenor_canonicalisation_convention_present(self):
        """PR12 + Primitive-1 spec — the tenor_canonicalisation
        convention exists and uses the ADR-0007 source tag.  Catches
        silent removal of this disclosure / drift to a vague tag."""
        cfg = load_tool_config(CONFIG_PATH)
        conv = cfg.conventions["tenor_canonicalisation"]
        assert conv.source == "adr_0007_otr_canonicalisation"
        assert conv.value == "uppercase_country_integer_y_tenor"

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("default_lookback_days") == 365
        assert cfg.convention_value("window_boundary_semantics") == "range_intersection"
        assert cfg.convention_value("transition_sort_order") == "ascending"

    def test_convention_sources_are_registered(self):
        """PR12 — every Convention.source value is a registered tag from
        docs_revamped/03_standards/methodology_disclosure.md.  No vague
        ``default``/``standard``/``tbd``."""
        cfg = load_tool_config(CONFIG_PATH)
        registered = {
            "industry_standard_1y_window",
            "team_judgment_pending_review",
            "adr_0007_otr_canonicalisation",
        }
        for name, conv in cfg.conventions.items():
            assert conv.source in registered, (
                f"convention {name!r} has unregistered source {conv.source!r}"
            )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        # PR14 — categorical conventions must have a documented path to widening.
        assert "window_boundary_semantics" in joined
        assert "transition_sort_order" in joined
        # TD #27 — forward-only + detection-date disclosure.
        assert "TD #27" in joined or "forward" in joined.lower()

    def test_pr13_lookback_days_consistency(self):
        """PR13 — default_lookback_days shares the catalogue's
        existing value for this key (365 calendar days = 1Y; matches
        cross_market_inflation_swap_spread and swap_breakeven_basis_simple).
        The shared name keys the lint; sharing the value is the
        consistency rule.  Different from the *trading-day* z-score
        windows (252) those primitives also expose, which are a
        different convention name."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("default_lookback_days") == 365


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, rows, config=None):
        engine = _build_engine_mock(rows)
        with patch(
            "rates_agent.sovereign_bonds.tools.get_otr_history.compute.date",
            _FrozenDate,
        ):
            return get_otr_history(engine=engine, params=params, config=config)

    def test_default_config_returns_well_formed_output(self):
        rows = _two_window_rows()
        params = OtrHistoryInput(country="US", tenor="10Y", lookback_days=252)
        out = self._run(params, rows)

        assert "error" not in out, out.get("error")

        cm = out["current_metrics"]
        for k in (
            "country", "tenor", "as_of_date",
            "otr_instrument_id", "cusip", "isin", "vendor_ticker",
            "maturity_date", "current_effective_from",
            "transition_count_in_window", "lookback_days",
        ):
            assert k in cm, f"missing {k}"

        # Current OTR is the second (open) row.
        assert cm["cusip"] == "91282CLB6"
        assert cm["otr_instrument_id"] == 102
        assert cm["current_effective_from"] == "2025-11-15"
        assert cm["transition_count_in_window"] == 2
        assert cm["lookback_days"] == 252
        assert cm["as_of_date"] == "2026-05-24"  # frozen wall-clock

        transitions = out["transitions"]
        assert len(transitions) == 2
        # Ascending order: closed first, open last.
        assert transitions[0]["effective_from"] == "2025-08-15"
        assert transitions[0]["effective_to"] == "2025-11-14"
        assert transitions[1]["effective_from"] == "2025-11-15"
        # Open window: effective_to preserved as None.
        assert transitions[1]["effective_to"] is None

    def test_explicit_config_matches_auto_loaded(self):
        """Calling with config=load_tool_config(CONFIG_PATH) returns the
        same dict as calling with config=None (auto-load)."""
        rows = _two_window_rows()
        params = OtrHistoryInput(country="US", tenor="10Y", lookback_days=252)
        out_auto = self._run(params, rows, config=None)
        out_explicit = self._run(params, rows, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_sql_called_with_correct_window_bounds(self):
        """The SQL bind params must use ``date.today() - lookback_days`` as
        window_start and ``date.today()`` as window_end."""
        rows = _two_window_rows()
        engine = _build_engine_mock(rows)
        params = OtrHistoryInput(country="DE", tenor="10Y", lookback_days=180)
        with patch(
            "rates_agent.sovereign_bonds.tools.get_otr_history.compute.date",
            _FrozenDate,
        ):
            get_otr_history(engine=engine, params=params)

        mock_conn = engine.connect.return_value.__enter__.return_value
        call_args = mock_conn.execute.call_args
        binds = call_args.args[1] if len(call_args.args) >= 2 else call_args.kwargs
        assert binds["country"] == "DE"
        assert binds["tenor"] == "10Y"
        assert binds["window_end"] == "2026-05-24"
        # 2026-05-24 - 180 days = 2025-11-25
        assert binds["window_start"] == "2025-11-25"


# ===========================================================================
# 3. Convention overrides (PR7 — YAML drives runtime behaviour)
# ===========================================================================

class TestConventionOverrides:
    def test_default_lookback_days_feeds_pydantic_default(self):
        """Editing the YAML default_lookback_days changes the Pydantic
        schema's default for lookback_days (P10 — single source of
        truth)."""
        # Test the schema's default-factory directly with the live YAML.
        # The factory reads the bundled config at call time.
        clear_tool_config_cache()
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("default_lookback_days") == 365
        p = OtrHistoryInput(country="US", tenor="10Y")
        assert p.lookback_days == 365

    def test_lookback_days_input_override_honoured(self):
        """Per-query input overrides the YAML default."""
        p = OtrHistoryInput(country="US", tenor="10Y", lookback_days=730)
        assert p.lookback_days == 730


# ===========================================================================
# 4. Honest-placeholder guards on categorical conventions — PR11 + PR14
# ===========================================================================

class TestConventionGuards:
    def test_unsupported_window_boundary_raises(self):
        rows = _two_window_rows()
        engine = _build_engine_mock(rows)
        params = OtrHistoryInput(country="US", tenor="10Y")
        bad = _custom_config(window_boundary_semantics="effective_from_within_window")
        with pytest.raises(NotImplementedError) as exc:
            get_otr_history(engine=engine, params=params, config=bad)
        msg = str(exc.value)
        assert "effective_from_within_window" in msg
        assert "planned_extensions" in msg
        assert "window_boundary_semantics" in msg

    def test_unsupported_sort_order_raises(self):
        rows = _two_window_rows()
        engine = _build_engine_mock(rows)
        params = OtrHistoryInput(country="US", tenor="10Y")
        bad = _custom_config(transition_sort_order="descending")
        with pytest.raises(NotImplementedError) as exc:
            get_otr_history(engine=engine, params=params, config=bad)
        msg = str(exc.value)
        assert "descending" in msg
        assert "planned_extensions" in msg
        assert "transition_sort_order" in msg

    def test_supported_defaults_do_not_raise(self):
        rows = _two_window_rows()
        engine = _build_engine_mock(rows)
        params = OtrHistoryInput(country="US", tenor="10Y")
        out = get_otr_history(
            engine=engine,
            params=params,
            config=_custom_config(),
        )
        assert "error" not in out


# ===========================================================================
# 5. Honest absence (P5 + P6) — empty SCD2 → empty list + None identity
# ===========================================================================

class TestHonestAbsence:
    def test_empty_result_returns_honest_absence(self):
        engine = _build_engine_mock(rows=[])
        params = OtrHistoryInput(country="ZA", tenor="10Y", lookback_days=252)
        with patch(
            "rates_agent.sovereign_bonds.tools.get_otr_history.compute.date",
            _FrozenDate,
        ):
            out = get_otr_history(engine=engine, params=params)

        # Per P6, this is not an error envelope.
        assert "error" not in out

        cm = out["current_metrics"]
        assert cm["country"] == "ZA"
        assert cm["tenor"] == "10Y"
        assert cm["otr_instrument_id"] is None
        assert cm["cusip"] is None
        assert cm["isin"] is None
        assert cm["vendor_ticker"] is None
        assert cm["maturity_date"] is None
        assert cm["current_effective_from"] is None
        assert cm["transition_count_in_window"] == 0

        assert out["transitions"] == []
        assert out["methodology_note"]  # surfaces TD #27

    def test_closed_only_window_no_current_otr(self):
        """If every SCD2 row in the lookback is CLOSED (no open window),
        current_metrics identity fields are None — honest absence for
        'we know the history, but no bond is currently OTR for this
        slot'.  This is unusual in production (the resolver normally
        keeps one window open) but a possible state and the primitive
        must handle it cleanly."""
        rows = [
            {
                "effective_from": date(2025, 5, 15),
                "effective_to": date(2025, 8, 14),
                "otr_instrument_id": 100,
                "cusip": "91282CKA9",
                "isin": "US91282CKA90",
                "vendor_ticker": "/cusip/91282CKA9",
                "maturity_date": date(2034, 5, 15),
            },
        ]
        engine = _build_engine_mock(rows)
        params = OtrHistoryInput(country="US", tenor="10Y", lookback_days=365)
        with patch(
            "rates_agent.sovereign_bonds.tools.get_otr_history.compute.date",
            _FrozenDate,
        ):
            out = get_otr_history(engine=engine, params=params)

        cm = out["current_metrics"]
        assert cm["otr_instrument_id"] is None
        assert cm["current_effective_from"] is None
        # But the transition log still shows the closed window.
        assert len(out["transitions"]) == 1
        assert out["transitions"][0]["cusip"] == "91282CKA9"
        assert cm["transition_count_in_window"] == 1


# ===========================================================================
# 6. Schema-layer invariants
# ===========================================================================

class TestSchemaInvariants:
    def test_country_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            OtrHistoryInput(tenor="10Y")  # type: ignore[call-arg]

    def test_tenor_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            OtrHistoryInput(country="US")  # type: ignore[call-arg]

    def test_lookback_days_lower_bound(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            OtrHistoryInput(country="US", tenor="10Y", lookback_days=10)

    def test_lookback_days_upper_bound(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            OtrHistoryInput(country="US", tenor="10Y", lookback_days=99999)

    def test_lookback_days_default_from_yaml(self):
        # The Pydantic default reads from YAML — see schemas.py.
        p = OtrHistoryInput(country="US", tenor="10Y")
        assert p.lookback_days == 365


# ===========================================================================
# Tenor / country canonicalisation — PR12 + Primitive-1 spec
# ===========================================================================

class TestCanonicalisation:
    """The ``tenor_canonicalisation`` convention (config.yaml, source
    ``adr_0007_otr_canonicalisation``) plus the Pydantic validators
    enforce the resolver's slot identity at the API boundary so
    mistyped inputs fail loudly rather than degrade silently to honest
    absence at the SQL layer.  See schemas.py validators."""

    def test_lowercase_country_canonicalised(self):
        p = OtrHistoryInput(country="us", tenor="10Y")
        assert p.country == "US"

    def test_lowercase_tenor_canonicalised(self):
        p = OtrHistoryInput(country="US", tenor="10y")
        assert p.tenor == "10Y"

    def test_country_whitespace_stripped(self):
        p = OtrHistoryInput(country=" DE ", tenor="10Y")
        assert p.country == "DE"

    def test_country_iso_alpha_3_accepted(self):
        # Some resolvers may write alpha-3; both shapes are honoured.
        p = OtrHistoryInput(country="USA", tenor="10Y")
        assert p.country == "USA"

    def test_country_numeric_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="ISO-3166"):
            OtrHistoryInput(country="12", tenor="10Y")

    def test_country_too_long_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="ISO-3166"):
            OtrHistoryInput(country="USAR", tenor="10Y")

    def test_country_empty_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            OtrHistoryInput(country="", tenor="10Y")

    def test_tenor_month_rejected(self):
        """Sovereign-cash-bond slots are integer-Y; '3M' / '6M' do not
        match the resolver's universe."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="integer-Y"):
            OtrHistoryInput(country="US", tenor="3M")

    def test_tenor_decimal_rejected(self):
        """'1.5Y' / '7.5Y' don't match the integer-Y slot labels."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="integer-Y"):
            OtrHistoryInput(country="US", tenor="1.5Y")

    def test_tenor_zero_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="integer-Y"):
            OtrHistoryInput(country="US", tenor="0Y")

    def test_tenor_without_unit_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="integer-Y"):
            OtrHistoryInput(country="US", tenor="10")


# ===========================================================================
# 7. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_get_otr_history_via_package_init(self):
        from rates_agent.sovereign_bonds.tools.get_otr_history import (
            get_otr_history as via_package,
        )
        from rates_agent.sovereign_bonds.tools.get_otr_history.compute import (
            get_otr_history as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_two_paths(self):
        from rates_agent.sovereign_bonds.tools.get_otr_history import (
            OtrHistoryInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.get_otr_history.schemas import (
            OtrHistoryInput as via_schemas,
        )
        assert via_package is via_schemas


# ===========================================================================
# 8. PR10 — methodology_note surfaces TD #27 disclosure
# ===========================================================================

class TestMethodologyNoteSurface:
    def test_methodology_note_is_required_field(self):
        from pydantic import ValidationError
        from rates_agent.sovereign_bonds.tools.get_otr_history import (
            OtrHistoryOutput,
            OtrHistoryCurrentMetrics,
        )
        # Building without methodology_note must raise — pin the
        # required-field shape.
        cm = OtrHistoryCurrentMetrics(
            country="US", tenor="10Y", as_of_date="2026-05-24",
            transition_count_in_window=0, lookback_days=252,
        )
        with pytest.raises(ValidationError):
            OtrHistoryOutput(current_metrics=cm, transitions=[])  # type: ignore[call-arg]

    def test_methodology_note_names_td_27(self):
        rows = _two_window_rows()
        engine = _build_engine_mock(rows)
        params = OtrHistoryInput(country="US", tenor="10Y")
        with patch(
            "rates_agent.sovereign_bonds.tools.get_otr_history.compute.date",
            _FrozenDate,
        ):
            out = get_otr_history(engine=engine, params=params)
        note = out["methodology_note"]
        # Surfaces ADR 0007, the resolver, forward-only and TD #27.
        assert "ADR 0007" in note
        assert "TD #27" in note
        # Surfaces the resolver name and the forward-only constraint.
        assert "resolver" in note
        assert "forward-only" in note.lower() or "Forward-only" in note
        # P12 named.
        assert "P12" in note

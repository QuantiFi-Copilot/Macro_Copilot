"""
test_breakeven_inflation_simple_compute.py — Unit tests for the
generic bond-implied breakeven inflation primitive.

Mirrors ``test_real_yield_level_compute.py`` and
``test_cross_market_spread_compute.py`` (the structurally adjacent
single-leg and two-leg analogs).

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window, ddof,
     period offsets, ffill, bps/z/yield rounding, default_field_name).
  4. Honest placeholder for trailing_range_window_days: setting it to
     anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML.
  6. No-proxy guard at the fetch layer: fetch_single_tenor is called
     for the linker leg with instrument_type='inflation_linker' and
     for the nominal leg with instrument_type='sovereign_benchmark';
     adversarial probes (nominal cf in linker slot / linker cf in
     nominal slot) return controlled error envelopes.
  7. Boundary-rounding parity: snapshot.breakeven_bps equals the
     bespoke time_series[-1].breakeven_bps AND the canonical
     time_series_breakeven.rows[-1].value bit-for-bit at the YAML's
     bps_round_decimals.
  8. Wire-honesty disclosure (``methodology_label``) is sourced from
     the YAML, not hardcoded.
  9. Three import paths still resolve to the same Pydantic class.
 10. Canonical TimeSeries outputs: BPS for the breakeven history,
     Z_SCORE for the rolling z-score, with the documented series_name
     pattern.
 11. Same-country invariant guard: cross-country pairs (DE_BUND vs
     EUR_FR_LINKER, UK_GILT vs USD_TIPS) are refused before any
     market-data fetch; missing or ambiguous instrument_master
     metadata is also refused.

The country/currency lookup helper
``_fetch_curve_family_country_currency`` issues a real SQL SELECT
against ``macro_data.instrument_master`` — at unit-test time we
patch it via the module-level ``_PATCH_COUNTRY_CURRENCY`` autouse
fixture so tests don't need a live database.  Tests that exercise
the guard itself override that patch with their own stubs.

Tests are fully offline (DB-backed grounding lives in the SQL-validation
runner — see ``tests/test_breakeven_inflation_simple_sql_validation.py``).
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
    CONFIG_PATH,
    BreakevenInflationSimpleInput,
    calculate_breakeven_inflation_simple,
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


# ---------------------------------------------------------------------------
# Same-country invariant — DB-free default lookup table
# ---------------------------------------------------------------------------
# The compute path now invokes
# ``_fetch_curve_family_country_currency(engine, curve_family,
# instrument_type)`` BEFORE either market-data fetch.  That helper
# issues a real SQL SELECT against ``macro_data.instrument_master``
# which the unit tests cannot run (engine is None).  This map mirrors
# the live-DB metadata for the (curve_family, instrument_type) pairs
# used in the rest of the file so the existing happy-path tests don't
# regress.  Tests that exercise the guard itself override the patch.
_DEFAULT_COUNTRY_CURRENCY = {
    # nominal sovereigns
    ("UST",         "sovereign_benchmark"): ("US", "USD"),
    ("UK_GILT",     "sovereign_benchmark"): ("UK", "GBP"),
    ("FR_OAT",      "sovereign_benchmark"): ("France", "EUR"),
    ("DE_BUND",     "sovereign_benchmark"): ("Germany", "EUR"),
    ("IT_BTP",      "sovereign_benchmark"): ("Italy", "EUR"),
    ("CANADA_GOVT", "sovereign_benchmark"): ("Canada", "CAD"),
    # linkers
    ("USD_TIPS",      "inflation_linker"): ("US", "USD"),
    ("GBP_LINKER",    "inflation_linker"): ("UK", "GBP"),
    ("EUR_FR_LINKER", "inflation_linker"): ("France", "EUR"),
    ("CAD_RRB",       "inflation_linker"): ("Canada", "CAD"),
}


def _stub_country_currency_factory(table=None):
    """Build a stand-in for
    ``compute._fetch_curve_family_country_currency`` driven by a
    ``{(curve_family, instrument_type): value}`` mapping.

    Each value can be:
      - a ``(country, currency)`` tuple — returned as
        ``(country, currency, None)`` (success);
      - a string — returned as ``(None, None, error_string)``
        (controlled error, e.g. "no instrument_master rows");
      - the literal ``"AMBIGUOUS"`` — returned as the multiple-rows
        error envelope.

    Unknown keys default to a "no instrument_master rows" error so a
    typo in a test surfaces loudly.
    """
    table = table if table is not None else dict(_DEFAULT_COUNTRY_CURRENCY)

    def _stub(engine, curve_family, instrument_type):  # noqa: ARG001
        key = (curve_family, instrument_type)
        if key not in table:
            return (
                None,
                None,
                (
                    f"No instrument_master rows found for "
                    f"curve_family='{curve_family}', "
                    f"instrument_type='{instrument_type}'."
                ),
            )
        value = table[key]
        if isinstance(value, tuple) and len(value) == 2:
            return value[0], value[1], None
        if value == "AMBIGUOUS":
            return (
                None,
                None,
                (
                    f"Ambiguous instrument_master metadata for "
                    f"curve_family='{curve_family}', "
                    f"instrument_type='{instrument_type}'."
                ),
            )
        # Treat a plain string as an error sentinel.
        return None, None, str(value)

    return _stub


@pytest.fixture(autouse=True)
def _patch_country_currency():
    """Default patch: every test runs with the canonical (country,
    currency) map so the same-country guard in compute() succeeds for
    same-country pairs and fails for cross-country pairs.  Tests that
    want to assert the guard's branches override this patch via their
    own ``patch.object`` / nested ``patch`` calls.
    """
    with patch(
        "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._fetch_curve_family_country_currency",
        new=_stub_country_currency_factory(),
    ):
        yield


def _synthetic_single_leg(
    *,
    days: int = 600,
    frozen_today: date = date(2026, 4, 30),
    base_pct: float,
    drift_pct: float,
) -> pd.DataFrame:
    """Build a ``[trade_date, field_value]`` long-format DataFrame
    matching the shape ``fetch_single_tenor`` returns for one leg.
    Linspace from ``base_pct`` to ``base_pct + drift_pct`` over
    ``days`` business days ending at ``frozen_today``.
    """
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(base_pct, base_pct + drift_pct, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
    })


def _patched_fetch_factory(nominal_df: pd.DataFrame, linker_df: pd.DataFrame):
    """Build a stand-in for ``fetch_single_tenor`` that dispatches by
    instrument_type so the compute path's two calls hit the right
    fixture.  Empty DataFrames model "no rows match".
    """
    def _stub(
        *,
        engine,
        curve_family,
        tenor,
        field_name,
        start_date,
        instrument_type=None,
    ):
        if instrument_type == "inflation_linker":
            return linker_df
        if instrument_type == "sovereign_benchmark":
            return nominal_df
        # Defensive: the breakeven primitive must always supply an
        # instrument_type — fall through to empty so a regression here
        # surfaces as "no data" rather than a silent proxy.
        return pd.DataFrame(columns=["trade_date", "field_value"])
    return _stub


class _FrozenDate(date):
    """Patches ``compute.date`` so the fetch ``start_date`` is
    deterministic across machines.  The latest-observation cutoff
    inside compute() is anchored to the data's last index date
    (``wide.index[-1]``), not to ``date.today()``, so the frozen-today
    value only matters for the fetch-window computation (which is
    mocked away in these tests anyway).
    """
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# Default-convention dict shared across the override-style tests below.
_BUNDLED_DEFAULTS = {
    "z_score_window_days": 252,
    "z_score_min_periods": 60,
    "z_score_ddof": 1,
    "z_score_buffer_multiplier": 1.5,
    "daily_change_offset_rows": 2,
    "weekly_change_offset_rows": 6,
    "monthly_change_offset_rows": 22,
    "trailing_range_window_days": 252,
    "ffill_limit_days": 5,
    "bps_round_decimals": 2,
    "z_score_round_decimals": 4,
    "yield_round_decimals": 4,
    "default_field_name": "YLD_YTM_MID",
}


def _build_config(
    *, methodology_what_it_does: str = "test methodology label", **overrides,
) -> ToolConfig:
    defaults = dict(_BUNDLED_DEFAULTS)
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(name="t", domain="d", description="x"),
        methodology=MethodologyMeta(what_it_does=methodology_what_it_does),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_breakeven_inflation_simple_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
            "weekly_change_offset_rows",
            "monthly_change_offset_rows",
            "trailing_range_window_days",
            "ffill_limit_days",
            "bps_round_decimals",
            "z_score_round_decimals",
            "yield_round_decimals",
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_align_with_other_rates_tools(self):
        """Cross-tool convention values MUST match the sovereign /
        linker / OIS level + spread tools — the cross-config lint
        enforces this, and the test pins the values explicitly so a
        YAML edit drifting one of these silently breaks here too."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("daily_change_offset_rows") == 2
        assert cfg.convention_value("weekly_change_offset_rows") == 6
        assert cfg.convention_value("monthly_change_offset_rows") == 22
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("bps_round_decimals") == 2
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined

    def test_methodology_what_it_does_carries_disclosure(self):
        """The wire-honesty disclosure that ships in
        ``current_metrics.methodology_label`` is sourced from this
        YAML field — it MUST mention the inflation risk premium /
        liquidity premium / not-pure-expected-inflation caveat."""
        cfg = load_tool_config(CONFIG_PATH)
        text = cfg.methodology.what_it_does.lower()
        assert "compensation" in text
        assert "expected" in text  # "not a clean expected-inflation read"


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, nominal_df, linker_df, config=None):
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            return calculate_breakeven_inflation_simple(
                engine=None, params=params, config=config,
            )

    def test_default_config_returns_well_formed_output(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        for k in (
            "as_of_date",
            "nominal_curve_family",
            "linker_curve_family",
            "tenor",
            "breakeven_label",
            "breakeven_pct",
            "breakeven_bps",
            "daily_change_bps",
            "weekly_change_bps",
            "monthly_change_bps",
            "current_z_score",
            "rolling_window_days",
            "high_252d_bps",
            "low_252d_bps",
            "percentile_252d",
            "nominal_yield_pct",
            "real_yield_pct",
            "methodology_label",
        ):
            assert k in cm, f"missing {k}"

        assert "high_window_bps" not in cm
        assert "trailing_window_days" not in cm

        assert isinstance(cm["breakeven_pct"], float)
        assert isinstance(cm["breakeven_bps"], float)
        # bps is pct * 100, modulo rounding
        assert abs(cm["breakeven_bps"] - cm["breakeven_pct"] * 100) < 1.0

        assert cm["nominal_yield_pct"] is not None
        assert cm["real_yield_pct"] is not None
        decomposed_bps = (
            cm["nominal_yield_pct"] - cm["real_yield_pct"]
        ) * 100
        assert abs(cm["breakeven_bps"] - decomposed_bps) < 1.0

    def test_explicit_config_matches_auto_loaded(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_auto = self._run(params, nominal_df, linker_df, config=None)
        out_explicit = self._run(
            params, nominal_df, linker_df, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_handles_negative_real_yields(self):
        """Real yields can be negative (post-2020).  Breakeven math is
        unchanged."""
        nominal_df = _synthetic_single_leg(base_pct=2.00, drift_pct=-0.20)
        linker_df = _synthetic_single_leg(base_pct=-0.50, drift_pct=0.10)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)
        assert "error" not in out
        assert out["current_metrics"]["breakeven_pct"] > 0


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def _run(self, params, nominal_df, linker_df, config):
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            return calculate_breakeven_inflation_simple(
                engine=None, params=params, config=config,
            )

    def test_z_score_window_override_changes_z(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_default = self._run(params, nominal_df, linker_df, _build_config())
        out_short = self._run(
            params, nominal_df, linker_df,
            _build_config(z_score_window_days=120),
        )
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_short["current_metrics"]["current_z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_sample = self._run(
            params, nominal_df, linker_df, _build_config(z_score_ddof=1),
        )
        out_pop = self._run(
            params, nominal_df, linker_df, _build_config(z_score_ddof=0),
        )
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_default = self._run(params, nominal_df, linker_df, _build_config())
        out_wider = self._run(
            params, nominal_df, linker_df,
            _build_config(daily_change_offset_rows=5),
        )
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_pivot(self):
        from shared.analytics.spreads import (
            pivot_and_align_tenors as real_pivot,
        )

        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            calculate_breakeven_inflation_simple(
                engine=None, params=params,
                config=_build_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2

    def test_bps_round_decimals_override_changes_precision(self):
        nominal_df = _synthetic_single_leg(base_pct=4.234567, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.876543, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out_2 = self._run(
            params, nominal_df, linker_df, _build_config(bps_round_decimals=2),
        )
        out_4 = self._run(
            params, nominal_df, linker_df, _build_config(bps_round_decimals=4),
        )
        v2 = out_2["current_metrics"]["breakeven_bps"]
        v4 = out_4["current_metrics"]["breakeven_bps"]
        assert v2 == round(v2, 2)
        assert v4 == round(v4, 4)
        assert v2 != v4


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_breakeven_inflation_simple(
                engine=None, params=params,
                config=_build_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg


# ===========================================================================
# 5. Schema-layer field_name behaviour + cross-field validator
# ===========================================================================

class TestInputSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
        )
        assert params.field_name is None

    def test_explicit_field_name_passes_through(self):
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            field_name="YLD_YTM_BID",
        )
        assert params.field_name == "YLD_YTM_BID"

    def test_curve_families_must_differ(self):
        with pytest.raises(Exception):
            BreakevenInflationSimpleInput(
                nominal_curve_family="USD_TIPS",
                linker_curve_family="USD_TIPS",
                tenor="10Y",
            )


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches both
    fetches."""

    def _capture_fetch_calls(self, params, config):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)

        captured = []

        def _stub(**kwargs):
            captured.append(kwargs)
            if kwargs["instrument_type"] == "inflation_linker":
                return linker_df
            return nominal_df

        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_stub,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            calculate_breakeven_inflation_simple(
                engine=None, params=params, config=config,
            )
        return captured

    def test_omitted_field_name_uses_yaml_default(self):
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params, _build_config(default_field_name="YLD_YTM_MID"),
        )
        assert len(captured) == 2
        for call in captured:
            assert call["field_name"] == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
        )
        captured = self._capture_fetch_calls(
            params, _build_config(default_field_name="YLD_YTM_BID"),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            field_name="YLD_YTM_ASK",
        )
        captured = self._capture_fetch_calls(
            params, _build_config(default_field_name="YLD_YTM_MID"),
        )
        for call in captured:
            assert call["field_name"] == "YLD_YTM_ASK"


# ===========================================================================
# 6. No-proxy guard (instrument_type filter on both legs)
# ===========================================================================

class TestNoProxyGuard:
    """The breakeven primitive must enforce instrument_type at the
    fetch boundary on BOTH legs:

      - Linker leg: instrument_type='inflation_linker'
      - Nominal leg: instrument_type='sovereign_benchmark'

    Without these filters, an LLM passing two linker curves (or two
    nominal curves) would get a near-zero "breakeven" — a proxy
    violation forbidden by DESIGN_PRINCIPLES.md §1, §3 and
    STANDARD_TOOL_AND_YAML_RULES.md §J.
    """

    def test_fetch_called_for_linker_leg_with_inflation_linker_filter(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        captured = []

        def _stub(**kwargs):
            captured.append(kwargs)
            if kwargs["instrument_type"] == "inflation_linker":
                return linker_df
            return nominal_df

        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_stub,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            calculate_breakeven_inflation_simple(engine=None, params=params)

        assert len(captured) == 2
        by_curve = {c["curve_family"]: c for c in captured}
        assert by_curve["USD_TIPS"]["instrument_type"] == "inflation_linker"
        assert by_curve["UST"]["instrument_type"] == "sovereign_benchmark"

    def test_fetch_called_for_nominal_leg_with_sovereign_benchmark_filter(self):
        """The nominal leg MUST filter on
        instrument_type='sovereign_benchmark' — without it, a caller
        passing a linker curve_family in the nominal slot would get
        linker real yields under a nominal-yield label and the
        breakeven would silently collapse to ~zero."""
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        captured = []

        def _stub(**kwargs):
            captured.append(kwargs)
            if kwargs["instrument_type"] == "inflation_linker":
                return linker_df
            return nominal_df

        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_stub,
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            calculate_breakeven_inflation_simple(engine=None, params=params)

        nominal_calls = [c for c in captured if c["curve_family"] == "UST"]
        assert len(nominal_calls) == 1
        assert nominal_calls[0]["instrument_type"] == "sovereign_benchmark", (
            "compute() must pass instrument_type='sovereign_benchmark' "
            "to the nominal leg's fetch_single_tenor call — without "
            "it, a linker curve_family in the nominal slot would "
            "silently flow through and collapse the breakeven."
        )

    def test_linker_cf_in_linker_slot_with_no_rows_returns_error_envelope(self):
        """When the linker leg returns zero rows (e.g. a typo or a
        stale universe), the tool must return a controlled error
        envelope — no fabricated breakeven."""
        empty_df = pd.DataFrame(columns=["trade_date", "field_value"])
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, empty_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_breakeven_inflation_simple(
                engine=None, params=params,
            )
        assert "error" in out
        assert "current_metrics" not in out
        assert "inflation_linker" in out["error"]

    def test_nominal_cf_in_linker_slot_returns_error_envelope(self):
        """Adversarial probe: a nominal curve_family in the linker slot
        MUST return the controlled error envelope.  Modeled by an
        empty linker fetch (instrument_type='inflation_linker' filter
        eliminates all rows when curve_family is nominal)."""
        empty_linker = pd.DataFrame(columns=["trade_date", "field_value"])
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="DE_BUND",
            linker_curve_family="UST",  # nominal curve in linker slot
            tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, empty_linker),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_breakeven_inflation_simple(
                engine=None, params=params,
            )
        assert "error" in out
        assert "current_metrics" not in out
        assert "inflation_linker" in out["error"]

    def test_linker_cf_in_nominal_slot_returns_error_envelope(self):
        """Mirror adversarial probe: a linker curve_family in the
        nominal slot MUST return the controlled error envelope.
        Modeled by an empty nominal fetch
        (instrument_type='sovereign_benchmark' eliminates all rows)."""
        empty_nominal = pd.DataFrame(columns=["trade_date", "field_value"])
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="USD_TIPS",  # linker curve in nominal slot
            linker_curve_family="GBP_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(empty_nominal, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_breakeven_inflation_simple(
                engine=None, params=params,
            )
        assert "error" in out
        assert "current_metrics" not in out
        assert "sovereign_benchmark" in out["error"]


# ===========================================================================
# 6b. Same-country / same-currency invariant guard
# ===========================================================================

class TestSameCountryGuard:
    """The same-country invariant lives in
    ``compute._enforce_same_country_invariant``.  Both legs MUST
    share country AND currency as stored in
    ``macro_data.instrument_master``; cross-country pairs are
    refused at compute time with a controlled error envelope and
    NEVER reach the market-data fetch.

    A bond-implied breakeven is by construction a same-country
    object — the structural invariant lives in code (not YAML), per
    DESIGN_PRINCIPLES.md §5 and STANDARD_TOOL_AND_YAML_RULES.md §H.
    """

    def _run(
        self,
        params,
        nominal_df,
        linker_df,
        *,
        country_currency_table=None,
        track_fetch=True,
    ):
        """Run compute with a custom country/currency stub.  Records
        whether ``fetch_single_tenor`` was invoked so the guard's
        no-fetch contract can be asserted."""
        fetch_calls = []

        def _record_fetch(**kwargs):
            fetch_calls.append(kwargs)
            if kwargs["instrument_type"] == "inflation_linker":
                return linker_df
            return nominal_df

        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._fetch_curve_family_country_currency",
            new=_stub_country_currency_factory(country_currency_table),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_record_fetch if track_fetch else _patched_fetch_factory(nominal_df, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_breakeven_inflation_simple(
                engine=None, params=params,
            )
        return out, fetch_calls

    def test_same_country_pair_passes_and_fetches(self):
        """UST (US/USD) + USD_TIPS (US/USD) is same-country — the
        guard MUST pass and the fetch path MUST execute."""
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out, fetch_calls = self._run(
            params,
            nominal_df,
            linker_df,
            country_currency_table={
                ("UST", "sovereign_benchmark"): ("US", "USD"),
                ("USD_TIPS", "inflation_linker"): ("US", "USD"),
            },
        )
        assert "error" not in out, out.get("error")
        # Both legs must have been fetched once each.
        types_called = sorted(c["instrument_type"] for c in fetch_calls)
        assert types_called == ["inflation_linker", "sovereign_benchmark"]

    def test_cross_country_same_currency_pair_refused(self):
        """DE_BUND (Germany/EUR) + EUR_FR_LINKER (France/EUR) — same
        currency, different country.  This is the load-bearing case
        that exposed the round-1 bug; it MUST be refused before any
        market-data fetch fires, and the error MUST surface both
        curve families AND the (country, currency) tuples.
        """
        nominal_df = _synthetic_single_leg(base_pct=2.30, drift_pct=-0.20)
        linker_df = _synthetic_single_leg(base_pct=0.40, drift_pct=-0.10)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="DE_BUND",
            linker_curve_family="EUR_FR_LINKER",
            tenor="10Y",
            lookback_days=365,
        )
        out, fetch_calls = self._run(
            params,
            nominal_df,
            linker_df,
            country_currency_table={
                ("DE_BUND", "sovereign_benchmark"): ("Germany", "EUR"),
                ("EUR_FR_LINKER", "inflation_linker"): ("France", "EUR"),
            },
        )
        assert sorted(out.keys()) == ["error"]
        assert "current_metrics" not in out
        assert fetch_calls == [], (
            "Cross-country pair must be refused BEFORE any market-data "
            f"fetch fires.  Got {len(fetch_calls)} fetch call(s)."
        )
        err = out["error"]
        # Both curve families surfaced.
        assert "DE_BUND" in err
        assert "EUR_FR_LINKER" in err
        # Both (country, currency) identities surfaced.
        assert "Germany" in err
        assert "France" in err
        # Same-country invariant explicitly named.
        assert "same-country" in err.lower()

    def test_cross_currency_pair_refused(self):
        """UK_GILT (UK/GBP) + USD_TIPS (US/USD) — different country
        AND different currency.  MUST be refused before any
        market-data fetch fires."""
        nominal_df = _synthetic_single_leg(base_pct=4.50, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UK_GILT",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out, fetch_calls = self._run(
            params,
            nominal_df,
            linker_df,
            country_currency_table={
                ("UK_GILT", "sovereign_benchmark"): ("UK", "GBP"),
                ("USD_TIPS", "inflation_linker"): ("US", "USD"),
            },
        )
        assert sorted(out.keys()) == ["error"]
        assert fetch_calls == [], (
            "Cross-currency pair must be refused BEFORE any market-data "
            "fetch fires."
        )
        err = out["error"]
        assert "UK_GILT" in err
        assert "USD_TIPS" in err
        assert "UK" in err and "GBP" in err
        assert "US" in err and "USD" in err
        assert "same-country" in err.lower()

    def test_missing_instrument_master_row_for_one_leg_refused(self):
        """If the instrument_master lookup returns zero rows for one
        leg, the guard MUST refuse and never invoke the fetch."""
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        # Linker leg: empty table entry — stub returns the
        # "no rows" controlled error.
        out, fetch_calls = self._run(
            params,
            nominal_df,
            linker_df,
            country_currency_table={
                ("UST", "sovereign_benchmark"): ("US", "USD"),
                # USD_TIPS/inflation_linker omitted intentionally —
                # stub default returns the "no rows" branch.
            },
        )
        assert sorted(out.keys()) == ["error"]
        assert fetch_calls == []
        err = out["error"]
        assert "linker leg" in err.lower()
        assert "USD_TIPS" in err

    def test_ambiguous_instrument_master_rows_refused(self):
        """If the instrument_master lookup returns multiple distinct
        (country, currency) rows for a leg, the guard MUST refuse —
        no silent picking."""
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out, fetch_calls = self._run(
            params,
            nominal_df,
            linker_df,
            country_currency_table={
                # Nominal leg ambiguous — sentinel triggers the
                # "multiple distinct rows" branch in the stub.
                ("UST", "sovereign_benchmark"): "AMBIGUOUS",
                ("USD_TIPS", "inflation_linker"): ("US", "USD"),
            },
        )
        assert sorted(out.keys()) == ["error"]
        assert fetch_calls == []
        err = out["error"]
        assert "nominal leg" in err.lower()
        assert "UST" in err

    def test_real_helper_signature(self):
        """The runtime helper has the documented signature
        ``(engine, curve_family, instrument_type) -> (country,
        currency, error)`` so tests + future callers stay coherent."""
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute import (
            _fetch_curve_family_country_currency,
        )
        import inspect
        sig = inspect.signature(_fetch_curve_family_country_currency)
        params = list(sig.parameters)
        assert params == ["engine", "curve_family", "instrument_type"]


# ===========================================================================
# 7. Boundary-rounding parity
# ===========================================================================

class TestBoundaryRoundingParity:
    """The snapshot's breakeven_bps MUST equal the bespoke
    time_series[-1].breakeven_bps AND the canonical
    time_series_breakeven.rows[-1].value bit-for-bit (not just within
    tolerance) — all three go through the same bps_round_decimals
    convention applied via compute_spread_bps and the canonical
    builder.
    """

    def _run(self, params, nominal_df, linker_df, *, config=None):
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            return calculate_breakeven_inflation_simple(
                engine=None, params=params, config=config,
            )

    def test_snapshot_bps_matches_bespoke_last_row_strictly(self):
        nominal_df = _synthetic_single_leg(base_pct=4.234, drift_pct=-0.301)
        linker_df = _synthetic_single_leg(base_pct=1.876, drift_pct=-0.402)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)
        snap = out["current_metrics"]["breakeven_bps"]
        bespoke_last = out["time_series"][-1]["breakeven_bps"]
        assert snap == bespoke_last

    def test_snapshot_bps_matches_canonical_last_row_strictly(self):
        nominal_df = _synthetic_single_leg(base_pct=4.234, drift_pct=-0.301)
        linker_df = _synthetic_single_leg(base_pct=1.876, drift_pct=-0.402)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)
        snap = out["current_metrics"]["breakeven_bps"]
        canon_last = out["time_series_breakeven"]["rows"][-1]["value"]
        assert snap == canon_last

    def test_z_score_yaml_propagates_to_canonical_zscore_rows(self):
        nominal_df = _synthetic_single_leg(base_pct=4.234, drift_pct=-0.301)
        linker_df = _synthetic_single_leg(base_pct=1.876, drift_pct=-0.402)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(
            params, nominal_df, linker_df,
            config=_build_config(z_score_round_decimals=2),
        )
        for row in out["time_series_zscore"]["rows"]:
            v = row["value"]
            if v is not None:
                assert v == round(v, 2)


# ===========================================================================
# 8. Wire-honesty disclosure threading
# ===========================================================================

class TestMethodologyLabelThreading:
    """The methodology disclosure on the wire MUST come from the YAML,
    not a hardcoded Python literal.  Guards against a future refactor
    that silently introduces a stale literal."""

    def _run(self, params, nominal_df, linker_df, *, config):
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            return calculate_breakeven_inflation_simple(
                engine=None, params=params, config=config,
            )

    def test_methodology_label_sourced_from_yaml(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        custom_text = (
            "CUSTOM-DISCLOSURE-12345 inflation compensation, not "
            "expected inflation"
        )
        out = self._run(
            params, nominal_df, linker_df,
            config=_build_config(methodology_what_it_does=custom_text),
        )
        assert out["current_metrics"]["methodology_label"] == custom_text.strip()

    def test_bundled_methodology_label_carries_compensation_caveat(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            out = calculate_breakeven_inflation_simple(
                engine=None, params=params,
            )
        label = out["current_metrics"]["methodology_label"].lower()
        assert "compensation" in label
        assert "expected" in label


# ===========================================================================
# 9. Import path stability
# ===========================================================================

class TestImportPathStability:
    def test_function_via_package_init(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
            calculate_breakeven_inflation_simple as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute import (
            calculate_breakeven_inflation_simple as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
            BreakevenInflationSimpleInput as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.schemas import (
            BreakevenInflationSimpleInput as via_schemas,
        )
        from rates_agent.inflation_indexed_bonds.tools.schemas import (
            BreakevenInflationSimpleInput as via_hub,
        )
        assert via_package is via_schemas is via_hub


# ===========================================================================
# 10. Canonical TimeSeries outputs
# ===========================================================================

class TestCanonicalTimeSeries:
    def _run(self, params, nominal_df, linker_df, *, config=None):
        with patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor",
            new=_patched_fetch_factory(nominal_df, linker_df),
        ), patch(
            "rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.date",
            _FrozenDate,
        ):
            return calculate_breakeven_inflation_simple(
                engine=None, params=params, config=config,
            )

    def test_canonical_breakeven_uses_bps_units_and_naming(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)
        ts = out["time_series_breakeven"]
        assert ts["units"] == "bps"
        assert ts["series_name"] == "ust_usd_tips_10y_breakeven"

    def test_canonical_zscore_uses_z_score_units_and_naming(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)
        ts = out["time_series_zscore"]
        assert ts["units"] == "z_score"
        assert ts["series_name"] == "ust_usd_tips_10y_zscore"

    def test_canonical_breakeven_length_matches_bespoke(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)
        assert len(out["time_series"]) == len(
            out["time_series_breakeven"]["rows"]
        )
        assert len(out["time_series"]) == len(
            out["time_series_zscore"]["rows"]
        )

    def test_canonical_payloads_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)
        TimeSeries.model_validate(out["time_series_breakeven"])
        TimeSeries.model_validate(out["time_series_zscore"])

    def test_canonical_breakeven_dates_chronological(self):
        nominal_df = _synthetic_single_leg(base_pct=4.20, drift_pct=-0.30)
        linker_df = _synthetic_single_leg(base_pct=1.85, drift_pct=-0.40)
        params = BreakevenInflationSimpleInput(
            nominal_curve_family="UST",
            linker_curve_family="USD_TIPS",
            tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params, nominal_df, linker_df)
        dates = [r["date"] for r in out["time_series_breakeven"]["rows"]]
        assert dates == sorted(dates)

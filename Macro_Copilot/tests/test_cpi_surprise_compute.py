"""
test_cpi_surprise_compute.py — Unit tests for the cpi_surprise primitive
=========================================================================

Covers:
  1. Bundled config.yaml structurally valid + loads cleanly (PR7 + PR12).
  2. compute() runs end-to-end against synthetic input with the bundled
     config and returns a well-formed CpiSurpriseOutput.
  3. Convention overrides actually change behaviour (release_z_window,
     surprise_pct_round_decimals, country → event_type mapping).
  4. NotImplementedError guard on the categorical convention
     (surprise_formula) — PR11 + PR14.
  5. Honest absence (P5 + P6):
     a. Empty raw_df → controlled error envelope (no fabricated surprise).
     b. All-scheduled (no realised) → error envelope.
     c. Rows with consensus_median=None emit surprise=None (NOT zero).
     d. Unsupported country → controlled error envelope with the
        registered country list.
  6. Schema-layer invariants: country required, lookback_releases
     bounded, country canonicalised, mistyped inputs rejected.
  7. Three import paths resolve to the same Pydantic class.
  8. methodology_note surfaces the ADR 0008 §2 + TD #28b disclosures.
  9. Bespoke time_series and canonical TimeSeries fields are 1-to-1
     by construction (cannot drift).
 10. Country → event_type resolution: US/UK/JP → cpi_yoy; EU → hicp_yoy
     reaches the fetcher correctly.

Tests are fully offline; the SQL fetcher is patched.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.inflation_swaps.tools.cpi_surprise import (
    CONFIG_PATH,
    CpiSurpriseInput,
    calculate_cpi_surprise,
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
    _frozen_value: date = date(2026, 5, 22)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _build_raw_df(
    *,
    n_releases: int = 30,
    end: date = date(2026, 5, 10),
    actual_base: float = 3.2,
    consensus_base: float = 3.1,
    null_consensus_indices: tuple = (),
    placeholder_at_end: int = 0,
    country: str = "US",
    event_type: str = "cpi_yoy",
) -> pd.DataFrame:
    """Build a synthetic event_calendar-shaped frame for one
    (event_type, country) series.

    Releases land monthly (every ~30 calendar days); the last
    ``placeholder_at_end`` rows have ``actual`` = None (scheduled).
    ``null_consensus_indices`` (0-indexed) mark rows where the
    consensus_median is None (no survey — honest absence).
    """
    rows = []
    d = end
    for i in range(n_releases):
        release_d = d - pd.Timedelta(days=30 * (n_releases - 1 - i))
        is_placeholder = i >= (n_releases - placeholder_at_end)
        is_null_consensus = i in null_consensus_indices
        actual = None if is_placeholder else actual_base + 0.05 * (i % 7)
        consensus = (
            None
            if (is_placeholder or is_null_consensus)
            else consensus_base + 0.04 * (i % 5)
        )
        prior = (
            None
            if i == 0 or is_placeholder
            else actual_base + 0.05 * ((i - 1) % 7)
        )
        rows.append({
            "event_id": 1000 + i,
            "event_type": event_type,
            "event_category": "economic_release",
            "country": country,
            "currency": "USD" if country == "US" else (
                "EUR" if country == "EU" else (
                    "GBP" if country == "UK" else "JPY"
                )
            ),
            "release_date": release_d.date() if hasattr(release_d, "date") else release_d,
            "release_time": None,
            "period": release_d.strftime("%b %Y") if hasattr(release_d, "strftime") else None,
            "actual": actual,
            "consensus_median": consensus,
            "consensus_high": (
                None if consensus is None else consensus + 0.1
            ),
            "consensus_low": (
                None if consensus is None else consensus - 0.1
            ),
            "prior": prior,
            "revised_prior": None,
            "surprise_std_dev": (
                None if consensus is None else 0.05
            ),
        })
    return pd.DataFrame(rows)


def _custom_config(**overrides) -> ToolConfig:
    """Build a custom ToolConfig with overridable conventions."""
    defaults = {
        "default_lookback_releases": 24,
        "release_z_window": 24,
        "release_z_min_periods": 6,
        "release_z_ddof": 1,
        "release_fetch_buffer_days_per_release": 35,
        "surprise_formula": "actual_minus_consensus_median",
        "cpi_event_type_for_us": "cpi_yoy",
        "cpi_event_type_for_uk": "cpi_yoy",
        "cpi_event_type_for_jp": "cpi_yoy",
        "cpi_event_type_for_eu": "hicp_yoy",
        "country_canonicalisation": "uppercase_iso_or_region_code",
        "surprise_pct_round_decimals": 4,
        "release_z_round_decimals": 4,
    }
    defaults.update(overrides)

    valid_ranges = {
        "default_lookback_releases": [4, 200],
        "release_z_window": [6, 60],
        "release_z_min_periods": [3, 24],
        "release_fetch_buffer_days_per_release": [25, 45],
        "surprise_pct_round_decimals": [2, 8],
        "release_z_round_decimals": [0, 8],
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
        assert cfg.tool.name == "calculate_cpi_surprise_tool"
        assert cfg.tool.domain == "inflation_swaps"
        assert cfg.tool.category == "desk_invariant_primitive"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "default_lookback_releases",
            "release_z_window",
            "release_z_min_periods",
            "release_z_ddof",
            "release_fetch_buffer_days_per_release",
            "surprise_formula",
            "cpi_event_type_for_us",
            "cpi_event_type_for_uk",
            "cpi_event_type_for_jp",
            "cpi_event_type_for_eu",
            "country_canonicalisation",
            "surprise_pct_round_decimals",
            "release_z_round_decimals",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_release_z_window_value(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("release_z_window") == 24

    def test_country_to_event_type_mapping_eu_is_hicp(self):
        """Critical correctness — EU resolves to hicp_yoy, NOT cpi_yoy."""
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("cpi_event_type_for_us") == "cpi_yoy"
        assert cfg.convention_value("cpi_event_type_for_uk") == "cpi_yoy"
        assert cfg.convention_value("cpi_event_type_for_jp") == "cpi_yoy"
        assert cfg.convention_value("cpi_event_type_for_eu") == "hicp_yoy"

    def test_convention_sources_registered(self):
        """PR12 — every Convention.source value is a registered tag
        from docs_revamped/03_standards/methodology_disclosure.md."""
        cfg = load_tool_config(CONFIG_PATH)
        registered = {
            "team_judgment_pending_review",
            "industry_standard_release_window",
            "industry_standard_sample_std",
            "derived_from_window",
            "adr_0008_event_playbook_contract",
        }
        for name, conv in cfg.conventions.items():
            assert conv.source in registered, (
                f"convention {name!r} has unregistered source {conv.source!r}"
            )

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "revision-adjusted" in joined.lower() or "revision" in joined.lower()
        assert "surprise_formula" in joined
        # TD #28 disclosure
        assert "TD #28" in joined or "ECO_RELEASE_DT_LIST" in joined


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, raw_df, config=None):
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df) as mock_fetch, patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_cpi_surprise(
                engine=mock_engine, params=params, config=config,
            )
        return out, mock_fetch

    def test_us_default_config_returns_well_formed_output(self):
        raw_df = _build_raw_df(n_releases=40, country="US")
        params = CpiSurpriseInput(country="US", lookback_releases=12)
        out, _ = self._run(params, raw_df)

        assert "error" not in out, out.get("error")

        cm = out["current_metrics"]
        for k in (
            "release_date",
            "country",
            "event_type",
            "period",
            "surprise_label",
            "current_surprise_pct",
            "current_z_score",
            "release_z_window_releases",
            "current_actual_pct",
            "current_consensus_median_pct",
            "current_prior_pct",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        assert cm["country"] == "US"
        assert cm["event_type"] == "cpi_yoy"
        assert cm["surprise_label"] == "US CPI YoY surprise"
        assert cm["release_z_window_releases"] == 24
        assert isinstance(cm["current_surprise_pct"], float)
        assert cm["observation_count"] == 12  # trimmed to lookback_releases

        # Canonical TimeSeries declared units
        assert out["time_series_surprise"]["units"] == "percent"
        assert out["time_series_zscore"]["units"] == "z_score"
        assert out["time_series_surprise"]["series_name"] == "us_cpi_surprise"
        assert out["time_series_zscore"]["series_name"] == "us_cpi_surprise_zscore"

        # Lengths agree (PR14 — bespoke/canonical cannot drift)
        n = len(out["time_series"])
        assert n == len(out["time_series_surprise"]["rows"])
        assert n == len(out["time_series_zscore"]["rows"])

    def test_eu_resolves_to_hicp_yoy(self):
        """EU country must produce a fetch call with event_type=hicp_yoy
        — NOT cpi_yoy.  This is the load-bearing country → event_type
        resolution; misrouting here would silently miss every EU
        release in event_calendar."""
        raw_df = _build_raw_df(n_releases=40, country="EU",
                                event_type="hicp_yoy")
        params = CpiSurpriseInput(country="EU", lookback_releases=12)
        out, mock_fetch = self._run(params, raw_df)
        assert "error" not in out, out.get("error")
        assert mock_fetch.call_args.kwargs["event_type"] == "hicp_yoy"
        assert mock_fetch.call_args.kwargs["country"] == "EU"
        cm = out["current_metrics"]
        assert cm["event_type"] == "hicp_yoy"
        assert cm["surprise_label"] == "EU HICP YoY surprise"

    def test_uk_and_jp_resolve_to_cpi_yoy(self):
        for country in ("UK", "JP"):
            raw_df = _build_raw_df(n_releases=40, country=country,
                                    event_type="cpi_yoy")
            params = CpiSurpriseInput(country=country, lookback_releases=12)
            out, mock_fetch = self._run(params, raw_df)
            assert "error" not in out, out.get("error")
            assert mock_fetch.call_args.kwargs["event_type"] == "cpi_yoy"

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _build_raw_df(n_releases=40, country="US")
        params = CpiSurpriseInput(country="US", lookback_releases=12)
        out_auto, _ = self._run(params, raw_df, config=None)
        out_explicit, _ = self._run(
            params, raw_df, config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit

    def test_surprise_identity_is_actual_minus_consensus(self):
        """Spot-check the identity: surprise_pct == actual − consensus_median
        on a row with both fields populated."""
        raw_df = _build_raw_df(n_releases=20, country="US")
        params = CpiSurpriseInput(country="US", lookback_releases=10)
        out, _ = self._run(params, raw_df)
        for row in out["time_series"]:
            if row["actual_pct"] is not None and row["consensus_median_pct"] is not None:
                expected = round(
                    row["actual_pct"] - row["consensus_median_pct"], 4,
                )
                assert row["surprise_pct"] == expected, (
                    f"{row['date']}: surprise_pct={row['surprise_pct']!r} "
                    f"!= actual_pct − consensus_median_pct = {expected!r}"
                )


# ===========================================================================
# 3. Convention overrides (PR7)
# ===========================================================================

class TestConventionOverrides:
    def test_release_z_window_override_surfaces_in_metrics(self):
        raw_df = _build_raw_df(n_releases=40, country="US")
        params = CpiSurpriseInput(country="US", lookback_releases=12)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out_default = calculate_cpi_surprise(
                engine=mock_engine, params=params,
                config=_custom_config(release_z_window=24),
            )
            out_tight = calculate_cpi_surprise(
                engine=mock_engine, params=params,
                config=_custom_config(release_z_window=12,
                                       release_z_min_periods=4),
            )
        assert out_default["current_metrics"]["release_z_window_releases"] == 24
        assert out_tight["current_metrics"]["release_z_window_releases"] == 12

    def test_surprise_rounding_override(self):
        raw_df = _build_raw_df(n_releases=20, country="US")
        params = CpiSurpriseInput(country="US", lookback_releases=10)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out_2dp = calculate_cpi_surprise(
                engine=mock_engine, params=params,
                config=_custom_config(surprise_pct_round_decimals=2),
            )
        # 2dp output has only 2 decimal places of precision.
        for row in out_2dp["time_series"]:
            if row["surprise_pct"] is not None:
                rounded = round(row["surprise_pct"], 2)
                assert abs(row["surprise_pct"] - rounded) < 1e-9


# ===========================================================================
# 4. Honest-placeholder guard — PR11 + PR14
# ===========================================================================

class TestConventionGuards:
    def test_unsupported_surprise_formula_raises(self):
        raw_df = _build_raw_df(n_releases=10, country="US")
        params = CpiSurpriseInput(country="US")
        bad = _custom_config(surprise_formula="actual_minus_consensus_mean")
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            with pytest.raises(NotImplementedError) as exc:
                calculate_cpi_surprise(
                    engine=mock_engine, params=params, config=bad,
                )
        msg = str(exc.value)
        assert "actual_minus_consensus_mean" in msg
        assert "surprise_formula" in msg
        assert "planned_extensions" in msg


# ===========================================================================
# 5. Honest absence (P5 + P6)
# ===========================================================================

class TestHonestAbsence:
    def test_empty_raw_df_returns_error_envelope(self):
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        empty = pd.DataFrame(columns=[
            "event_id", "event_type", "event_category", "country",
            "currency", "release_date", "release_time", "period",
            "actual", "consensus_median", "consensus_high",
            "consensus_low", "prior", "revised_prior", "surprise_std_dev",
        ])
        params = CpiSurpriseInput(country="US", lookback_releases=12)
        with patch(target, return_value=empty), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_cpi_surprise(engine=mock_engine, params=params)
        assert "error" in out
        assert "US" in out["error"]
        assert "cpi_yoy" in out["error"]
        assert "TD #28" in out["error"] or "ADR 0008" in out["error"]

    def test_all_scheduled_no_realised_returns_error(self):
        raw_df = _build_raw_df(
            n_releases=8, country="US", placeholder_at_end=8,
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        params = CpiSurpriseInput(country="US", lookback_releases=12)
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_cpi_surprise(engine=mock_engine, params=params)
        assert "error" in out
        assert "realised" in out["error"].lower() or "scheduled" in out["error"].lower()

    def test_null_consensus_emits_none_surprise(self):
        """Rows where Bloomberg has no survey (consensus_median IS NULL)
        emit surprise=None — NOT a fabricated zero."""
        raw_df = _build_raw_df(
            n_releases=20, country="US",
            null_consensus_indices=(5, 6, 7),  # 3 rows mid-series
        )
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        params = CpiSurpriseInput(country="US", lookback_releases=20)
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_cpi_surprise(engine=mock_engine, params=params)
        assert "error" not in out, out.get("error")
        none_rows = [
            r for r in out["time_series"]
            if r["consensus_median_pct"] is None
        ]
        assert len(none_rows) > 0
        for r in none_rows:
            assert r["surprise_pct"] is None, (
                f"{r['date']}: consensus_median_pct=None but "
                f"surprise_pct={r['surprise_pct']!r} — should be None"
            )

    def test_unsupported_country_returns_error_envelope(self):
        raw_df = _build_raw_df(n_releases=10, country="CA")
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        params = CpiSurpriseInput(country="CA")
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_cpi_surprise(engine=mock_engine, params=params)
        assert "error" in out
        assert "CA" in out["error"]
        # Lists the supported countries.
        for c in ("US", "UK", "JP", "EU"):
            assert c in out["error"]


# ===========================================================================
# 6. Schema-layer invariants
# ===========================================================================

class TestSchemaInvariants:
    def test_country_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CpiSurpriseInput()  # type: ignore[call-arg]

    def test_lookback_releases_lower_bound(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CpiSurpriseInput(country="US", lookback_releases=2)

    def test_lookback_releases_upper_bound(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CpiSurpriseInput(country="US", lookback_releases=10_000)

    def test_lookback_releases_default_from_yaml(self):
        p = CpiSurpriseInput(country="US")
        assert p.lookback_releases == 24


# ===========================================================================
# 7. Country canonicalisation — PR12
# ===========================================================================

class TestCanonicalisation:
    def test_lowercase_canonicalised(self):
        p = CpiSurpriseInput(country="us")
        assert p.country == "US"

    def test_whitespace_stripped(self):
        p = CpiSurpriseInput(country=" eu ")
        assert p.country == "EU"

    def test_alpha_3_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CpiSurpriseInput(country="USA")

    def test_numeric_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CpiSurpriseInput(country="12")

    def test_empty_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            CpiSurpriseInput(country="")


# ===========================================================================
# 8. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_compute_via_package_init(self):
        from rates_agent.inflation_swaps.tools.cpi_surprise import (
            calculate_cpi_surprise as via_package,
        )
        from rates_agent.inflation_swaps.tools.cpi_surprise.compute import (
            calculate_cpi_surprise as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.inflation_swaps.tools.cpi_surprise import (
            CpiSurpriseInput as via_package,
        )
        from rates_agent.inflation_swaps.tools.cpi_surprise.schemas import (
            CpiSurpriseInput as via_schemas,
        )
        from rates_agent.inflation_swaps.tools.schemas import (
            CpiSurpriseInput as via_hub,
        )
        assert via_package is via_schemas
        assert via_package is via_hub


# ===========================================================================
# 9. PR10 — methodology_note disclosure
# ===========================================================================

class TestMethodologyNoteSurface:
    def test_methodology_note_required(self):
        from pydantic import ValidationError
        from rates_agent.inflation_swaps.tools.cpi_surprise import (
            CpiSurpriseCurrentMetrics,
            CpiSurpriseOutput,
        )
        from shared.schemas import TimeSeries, TimeSeriesUnits
        cm = CpiSurpriseCurrentMetrics(
            release_date="2026-04-12",
            country="US",
            event_type="cpi_yoy",
            surprise_label="US CPI YoY surprise",
            release_z_window_releases=24,
            observation_count=0,
        )
        empty_pct = TimeSeries(
            series_name="us_cpi_surprise",
            units=TimeSeriesUnits.PERCENT,
            description="x",
            rows=[],
        )
        empty_z = TimeSeries(
            series_name="us_cpi_surprise_zscore",
            units=TimeSeriesUnits.Z_SCORE,
            description="x",
            rows=[],
        )
        with pytest.raises(ValidationError):
            CpiSurpriseOutput(  # type: ignore[call-arg]
                current_metrics=cm,
                time_series=[],
                time_series_surprise=empty_pct,
                time_series_zscore=empty_z,
            )

    def test_methodology_note_names_adr_0008_and_td_28(self):
        raw_df = _build_raw_df(n_releases=30, country="US")
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        params = CpiSurpriseInput(country="US", lookback_releases=12)
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_cpi_surprise(engine=mock_engine, params=params)
        note = out["methodology_note"]
        assert "ADR 0004" in note
        assert "ADR 0008" in note
        assert "TD #28" in note or "ECO_RELEASE_DT_LIST" in note
        assert "P12" in note
        assert "actual − consensus_median" in note or "actual − consensus_median" in note
        # Revisions disclosure
        assert "revis" in note.lower() or "revised_prior" in note


# ===========================================================================
# 10. Bespoke vs canonical TimeSeries parity (cannot drift)
# ===========================================================================

class TestSeriesParity:
    def test_bespoke_and_canonical_surprise_match_row_for_row(self):
        raw_df = _build_raw_df(n_releases=40, country="US")
        params = CpiSurpriseInput(country="US", lookback_releases=20)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_cpi_surprise(engine=mock_engine, params=params)
        for i, (bespoke, canonical) in enumerate(
            zip(out["time_series"], out["time_series_surprise"]["rows"])
        ):
            assert bespoke["date"] == canonical["date"], f"row {i}"
            assert bespoke["surprise_pct"] == canonical["value"], f"row {i}"

    def test_bespoke_and_canonical_zscore_match_row_for_row(self):
        raw_df = _build_raw_df(n_releases=40, country="US")
        params = CpiSurpriseInput(country="US", lookback_releases=20)
        mock_engine = MagicMock(name="engine")
        target = (
            "rates_agent.inflation_swaps.tools.cpi_surprise."
            "compute.fetch_economic_release_surprises"
        )
        with patch(target, return_value=raw_df), patch(
            "rates_agent.inflation_swaps.tools.cpi_surprise.compute.date",
            _FrozenDate,
        ):
            out = calculate_cpi_surprise(engine=mock_engine, params=params)
        for i, (bespoke, canonical) in enumerate(
            zip(out["time_series"], out["time_series_zscore"]["rows"])
        ):
            assert bespoke["date"] == canonical["date"], f"row {i}"
            assert bespoke["z_score"] == canonical["value"], f"row {i}"

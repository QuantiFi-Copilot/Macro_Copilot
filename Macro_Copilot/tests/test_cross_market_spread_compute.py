"""
test_cross_market_spread_compute.py — Unit tests for the cross_market_spread migration

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z_score_window_days,
     ddof, daily/weekly/monthly_change_offset_rows, ffill_limit_days,
     bps_round_decimals, z_score_round_decimals).
  4. Honest placeholder for trailing_range_window_days: setting it to
     anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML.
  6. Defensive buffer sizing: max(z_window, trailing_window).
  7. bps_round_decimals reaches daily_change_bps via the new
     period_changes(decimals=...) plumbing.
  8. z_score_round_decimals reaches the OUTPUT boundary (current_z_score
     and time_series[].z_score), not just the rolling_zscore call.
  9. Three import paths still resolve to the same Pydantic class.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.cross_market_spread import (
    CONFIG_PATH,
    calculate_cross_market_spread,
    CrossMarketSpreadInput,
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


def _synthetic_raw_df(
    *,
    days: int = 600,
    frozen_today: date = date(2026, 4, 30),
    cf1_drift: float = -0.30,
    cf2_drift: float = +0.10,
    cf1_base: float = 4.50,
    cf2_base: float = 4.20,
) -> pd.DataFrame:
    """Build a 2-curve long-format DataFrame matching the shape
    fetch_cross_market_pair returns.  Two curve_family values
    (IT_BTP, DE_BUND), each with its own drift so the spread varies
    across the window."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    rows = []
    base = {"IT_BTP": cf1_base, "DE_BUND": cf2_base}
    drifts = {"IT_BTP": cf1_drift, "DE_BUND": cf2_drift}
    for cf, base_v in base.items():
        series = np.linspace(base_v, base_v + drifts[cf], n)
        for d, v in zip(bdays, series):
            rows.append({"trade_date": d.date(), "curve_family": cf, "field_value": v})
    return pd.DataFrame(rows)


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _custom_config(**overrides) -> ToolConfig:
    """Build a ToolConfig from defaults + any overrides.  Defaults
    mirror config.yaml exactly so single-knob tests measure a true
    single-variable change."""
    defaults = {
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
        "default_field_name": "YLD_YTM_MID",
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(name="t", domain="d", description="x"),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


def _run(params, raw_df, config=None):
    with patch(
        "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.fetch_cross_market_pair",
        return_value=raw_df,
    ), patch(
        "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.date",
        _FrozenDate,
    ):
        return calculate_cross_market_spread(engine=None, params=params, config=config)


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_cross_market_spread_tool"
        assert cfg.tool.domain == "sovereign_bonds"

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
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_match_legacy(self):
        """The bundled defaults reproduce the legacy hardcoded values
        bit-for-bit so the migration is a pure refactor."""
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
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 4
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        # The "252d clipping when lookback_days < 252" follow-up
        assert "clipping" in joined or "lookback_days" in joined
        # The sovereign-vs-OIS anchoring divergence (Codex P2)
        assert "anchoring" in joined.lower() or "OIS" in joined
        # The spread direction convention deferral
        assert "direction" in joined.lower() or "cf1" in joined.lower()


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out = _run(params, raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        # Required current_metrics fields present
        for k in (
            "as_of_date", "curve_family_1", "curve_family_2", "tenor",
            "spread_label", "current_spread_bps",
            "daily_change_bps", "weekly_change_bps", "monthly_change_bps",
            "current_z_score", "rolling_window_days",
            "high_252d_bps", "low_252d_bps", "percentile_252d",
            "curve_family_1_yield", "curve_family_2_yield",
        ):
            assert k in cm, f"missing {k}"

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_bps" not in cm
        assert "trailing_window_days" not in cm

        # Spread label format
        assert cm["spread_label"] == "IT_BTP-DE_BUND 10Y"
        assert cm["rolling_window_days"] == 252

        # time_series exists and rows have the right shape
        ts = out["time_series"]
        assert len(ts) > 0
        assert set(ts[0].keys()) == {"date", "spread_bps", "z_score"}

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out_auto = _run(params, raw_df, config=None)
        out_explicit = _run(params, raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_window_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out_default = _run(params, raw_df, _custom_config())
        out_short = _run(params, raw_df, _custom_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_short["current_metrics"]["current_z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out_sample = _run(params, raw_df, _custom_config(z_score_ddof=1))
        out_pop = _run(params, raw_df, _custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        """Bumping the daily_change offset to 5 trading days produces
        a different daily_change_bps value than the default (2 = 1
        trading day back)."""
        raw_df = _synthetic_raw_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out_default = _run(params, raw_df, _custom_config())
        out_wider = _run(params, raw_df, _custom_config(daily_change_offset_rows=5))
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_pivot(self):
        from shared.analytics.spreads import pivot_and_align_tenors as real_pivot

        raw_df = _synthetic_raw_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        with patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            calculate_cross_market_spread(
                engine=None, params=params,
                config=_custom_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2

    def test_bps_round_decimals_override_changes_precision(self):
        """Cranking bps_round_decimals from 2 to 4 should reveal sub-2-
        decimal precision on the time_series spread_bps for at least
        one row on a synthetic series with non-round drifts."""
        raw_df = _synthetic_raw_df(cf1_drift=-0.32193, cf2_drift=+0.10987)
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out_lo = _run(params, raw_df, _custom_config(bps_round_decimals=2))
        out_hi = _run(params, raw_df, _custom_config(bps_round_decimals=4))

        v_lo = out_lo["current_metrics"]["current_spread_bps"]
        v_hi = out_hi["current_metrics"]["current_spread_bps"]
        # 2-decimal rounding equals 4-decimal rounding rounded back to 2
        assert round(v_hi, 2) == v_lo

        # Some time_series row reveals sub-2-decimal precision at decimals=4
        ts_lo = out_lo["time_series"]
        ts_hi = out_hi["time_series"]
        differs = any(
            round(hi["spread_bps"], 2) == lo["spread_bps"]
            and hi["spread_bps"] != lo["spread_bps"]
            for hi, lo in zip(ts_hi, ts_lo)
        )
        assert differs, (
            "no time_series row exhibited sub-2-decimal precision at "
            "decimals=4 — bps_round_decimals override may not be reaching "
            "the .round() call"
        )


# ===========================================================================
# 4. bps_round_decimals reaches daily/weekly/monthly via period_changes
#    (the new period_changes(decimals=...) plumbing — without it the
#    delta_bps default of 2 would silently shadow any YAML override)
# ===========================================================================

class TestBpsRoundingReachesPeriodChanges:
    def test_period_changes_called_with_decimals(self):
        from shared.analytics import levels as levels_mod

        raw_df = _synthetic_raw_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        with patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.period_changes",
            wraps=levels_mod.period_changes,
        ) as spy:
            calculate_cross_market_spread(
                engine=None, params=params,
                config=_custom_config(bps_round_decimals=4),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["decimals"] == 4
        assert spy.call_args.kwargs["already_bps"] is True


# ===========================================================================
# 5. z_score_round_decimals reaches the OUTPUT boundary
#    (the same P2 fix Codex caught on butterfly — applied day one this time)
# ===========================================================================

class TestZScoreRoundDecimalsReachesOutput:
    def test_current_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(cf1_drift=-0.32193, cf2_drift=+0.10987)
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out_hi = _run(params, raw_df, _custom_config(z_score_round_decimals=6))
        out_lo = _run(params, raw_df, _custom_config(z_score_round_decimals=4))

        z_hi = out_hi["current_metrics"]["current_z_score"]
        z_lo = out_lo["current_metrics"]["current_z_score"]
        assert z_hi is not None and z_lo is not None
        # Contract: at decimals=6, value rounded back to 4 == value at decimals=4
        assert round(z_hi, 4) == z_lo

    def test_time_series_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(cf1_drift=-0.32193, cf2_drift=+0.10987)
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out_hi = _run(params, raw_df, _custom_config(z_score_round_decimals=6))
        out_lo = _run(params, raw_df, _custom_config(z_score_round_decimals=4))

        differs = any(
            hi["z_score"] is not None and lo["z_score"] is not None
            and round(hi["z_score"], 4) == lo["z_score"]
            and hi["z_score"] != lo["z_score"]
            for hi, lo in zip(out_hi["time_series"], out_lo["time_series"])
        )
        assert differs, (
            "no time_series row exhibited sub-4-decimal precision at "
            "z_score_round_decimals=6 — safe_float() at the output "
            "boundary may be ignoring the YAML override"
        )


# ===========================================================================
# 6. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_cross_market_spread(
                engine=None, params=params,
                config=_custom_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        raw_df = _synthetic_raw_df()
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        out = _run(params, raw_df, _custom_config(trailing_range_window_days=252))
        assert "error" not in out


# ===========================================================================
# 7. Schema-layer field_name behaviour
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND", tenor="10Y",
        )
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_field_name"
        )

    def test_explicit_field_name_passes_through(self):
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND", tenor="10Y",
            field_name="PX_LAST",
        )
        assert params.field_name == "PX_LAST"

    def test_curves_must_differ(self):
        with pytest.raises(Exception) as exc_info:
            CrossMarketSpreadInput(
                curve_family_1="IT_BTP", curve_family_2="IT_BTP", tenor="10Y",
            )
        assert "different" in str(exc_info.value).lower() or "must" in str(exc_info.value).lower()


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches fetch."""

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ):
            calculate_cross_market_spread(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND", tenor="10Y",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="YLD_YTM_MID"),
        )
        assert passed == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND", tenor="10Y",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_explicit_field_name_overrides_yaml(self):
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND", tenor="10Y",
            field_name="YLD_BID",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="PX_LAST"),
        )
        assert passed == "YLD_BID"


# ===========================================================================
# 8. Buffer sizing — defensive max(z_window, trailing_window)
# ===========================================================================

class TestBufferSizing:
    """When the YAML decouples z_window from trailing_window in a
    future migration, the fetch buffer must already be sized off the
    LARGER of the two so neither stat starves."""

    def _capture_start_date(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.fetch_cross_market_pair",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.sovereign_bonds.tools.cross_market_spread.compute.date",
            _FrozenDate,
        ):
            calculate_cross_market_spread(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["start_date"]

    def test_buffer_uses_larger_of_z_window_and_trailing_window(self):
        params = CrossMarketSpreadInput(
            curve_family_1="IT_BTP", curve_family_2="DE_BUND",
            tenor="10Y", lookback_days=365,
        )
        start_default = self._capture_start_date(params, _custom_config())
        start_smaller_z = self._capture_start_date(
            params, _custom_config(z_score_window_days=120),
        )
        # Same buffer ⇒ same start_date when only z_window shrinks
        # (trailing=252 still drives the max).
        assert start_default == start_smaller_z


# ===========================================================================
# 9. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_cross_market_spread_via_package_init(self):
        from rates_agent.sovereign_bonds.tools.cross_market_spread import (
            calculate_cross_market_spread as via_package,
        )
        from rates_agent.sovereign_bonds.tools.cross_market_spread.compute import (
            calculate_cross_market_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.sovereign_bonds.tools.cross_market_spread import (
            CrossMarketSpreadInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.cross_market_spread.schemas import (
            CrossMarketSpreadInput as via_schemas,
        )
        from rates_agent.sovereign_bonds.tools.schemas import (
            CrossMarketSpreadInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_legacy_shim_path_is_deleted(self):
        with pytest.raises(ModuleNotFoundError):
            import rates_agent.sovereign_bonds.tools.schemas.cross_market  # noqa: F401


# ===========================================================================
# Canonical TimeSeries output (legacy-TimeSeries cleanup)
# ===========================================================================


class TestCanonicalTimeSeries:
    """Pin the legacy-TimeSeries cleanup contract: cross_market_spread
    emits TWO canonical ``TimeSeries`` fields (``time_series_spread``
    in BPS, ``time_series_zscore`` in Z_SCORE) alongside its
    wire-frozen bespoke ``time_series:
    List[CrossMarketSpreadTimeSeriesRow]`` array."""

    def _params(self):
        return CrossMarketSpreadInput(
            curve_family_1="IT_BTP",
            curve_family_2="DE_BUND",
            tenor="10Y",
            lookback_days=365,
            field_name="YLD_YTM_MID",
        )

    def test_canonical_time_series_legacy_field_NOT_present(self):
        out = _run(self._params(), _synthetic_raw_df())
        assert "canonical_time_series" not in out

    def test_time_series_spread_field_present(self):
        out = _run(self._params(), _synthetic_raw_df())
        assert "time_series_spread" in out
        assert isinstance(out["time_series_spread"], dict)

    def test_time_series_zscore_field_present(self):
        out = _run(self._params(), _synthetic_raw_df())
        assert "time_series_zscore" in out
        assert isinstance(out["time_series_zscore"], dict)

    def test_spread_series_uses_BPS(self):
        out = _run(self._params(), _synthetic_raw_df())
        assert out["time_series_spread"]["units"] == "bps"

    def test_zscore_series_uses_Z_SCORE(self):
        out = _run(self._params(), _synthetic_raw_df())
        assert out["time_series_zscore"]["units"] == "z_score"

    def test_spread_series_name_follows_convention(self):
        out = _run(self._params(), _synthetic_raw_df())
        assert (
            out["time_series_spread"]["series_name"]
            == "it_btp_de_bund_10y_spread"
        )

    def test_zscore_series_name_follows_convention(self):
        out = _run(self._params(), _synthetic_raw_df())
        assert (
            out["time_series_zscore"]["series_name"]
            == "it_btp_de_bund_10y_zscore"
        )

    def test_both_series_length_equals_bespoke_length(self):
        out = _run(self._params(), _synthetic_raw_df())
        bespoke = out["time_series"]
        assert len(out["time_series_spread"]["rows"]) == len(bespoke)
        assert len(out["time_series_zscore"]["rows"]) == len(bespoke)

    def test_spread_values_match_bespoke_pointwise(self):
        out = _run(self._params(), _synthetic_raw_df())
        canonical = out["time_series_spread"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(canonical["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["spread_bps"], (
                f"row {i}: value mismatch"
            )

    def test_zscore_values_match_bespoke_pointwise(self):
        """Codex P1 follow-up: z_score historical signal must also be
        canonicalized."""
        out = _run(self._params(), _synthetic_raw_df())
        canonical = out["time_series_zscore"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(canonical["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["z_score"], (
                f"row {i}: value mismatch"
            )

    def test_both_canonical_series_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        out = _run(self._params(), _synthetic_raw_df())
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])

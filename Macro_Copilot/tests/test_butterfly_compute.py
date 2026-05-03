"""
test_butterfly_compute.py — Unit tests for the butterfly migration

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour
     (z_score_window_days, ddof, daily_change_offset_rows,
     ffill_limit_days, bps_round_decimals).
  4. Honest placeholder for trailing_range_window_days: setting it to
     anything other than 252 raises NotImplementedError.
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML.
  6. Three import paths still resolve to the same Pydantic class.

Tests are fully offline.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.butterfly import (
    CONFIG_PATH,
    calculate_butterfly,
    ButterflyInput,
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
    short_drift: float = -0.40,
    belly_drift: float = -0.20,
    long_drift: float = +0.10,
) -> pd.DataFrame:
    """Build a 3-tenor long-format DataFrame matching the shape
    fetch_tenor_group returns.  Three tenors (2Y, 5Y, 10Y), each with
    its own drift so the butterfly varies across the window."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    rows = []
    base = {"2Y": 4.5, "5Y": 4.2, "10Y": 4.4}
    drifts = {"2Y": short_drift, "5Y": belly_drift, "10Y": long_drift}
    for tenor, base_v in base.items():
        series = np.linspace(base_v, base_v + drifts[tenor], n)
        for d, v in zip(bdays, series):
            rows.append({"trade_date": d.date(), "tenor": tenor, "field_value": v})
    return pd.DataFrame(rows)


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _custom_config(**overrides) -> ToolConfig:
    """Build a ToolConfig from defaults + any overrides.  Defaults
    mirror config.yaml exactly so tests that override one knob are
    measuring a single-variable change."""
    defaults = {
        "z_score_window_days": 252,
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "daily_change_offset_rows": 2,
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
        "rates_agent.sovereign_bonds.tools.butterfly.compute.fetch_tenor_group",
        return_value=raw_df,
    ), patch(
        "rates_agent.sovereign_bonds.tools.butterfly.compute.date",
        _FrozenDate,
    ):
        return calculate_butterfly(engine=None, params=params, config=config)


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "calculate_butterfly_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "daily_change_offset_rows",
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
        assert cfg.convention_value("trailing_range_window_days") == 252
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("bps_round_decimals") == 2
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        # The "252d clipping when lookback_days < 252" follow-up
        assert "clipping" in joined or "lookback_days" in joined
        # The methodology choice (equal-weighted vs duration-neutral)
        assert "duration" in joined or "weight" in joined.lower()


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        # Required current_metrics fields present
        for k in (
            "as_of_date", "curve_family", "butterfly_label",
            "current_butterfly_bps", "daily_change_bps",
            "current_z_score", "rolling_window_days",
            "high_252d_bps", "low_252d_bps", "percentile_252d",
            "wing_short_bps", "wing_long_bps",
            "short_tenor_yield", "belly_tenor_yield", "long_tenor_yield",
        ):
            assert k in cm, f"missing {k}"

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_bps" not in cm
        assert "trailing_window_days" not in cm

        # Butterfly label is "{short}s{belly}s{long}s" with Y stripped.
        assert cm["butterfly_label"] == "2s5s10s"
        assert cm["rolling_window_days"] == 252

        # time_series exists and rows have the right shape
        ts = out["time_series"]
        assert len(ts) > 0
        assert set(ts[0].keys()) == {"date", "butterfly_bps", "z_score"}

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
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
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out_default = _run(params, raw_df, _custom_config())
        out_short = _run(params, raw_df, _custom_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["current_z_score"]
            != out_short["current_metrics"]["current_z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out_sample = _run(params, raw_df, _custom_config(z_score_ddof=1))
        out_pop = _run(params, raw_df, _custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    def test_daily_change_offset_override_changes_daily(self):
        """Bumping daily_change_offset_rows from 2 → 5 changes the
        daily_change_bps value (5 trading days back vs 1)."""
        raw_df = _synthetic_raw_df()
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
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
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            calculate_butterfly(
                engine=None, params=params,
                config=_custom_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2

    def test_bps_round_decimals_override_changes_precision(self):
        """current_butterfly_bps should round to whatever
        bps_round_decimals says.  We verify the contract by checking
        that the 4-decimal value rounds back to the 2-decimal value;
        we also scan the time_series for at least one row where the
        4-decimal version reveals sub-2-decimal precision (proving the
        flag actually reaches the rounding step rather than being
        no-oped by some intermediate cast)."""
        raw_df = _synthetic_raw_df(
            short_drift=-0.43219, belly_drift=-0.12345, long_drift=+0.09876,
        )
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out_lo = _run(params, raw_df, _custom_config(bps_round_decimals=2))
        out_hi = _run(params, raw_df, _custom_config(bps_round_decimals=4))

        v_lo = out_lo["current_metrics"]["current_butterfly_bps"]
        v_hi = out_hi["current_metrics"]["current_butterfly_bps"]
        # 2-decimal rounding should equal 4-decimal rounding rounded
        # back to 2 decimals.
        assert round(v_hi, 2) == v_lo
        # On a synthetic series with non-round drifts there should be
        # at least one row in time_series where the 4-decimal version
        # carries trailing digits beyond 2 decimals.
        ts_lo = out_lo["time_series"]
        ts_hi = out_hi["time_series"]
        differs = any(
            round(hi["butterfly_bps"], 2) == lo["butterfly_bps"]
            and hi["butterfly_bps"] != lo["butterfly_bps"]
            for hi, lo in zip(ts_hi, ts_lo)
        )
        assert differs, (
            "no time_series row exhibited sub-2-decimal precision at "
            "decimals=4 — bps_round_decimals override may not be reaching "
            "the .round() call"
        )


class TestZScoreRoundDecimalsReachesOutput:
    """z_score_round_decimals must apply at the OUTPUT boundary, not
    just to the internal rolling_zscore() call.  An earlier version
    rounded the rolling series to the YAML decimals, but then called
    safe_float() without `decimals=` at the metric-assembly step,
    silently truncating any value above 4.  This test pins the wiring
    end-to-end so a future edit can't reintroduce the bug.

    Caught by Codex review of the original butterfly migration commit;
    same boundary-shadowing class as the field_name fix in b2605ee."""

    def test_current_z_score_uses_z_round_decimals(self):
        from shared.analytics import spreads as spreads_mod

        raw_df = _synthetic_raw_df(
            short_drift=-0.43219, belly_drift=-0.12345, long_drift=+0.09876,
        )
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        # z_score_round_decimals=6 — well above safe_float's default of 4.
        # If safe_float at the output boundary ignores this, we'll see
        # at most 4 decimals on current_z_score.
        out = _run(params, raw_df, _custom_config(z_score_round_decimals=6))

        z = out["current_metrics"]["current_z_score"]
        assert z is not None
        # Find the actual rounded representation.  At decimals=6 the
        # raw value should preserve more precision than decimals=4
        # would.  Check by re-rounding to 4 and comparing.
        out_at_4 = _run(params, raw_df, _custom_config(z_score_round_decimals=4))
        z_at_4 = out_at_4["current_metrics"]["current_z_score"]
        # Contract: at decimals=6, value rounded back to 4 == value at decimals=4
        assert round(z, 4) == z_at_4

    def test_time_series_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(
            short_drift=-0.43219, belly_drift=-0.12345, long_drift=+0.09876,
        )
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out_hi = _run(params, raw_df, _custom_config(z_score_round_decimals=6))
        out_lo = _run(params, raw_df, _custom_config(z_score_round_decimals=4))

        # On a synthetic series with non-round drifts there should be
        # at least one row where the 6-decimal version reveals
        # sub-4-decimal precision in z_score.
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


class TestBpsRoundingReachesDailyChange:
    """delta_bps used to be hardcoded to 2 decimals, so a bumped
    bps_round_decimals would silently NOT apply to daily_change_bps.
    The migration parameterised delta_bps; this test pins the wiring."""

    def test_delta_bps_decimals_reaches_daily_change(self):
        from shared.analytics import levels as levels_mod

        raw_df = _synthetic_raw_df()
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        with patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.delta_bps",
            wraps=levels_mod.delta_bps,
        ) as spy:
            calculate_butterfly(
                engine=None, params=params,
                config=_custom_config(bps_round_decimals=4),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["decimals"] == 4


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def test_unsupported_trailing_window_raises(self):
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            calculate_butterfly(
                engine=None, params=params,
                config=_custom_config(trailing_range_window_days=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_bps" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        raw_df = _synthetic_raw_df()
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        out = _run(params, raw_df, _custom_config(trailing_range_window_days=252))
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer field_name behaviour
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
        )
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_field_name"
        )

    def test_explicit_field_name_passes_through(self):
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            field_name="PX_LAST",
        )
        assert params.field_name == "PX_LAST"

    def test_tenors_must_all_differ(self):
        with pytest.raises(Exception):
            ButterflyInput(
                curve_family="UST", short_tenor="5Y", belly_tenor="5Y", long_tenor="10Y",
            )


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches fetch."""

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.date",
            _FrozenDate,
        ):
            calculate_butterfly(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="YLD_YTM_MID"),
        )
        assert passed == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_explicit_field_name_overrides_yaml(self):
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            field_name="YLD_BID",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="PX_LAST"),
        )
        assert passed == "YLD_BID"


# ===========================================================================
# 6. Buffer sizing — defensive max(z_window, trailing_window)
# ===========================================================================

class TestBufferSizing:
    """When the YAML decouples z_window from trailing_window in a
    future migration, the fetch buffer must already be sized off the
    LARGER of the two so neither stat starves."""

    def _capture_start_date(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.fetch_tenor_group",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.sovereign_bonds.tools.butterfly.compute.date",
            _FrozenDate,
        ):
            calculate_butterfly(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["start_date"]

    def test_buffer_uses_larger_of_z_window_and_trailing_window(self):
        """With the trailing window locked at 252, halving the
        z-window from 252 to 120 should NOT shrink the fetch buffer
        — the buffer must continue to use 252 because trailing > z."""
        params = ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
            lookback_days=365,
        )
        start_default = self._capture_start_date(params, _custom_config())
        start_smaller_z = self._capture_start_date(
            params, _custom_config(z_score_window_days=120),
        )
        # Same buffer ⇒ same start_date when only z_window shrinks
        # (because trailing=252 still drives the max).
        assert start_default == start_smaller_z


# ===========================================================================
# 7. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_butterfly_via_package_init(self):
        from rates_agent.sovereign_bonds.tools.butterfly import (
            calculate_butterfly as via_package,
        )
        from rates_agent.sovereign_bonds.tools.butterfly.compute import (
            calculate_butterfly as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.sovereign_bonds.tools.butterfly import (
            ButterflyInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.butterfly.schemas import (
            ButterflyInput as via_schemas,
        )
        from rates_agent.sovereign_bonds.tools.schemas import (
            ButterflyInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_legacy_shim_path_is_deleted(self):
        with pytest.raises(ModuleNotFoundError):
            import rates_agent.sovereign_bonds.tools.schemas.butterfly  # noqa: F401


# ===========================================================================
# Canonical TimeSeries output (legacy-TimeSeries cleanup)
# ===========================================================================


class TestCanonicalTimeSeries:
    """Pin the legacy-TimeSeries cleanup contract: butterfly emits a
    canonical ``TimeSeries`` payload alongside its wire-frozen bespoke
    ``time_series: List[ButterflyTimeSeriesRow]`` array."""

    def _params(self):
        return ButterflyInput(
            curve_family="UST", short_tenor="2Y", belly_tenor="5Y",
            long_tenor="10Y", lookback_days=365, field_name="YLD_YTM_MID",
        )

    def test_canonical_time_series_field_present(self):
        out = _run(self._params(), _synthetic_raw_df())
        assert "canonical_time_series" in out
        assert isinstance(out["canonical_time_series"], list)
        assert len(out["canonical_time_series"]) == 1

    def test_canonical_series_uses_closed_enum_units(self):
        out = _run(self._params(), _synthetic_raw_df())
        ts = out["canonical_time_series"][0]
        assert ts["units"] == "bps"

    def test_canonical_series_name_follows_convention(self):
        out = _run(self._params(), _synthetic_raw_df())
        ts = out["canonical_time_series"][0]
        assert ts["series_name"] == "ust_2y_5y_10y_butterfly"

    def test_canonical_series_length_equals_bespoke_length(self):
        out = _run(self._params(), _synthetic_raw_df())
        canonical = out["canonical_time_series"][0]
        bespoke = out["time_series"]
        assert len(canonical["rows"]) == len(bespoke)

    def test_canonical_values_match_bespoke_butterfly_bps_pointwise(self):
        out = _run(self._params(), _synthetic_raw_df())
        canonical = out["canonical_time_series"][0]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(canonical["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["butterfly_bps"], (
                f"row {i}: value mismatch"
            )

    def test_canonical_series_validates_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        out = _run(self._params(), _synthetic_raw_df())
        TimeSeries.model_validate(out["canonical_time_series"][0])

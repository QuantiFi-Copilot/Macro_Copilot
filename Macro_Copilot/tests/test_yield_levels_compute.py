"""
test_yield_levels_compute.py — Unit tests for the yield_levels migration

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly.
  2. compute() runs end-to-end against synthetic input with the
     bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window,
     ddof, period offsets, ffill, default_field_name).
  4. The honest placeholder for trailing_range_window_days: setting
     it to anything other than 252 raises NotImplementedError.
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

from rates_agent.sovereign_bonds.tools.yield_levels import (
    CONFIG_PATH,
    get_yield_levels,
    YieldLevelInput,
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
    drift_pct: float = -0.50,
    days: int = 400,
    frozen_today: date = date(2026, 4, 30),
) -> pd.DataFrame:
    """Build a single-tenor long-format DataFrame matching the shape
    fetch_single_tenor returns.  Linspace from 4.5 to 4.5+drift_pct
    over `days` business days ending at frozen_today."""
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = np.linspace(4.5, 4.5 + drift_pct, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
    })


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "get_yield_levels_tool"
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
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_match_legacy(self):
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
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 2
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "trailing_range_window_days" in joined
        assert "observation_count" in joined  # the OIS-anchoring follow-up


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, raw_df, config=None):
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            return get_yield_levels(engine=None, params=params, config=config)

    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]

        # Required fields present
        for k in (
            "as_of_date", "curve_family", "tenor", "current_yield_pct",
            "daily_change_bps", "weekly_change_bps", "monthly_change_bps",
            "z_score", "high_252d_pct", "low_252d_pct", "percentile_252d",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        # Wire-frozen field names — explicit check that the rename
        # plan was NOT silently applied.
        assert "high_window_pct" not in cm
        assert "trailing_window_days" not in cm

        # current_yield_pct should be the latest value (rounded).
        assert isinstance(cm["current_yield_pct"], float)

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out_auto = self._run(params, raw_df, config=None)
        out_explicit = self._run(params, raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def _custom_config(self, **overrides) -> ToolConfig:
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
            "default_field_name": "YLD_YTM_MID",
            "yield_round_decimals": 4,
            "z_score_round_decimals": 4,
            "high_low_round_decimals": 4,
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

    def _run(self, params, raw_df, config):
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            return get_yield_levels(engine=None, params=params, config=config)

    def test_z_score_window_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out_default = self._run(params, raw_df, self._custom_config())
        out_short = self._run(params, raw_df, self._custom_config(z_score_window_days=120))
        assert (
            out_default["current_metrics"]["z_score"]
            != out_short["current_metrics"]["z_score"]
        )

    def test_z_score_ddof_override_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out_sample = self._run(params, raw_df, self._custom_config(z_score_ddof=1))
        out_pop = self._run(params, raw_df, self._custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["z_score"]
            != out_pop["current_metrics"]["z_score"]
        )

    def test_period_offsets_override_changes_changes(self):
        """Bumping the daily_change offset to 5 trading days produces
        a different daily_change_bps value than the default (2 = 1
        trading day back)."""
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out_default = self._run(params, raw_df, self._custom_config())
        out_wider = self._run(params, raw_df, self._custom_config(daily_change_offset_rows=5))
        assert (
            out_default["current_metrics"]["daily_change_bps"]
            != out_wider["current_metrics"]["daily_change_bps"]
        )

    def test_ffill_limit_passed_to_clean(self):
        from shared.analytics.levels import clean_single_series as real_clean

        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.clean_single_series",
            wraps=real_clean,
        ) as spy:
            get_yield_levels(
                engine=None, params=params,
                config=self._custom_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2


# ===========================================================================
# 4. Honest-placeholder guard on trailing_range_window_days
# ===========================================================================

class TestTrailingWindowGuard:
    def _custom_config(self, trailing_window: int) -> ToolConfig:
        defaults = {
            "z_score_window_days": 252,
            "z_score_min_periods": 60,
            "z_score_ddof": 1,
            "z_score_buffer_multiplier": 1.5,
            "daily_change_offset_rows": 2,
            "weekly_change_offset_rows": 6,
            "monthly_change_offset_rows": 22,
            "trailing_range_window_days": trailing_window,
            "ffill_limit_days": 5,
            "default_field_name": "YLD_YTM_MID",
            "yield_round_decimals": 4,
            "z_score_round_decimals": 4,
            "high_low_round_decimals": 4,
        }
        return ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                k: Convention(value=v, source="test", rationale="test")
                for k, v in defaults.items()
            },
        )

    def test_unsupported_trailing_window_raises(self):
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        with pytest.raises(NotImplementedError) as exc_info:
            get_yield_levels(
                engine=None, params=params,
                config=self._custom_config(trailing_window=180),
            )
        msg = str(exc_info.value)
        assert "180" in msg
        assert "high_252d_pct" in msg
        assert "planned_extensions" in msg

    def test_supported_default_does_not_raise(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            out = get_yield_levels(
                engine=None, params=params,
                config=self._custom_config(trailing_window=252),
            )
        assert "error" not in out


# ===========================================================================
# 5. Schema-layer field_name behaviour
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = YieldLevelInput(curve_family="UST", tenor="10Y")
        assert params.field_name is None, (
            "schema default must be None so compute() can fall through "
            "to the YAML's default_field_name"
        )

    def test_explicit_field_name_passes_through(self):
        params = YieldLevelInput(curve_family="UST", tenor="10Y", field_name="PX_LAST")
        assert params.field_name == "PX_LAST"


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_field_name reaches fetch."""

    def _custom_config(self, default_field_name: str) -> ToolConfig:
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
            "default_field_name": default_field_name,
            "yield_round_decimals": 4,
            "z_score_round_decimals": 4,
            "high_low_round_decimals": 4,
        }
        return ToolConfig(
            tool=ToolMeta(name="t", domain="d", description="x"),
            methodology=MethodologyMeta(what_it_does="x"),
            conventions={
                k: Convention(value=v, source="test", rationale="test")
                for k, v in defaults.items()
            },
        )

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            get_yield_levels(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = YieldLevelInput(curve_family="UST", tenor="10Y")  # no field_name
        passed = self._capture_field_name(params, self._custom_config("YLD_YTM_MID"))
        assert passed == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = YieldLevelInput(curve_family="UST", tenor="10Y")
        passed = self._capture_field_name(params, self._custom_config("PX_LAST"))
        assert passed == "PX_LAST"

    def test_explicit_field_name_overrides_yaml(self):
        params = YieldLevelInput(
            curve_family="UST", tenor="10Y", field_name="YLD_BID",
        )
        passed = self._capture_field_name(params, self._custom_config("PX_LAST"))
        assert passed == "YLD_BID"


# ===========================================================================
# 6. Import path backward-compat
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_get_yield_levels_via_package_init(self):
        from rates_agent.sovereign_bonds.tools.yield_levels import (
            get_yield_levels as via_package,
        )
        from rates_agent.sovereign_bonds.tools.yield_levels.compute import (
            get_yield_levels as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.sovereign_bonds.tools.yield_levels import (
            YieldLevelInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.yield_levels.schemas import (
            YieldLevelInput as via_schemas,
        )
        from rates_agent.sovereign_bonds.tools.schemas import (
            YieldLevelInput as via_hub,
        )
        assert via_package is via_schemas is via_hub

    def test_legacy_shim_path_is_deleted(self):
        with pytest.raises(ModuleNotFoundError):
            import rates_agent.sovereign_bonds.tools.schemas.yield_level  # noqa: F401


# ===========================================================================
# 8. Canonical TimeSeries output (legacy-TimeSeries cleanup)
# ===========================================================================


class TestCanonicalTimeSeries:
    """Pin the legacy-TimeSeries cleanup contract: yield_levels emits a
    canonical ``TimeSeries`` payload alongside its wire-frozen
    ``current_metrics`` snapshot.

    Field name is ``time_series`` (singular ``TimeSeries`` value),
    matching the v6 sovereign primitive convention used by
    ``zscore_custom``.  Each row is rounded with the YAML-controlled
    ``yield_round_decimals`` convention so the snapshot's
    ``current_yield_pct`` equals ``time_series.rows[-1].value``
    STRICTLY (not just within tolerance).
    """

    def _run(self, params, raw_df, *, config=None):
        with patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.yield_levels.compute.date",
            _FrozenDate,
        ):
            return get_yield_levels(engine=None, params=params, config=config)

    def test_time_series_field_present(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)
        assert "time_series" in out
        # Singular TimeSeries object, not a list — matches v6 pattern.
        assert isinstance(out["time_series"], dict)

    def test_canonical_time_series_legacy_name_NOT_present(self):
        """The transitional ``canonical_time_series`` field (introduced
        in PR #58 and renamed by PR #59) MUST be gone.  Guards against
        a regression that re-introduces the name."""
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)
        assert "canonical_time_series" not in out

    def test_time_series_uses_closed_enum_units(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert ts["units"] == "percent"

    def test_time_series_name_follows_convention(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert ts["series_name"] == "ust_10y_yield"

    def test_time_series_length_matches_observation_count(self):
        """The canonical series covers the same display window the
        snapshot's observation_count was computed from — length must
        match exactly."""
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)
        ts = out["time_series"]
        assert len(ts["rows"]) == out["current_metrics"]["observation_count"]

    def test_time_series_last_value_matches_snapshot_STRICTLY(self):
        """Latest row in the canonical series MUST equal
        ``current_yield_pct`` STRICTLY (not just within tolerance) —
        both go through the same ``yield_round_decimals`` convention
        applied via ``compute_level_metrics`` and the canonical
        builder."""
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)
        ts = out["time_series"]
        last_row_value = ts["rows"][-1]["value"]
        snapshot_value = out["current_metrics"]["current_yield_pct"]
        # Strict equality — proves the YAML rounding convention is
        # threaded through both paths.
        assert last_row_value == snapshot_value

    def test_yaml_yield_round_decimals_change_propagates_to_time_series(self):
        """Tweaking the YAML's ``yield_round_decimals`` MUST change
        the precision of the canonical series.  Regression guard
        against the canonical builder hardcoding a default instead
        of reading the YAML."""
        from shared.config import (
            Convention, MethodologyMeta, ToolConfig, ToolMeta,
            load_tool_config,
        )
        bundled = load_tool_config(CONFIG_PATH)
        # Build a tweaked config with yield_round_decimals=2.
        tweaked_conventions = dict(bundled.conventions)
        tweaked_conventions["yield_round_decimals"] = Convention(
            value=2, source="test_override", rationale="test",
            valid_range=[0, 8],
        )
        tweaked = ToolConfig(
            tool=bundled.tool,
            conventions=tweaked_conventions,
            methodology=bundled.methodology,
        )

        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df, config=tweaked)
        ts = out["time_series"]
        # Every row's value must round to ≤ 2 decimals.
        for row in ts["rows"]:
            v = row["value"]
            if v is not None:
                # Round to 2; if the value was rounded in the builder
                # to 2 decimals already, this is a no-op.  If it was
                # rounded to a different precision, this assertion
                # fires.
                assert v == round(v, 2)

    def test_time_series_dates_chronological(self):
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)
        ts = out["time_series"]
        dates = [r["date"] for r in ts["rows"]]
        assert dates == sorted(dates)

    def test_time_series_validates_against_TimeSeries_schema(self):
        """The output dict must round-trip cleanly through the
        canonical ``shared.schemas.TimeSeries`` model — guards against
        the bespoke shape silently leaking back in."""
        from shared.schemas import TimeSeries
        raw_df = _synthetic_raw_df()
        params = YieldLevelInput(curve_family="UST", tenor="10Y", lookback_days=365)
        out = self._run(params, raw_df)
        TimeSeries.model_validate(out["time_series"])

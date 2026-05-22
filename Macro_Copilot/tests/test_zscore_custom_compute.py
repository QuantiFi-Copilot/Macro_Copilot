"""
test_zscore_custom_compute.py — Unit tests for the zscore_custom tool.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly + has
     the new ``category`` field correctly set.
  2. compute() runs end-to-end against synthetic input and returns a
     well-formed output.
  3. Central knob (``z_score_window_days``) overrides actually change
     the output; YAML-locked knobs cannot be overridden via input.
  4. Convention overrides (passing a custom ToolConfig) propagate as
     expected (z_score_min_periods, z_score_ddof,
     z_score_round_decimals, default_field_name, ffill_limit_days).
  5. Schema-layer behaviour: field_name defaults to None (sentinel),
     z_score_window_days is required + bounded.
  6. TimeSeries output shape matches the new shared contract.
  7. Boundary rounding: z_score_round_decimals reaches both
     current_z_score AND time_series rows (the P2-class fix).
  8. Three import paths still resolve to the same Pydantic class.

Tests are fully offline — fetch is mocked, today is frozen.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.zscore_custom import (
    CONFIG_PATH,
    ZscoreCustomInput,
    calculate_zscore_custom,
)
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)
from shared.schemas import TimeSeriesUnits


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
    drift_pct: float = -0.40,
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


def _custom_config(**overrides) -> ToolConfig:
    """Build a ToolConfig from defaults + any overrides.  Defaults
    mirror config.yaml exactly so tests that override one knob are
    measuring a single-variable change."""
    defaults = {
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "ffill_limit_days": 5,
        "yield_round_decimals": 4,
        "z_score_round_decimals": 4,
        "default_field_name": "YLD_YTM_MID",
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(
            name="t",
            domain="d",
            description="x",
            category="desk_invariant_primitive",
        ),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


def _run(params, raw_df, config=None):
    with patch(
        "rates_agent.sovereign_bonds.tools.zscore_custom.compute.fetch_single_tenor",
        return_value=raw_df,
    ), patch(
        "rates_agent.sovereign_bonds.tools.zscore_custom.compute.date",
        _FrozenDate,
    ):
        return calculate_zscore_custom(engine=None, params=params, config=config)


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "zscore_custom_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_category_is_desk_invariant_primitive(self):
        cfg = load_tool_config(CONFIG_PATH)
        # zscore_custom is a desk-invariant primitive: a trader hears
        # "z-score" and knows the shape; window length is calibration.
        assert cfg.tool.category == "desk_invariant_primitive"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "ffill_limit_days",
            "yield_round_decimals",
            "z_score_round_decimals",
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_no_z_score_window_days_in_yaml(self):
        """``z_score_window_days`` is the user's central knob and must
        NOT live in YAML — that would make it a convention rather than
        a per-request parameter."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "z_score_window_days" not in cfg.conventions

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("yield_round_decimals") == 4
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "cross-sectional" in joined.lower() or "robust" in joined.lower()


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=252, lookback_days=365,
        )
        out = _run(params, raw_df)

        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        for k in (
            "as_of_date", "curve_family", "tenor",
            "current_yield_pct", "current_z_score",
            "z_score_window_days_used",
            "z_score_min_periods_used",
            "z_score_ddof_used",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        assert cm["z_score_window_days_used"] == 252
        assert cm["z_score_min_periods_used"] == 60
        assert cm["z_score_ddof_used"] == 1
        assert cm["observation_count"] > 0

        # TimeSeries output shape
        ts = out["time_series"]
        assert ts["units"] == TimeSeriesUnits.Z_SCORE.value
        assert ts["series_name"].startswith("ust_10y_zscore_252d")
        assert isinstance(ts["rows"], list)
        assert len(ts["rows"]) > 0
        assert set(ts["rows"][0].keys()) == {"date", "value"}

    def test_explicit_config_matches_auto_loaded(self):
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=252, lookback_days=365,
        )
        out_auto = _run(params, raw_df, config=None)
        out_explicit = _run(params, raw_df, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit

    def test_time_series_zscore_alias_matches_time_series(self):
        """``ZscoreCustomOutput`` exposes both ``time_series`` (legacy
        V1 name) and ``time_series_zscore`` (canonical convention used
        by every other z-score-emitting primitive).  Both must carry
        identical payloads — the alias exists solely to close the
        substrate's naming-convention inconsistency that caused the
        workflow_router to bind ``time_series_zscore`` for
        zscore_custom and crash at the bridge lift step.
        """
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=252, lookback_days=365,
        )
        out = _run(params, raw_df)
        assert "error" not in out, out.get("error")
        # Both keys present.
        assert "time_series" in out
        assert "time_series_zscore" in out
        # Identical payload by construction.
        assert out["time_series"] == out["time_series_zscore"]
        # Units carry the canonical z-score tag on both.
        assert out["time_series"]["units"] == TimeSeriesUnits.Z_SCORE.value
        assert out["time_series_zscore"]["units"] == TimeSeriesUnits.Z_SCORE.value


# ===========================================================================
# 3. Central knob — z_score_window_days
# ===========================================================================

class TestCentralKnob:
    def test_different_windows_produce_different_z(self):
        """The whole point of the tool: changing z_score_window_days
        per request changes the z-score."""
        raw_df = _synthetic_raw_df()
        p_60 = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=60, lookback_days=180,
        )
        p_252 = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=252, lookback_days=180,
        )
        out_60 = _run(p_60, raw_df)
        out_252 = _run(p_252, raw_df)

        assert out_60["current_metrics"]["current_z_score"] != \
            out_252["current_metrics"]["current_z_score"]
        assert out_60["current_metrics"]["z_score_window_days_used"] == 60
        assert out_252["current_metrics"]["z_score_window_days_used"] == 252

    def test_window_propagates_to_series_name(self):
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="DE_BUND", tenor="5Y",
            z_score_window_days=126, lookback_days=180,
        )
        out = _run(params, raw_df)
        assert out["time_series"]["series_name"] == "de_bund_5y_zscore_126d"


# ===========================================================================
# 4. Convention overrides (YAML-locked methodology)
# ===========================================================================

class TestConventionOverrides:
    def test_z_score_min_periods_propagates(self):
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=252, lookback_days=365,
        )
        out_default = _run(params, raw_df, _custom_config())
        out_low = _run(params, raw_df, _custom_config(z_score_min_periods=30))
        # min_periods_used echoes the YAML value
        assert out_default["current_metrics"]["z_score_min_periods_used"] == 60
        assert out_low["current_metrics"]["z_score_min_periods_used"] == 30

    def test_z_score_ddof_changes_z(self):
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=120, lookback_days=180,
        )
        out_sample = _run(params, raw_df, _custom_config(z_score_ddof=1))
        out_pop = _run(params, raw_df, _custom_config(z_score_ddof=0))
        assert (
            out_sample["current_metrics"]["current_z_score"]
            != out_pop["current_metrics"]["current_z_score"]
        )

    def test_ffill_limit_passed_to_clean(self):
        from shared.analytics.levels import clean_single_series as real_clean

        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=252, lookback_days=365,
        )
        with patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.fetch_single_tenor",
            return_value=raw_df,
        ), patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.clean_single_series",
            wraps=real_clean,
        ) as spy:
            calculate_zscore_custom(
                engine=None, params=params,
                config=_custom_config(ffill_limit_days=2),
            )
        assert spy.call_count == 1
        assert spy.call_args.kwargs["ffill_limit"] == 2


# ===========================================================================
# 5. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y", z_score_window_days=252,
        )
        assert params.field_name is None

    def test_explicit_field_name_passes_through(self):
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=252, field_name="PX_LAST",
        )
        assert params.field_name == "PX_LAST"

    def test_z_score_window_days_required(self):
        with pytest.raises(Exception):
            ZscoreCustomInput(curve_family="UST", tenor="10Y")  # missing window

    def test_z_score_window_days_bounded(self):
        with pytest.raises(Exception):
            ZscoreCustomInput(
                curve_family="UST", tenor="10Y", z_score_window_days=10,
            )
        with pytest.raises(Exception):
            ZscoreCustomInput(
                curve_family="UST", tenor="10Y", z_score_window_days=2000,
            )

    def test_lookback_days_bounded(self):
        with pytest.raises(Exception):
            ZscoreCustomInput(
                curve_family="UST", tenor="10Y",
                z_score_window_days=252, lookback_days=10,
            )


class TestSmallWindowControlledError:
    """The schema accepts z_score_window_days >= 20 but the bundled
    YAML's z_score_min_periods is 60.  pandas raises if window <
    min_periods; compute() must intercept that and return a controlled
    error envelope naming both values + the YAML knob, so the caller
    sees actionable feedback instead of an unhandled crash.
    """

    def test_small_window_returns_controlled_error(self):
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=30, lookback_days=180,
        )
        out = _run(params, raw_df)  # uses bundled YAML (min_periods=60)
        assert "error" in out
        assert "30" in out["error"]
        assert "60" in out["error"]
        assert "z_score_min_periods" in out["error"]

    def test_window_equal_to_min_periods_succeeds(self):
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=60, lookback_days=180,
        )
        out = _run(params, raw_df)
        assert "error" not in out
        assert out["current_metrics"]["z_score_window_days_used"] == 60

    def test_yaml_min_periods_lowered_unblocks_smaller_window(self):
        """If the YAML's z_score_min_periods is lowered (e.g., to 30),
        a window of 30 should succeed.  Verifies the guard reads
        min_periods from config rather than hardcoding 60."""
        raw_df = _synthetic_raw_df()
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=30, lookback_days=180,
        )
        out = _run(params, raw_df, _custom_config(z_score_min_periods=30))
        assert "error" not in out


# ===========================================================================
# 6. Field name fall-through (sentinel)
# ===========================================================================

class TestFieldNameFallthrough:
    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.fetch_single_tenor",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.sovereign_bonds.tools.zscore_custom.compute.date",
            _FrozenDate,
        ):
            calculate_zscore_custom(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y", z_score_window_days=252,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="YLD_YTM_MID"),
        )
        assert passed == "YLD_YTM_MID"

    def test_yaml_override_changes_resolved_field(self):
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y", z_score_window_days=252,
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_explicit_field_name_overrides_yaml(self):
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=252, field_name="YLD_BID",
        )
        passed = self._capture_field_name(
            params, _custom_config(default_field_name="PX_LAST"),
        )
        assert passed == "YLD_BID"


# ===========================================================================
# 7. Boundary rounding — z_score_round_decimals reaches both surfaces
# ===========================================================================

class TestZScoreRoundDecimalsBoundary:
    """z_score_round_decimals must reach BOTH current_z_score AND every
    time_series row.  Same boundary-shadowing class as the field_name
    fix (b2605ee) and the butterfly z_score_round_decimals fix.

    With safe_float's default of 4, a YAML override above 4 would be
    silently truncated at the output boundary unless decimals= is
    threaded through explicitly.  This pins the wiring."""

    def test_current_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(drift_pct=-0.43217)
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=120, lookback_days=180,
        )
        out_4 = _run(params, raw_df, _custom_config(z_score_round_decimals=4))
        out_6 = _run(params, raw_df, _custom_config(z_score_round_decimals=6))

        z_4 = out_4["current_metrics"]["current_z_score"]
        z_6 = out_6["current_metrics"]["current_z_score"]
        assert z_4 is not None and z_6 is not None
        # Weak contract: 6-decimal value rounded to 4 == 4-decimal value.
        # This alone is satisfied by silent truncation, so we also need
        # the strong assertion below.
        assert round(z_6, 4) == z_4
        # Strong contract: at decimals=6 on a synthetic series with
        # non-round drift, current_z_score MUST preserve sub-4-decimal
        # precision — otherwise the boundary is silently truncating to
        # safe_float's default of 4.  If this assertion fails, the
        # decimals= kwarg is no longer reaching the metric assembly's
        # safe_float() call.
        assert z_4 != z_6, (
            f"current_z_score at decimals=4 ({z_4}) equals decimals=6 "
            f"({z_6}) — boundary is silently truncating to 4.  Fix the "
            f"safe_float() call in metric assembly to pass decimals="
            f"z_round_decimals."
        )
        # Cross-check: current_z_score at decimals=6 must equal the last
        # time_series row's value at decimals=6.  If the boundary
        # truncates current_z_score but not the time_series rows (or
        # vice versa), this asymmetry surfaces it.
        last_ts_row_6 = out_6["time_series"]["rows"][-1]["value"]
        assert z_6 == last_ts_row_6, (
            f"current_z_score ({z_6}) does not match the last "
            f"time_series row ({last_ts_row_6}) at the same precision — "
            f"the two output surfaces have drifted."
        )

    def test_time_series_z_score_uses_z_round_decimals(self):
        raw_df = _synthetic_raw_df(drift_pct=-0.43217)
        params = ZscoreCustomInput(
            curve_family="UST", tenor="10Y",
            z_score_window_days=120, lookback_days=180,
        )
        out_4 = _run(params, raw_df, _custom_config(z_score_round_decimals=4))
        out_6 = _run(params, raw_df, _custom_config(z_score_round_decimals=6))

        rows_4 = out_4["time_series"]["rows"]
        rows_6 = out_6["time_series"]["rows"]
        assert len(rows_4) == len(rows_6)

        # On a synthetic series with non-round drift, at least one row
        # should reveal sub-4-decimal precision at decimals=6.
        differs = any(
            r6["value"] is not None and r4["value"] is not None
            and round(r6["value"], 4) == r4["value"]
            and r6["value"] != r4["value"]
            for r4, r6 in zip(rows_4, rows_6)
        )
        assert differs, (
            "no time_series row showed sub-4-decimal precision at "
            "z_score_round_decimals=6 — boundary wiring may be broken"
        )


# ===========================================================================
# 8. Import-path back-compat
# ===========================================================================

class TestImportPathBackCompat:
    def test_calculate_via_package_init_and_compute_match(self):
        from rates_agent.sovereign_bonds.tools.zscore_custom import (
            calculate_zscore_custom as via_package,
        )
        from rates_agent.sovereign_bonds.tools.zscore_custom.compute import (
            calculate_zscore_custom as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_two_paths(self):
        from rates_agent.sovereign_bonds.tools.zscore_custom import (
            ZscoreCustomInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.zscore_custom.schemas import (
            ZscoreCustomInput as via_schemas,
        )
        assert via_package is via_schemas

    def test_config_path_identity(self):
        from rates_agent.sovereign_bonds.tools.zscore_custom import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.zscore_custom.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

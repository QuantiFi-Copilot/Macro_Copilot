"""
test_ois_curve_spread_compute.py — Unit tests for the OIS curve_spread migration

Mirrors ``test_curve_spread_compute.py`` (the sovereign analog) and
``test_ois_rate_level_compute.py`` (the first OIS migration), so the
three surfaces evolve together.

Covers:
  1. Bundled config.yaml structurally valid + loads cleanly.
  2. ``calculate_ois_curve_spread`` runs end-to-end against synthetic
     input with the bundled config and returns a well-formed output.
  3. Convention overrides actually change behaviour (z-window, ddof,
     spread/zscore rounding, min_periods, buffer multiplier, ffill).
  4. Schema-layer behaviour: field_name defaults to None (sentinel),
     compute() resolves the sentinel against the YAML's
     ``default_swap_rate_field``.
  5. Three import paths still resolve to the same Pydantic class;
     legacy ``schemas.spread`` shim is gone.
  6. Canonical TimeSeries output (BPS spread + Z_SCORE rolling) align
     point-by-point with the bespoke wire-frozen ``time_series`` rows.
  7. OIS-specific spread-label formatting (``3M/2Y`` for sub-year).

Tests are fully offline — DB fetcher mocked, ``date.today()`` frozen.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.ois.tools.curve_spread import (
    CONFIG_PATH,
    calculate_ois_curve_spread,
    OISCurveSpreadInput,
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


def _synthetic_raw_df(years: int = 5, frozen_today: date = date(2026, 4, 30)) -> pd.DataFrame:
    """Build a synthetic long-format raw DataFrame matching the shape
    fetch_tenor_pair returns for two tenors of one OIS curve.  Mean-
    reverting random-walk so the rolling z-score is well-populated.
    """
    bdays = pd.bdate_range(frozen_today - timedelta(days=years * 365), frozen_today)

    def _series(tenor: str, mu: float, vol: float, init: float, seed: int) -> pd.DataFrame:
        rs2 = np.random.RandomState(seed)
        n = len(bdays)
        v = np.empty(n, dtype=float)
        v[0] = init
        for i in range(1, n):
            v[i] = v[i - 1] + 0.005 * (mu - v[i - 1]) + rs2.randn() * vol
        return pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "tenor": tenor,
            "field_value": v,
        })

    return pd.concat(
        [
            _series("2Y", 4.30, 0.045, 4.20, 11),
            _series("10Y", 4.55, 0.040, 4.50, 12),
        ],
        ignore_index=True,
    ).sort_values(["trade_date", "tenor"]).reset_index(drop=True)


class _FrozenDate(date):
    """Patches ``compute.date`` so the fetch ``start_date`` is
    deterministic.  The OIS tool's display cutoff is anchored to the
    data's last index date (not ``date.today()``), so the frozen-today
    value only matters for the fetch-window computation (which is
    mocked away in these tests anyway).
    """
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


_BUNDLED_DEFAULTS = {
    "z_score_window_days": 252,
    "z_score_min_periods": 60,
    "z_score_ddof": 1,
    "z_score_buffer_multiplier": 1.5,
    "ffill_limit_days": 5,
    "spread_bps_round_decimals": 2,
    "z_score_round_decimals": 4,
    "default_swap_rate_field": "PX_LAST",
}


def _build_config(**overrides) -> ToolConfig:
    defaults = dict(_BUNDLED_DEFAULTS)
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(name="t", domain="d", description="x"),
        methodology=MethodologyMeta(what_it_does="x"),
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
        assert cfg.tool.name == "calculate_ois_curve_spread_tool"
        assert cfg.tool.domain == "ois"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "ffill_limit_days",
            "spread_bps_round_decimals",
            "z_score_round_decimals",
            "default_swap_rate_field",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_convention_defaults_match_legacy(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("spread_bps_round_decimals") == 2
        assert cfg.convention_value("z_score_round_decimals") == 4
        assert cfg.convention_value("default_swap_rate_field") == "PX_LAST"

    def test_no_default_field_name_collision_with_sovereign(self):
        """Sovereign tools set ``default_field_name: YLD_YTM_MID``.
        OIS tools want PX_LAST.  The OIS curve_spread MUST use the
        OIS-specific name ``default_swap_rate_field`` to avoid
        tripping the cross-config lint."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "default_swap_rate_field" in cfg.conventions
        assert "default_field_name" not in cfg.conventions, (
            "OIS curve_spread must not redeclare the sovereign "
            "convention name — would clash with sovereign value "
            "YLD_YTM_MID in the cross-config lint."
        )


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def _run(self, params, config=None):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            return calculate_ois_curve_spread(
                engine=None, params=params, config=config,
            )

    def test_default_config_returns_well_formed_output(self):
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
            lookback_days=365,
        )
        out = self._run(params)

        assert "error" not in out, out.get("error")
        assert "current_metrics" in out
        assert "time_series" in out
        assert "time_series_spread" in out
        assert "time_series_zscore" in out

        cm = out["current_metrics"]
        assert cm["curve_family"] == "USD_SOFR_OIS"
        assert cm["spread_label"] == "2s10s"
        assert cm["rolling_window_days"] == 252
        assert isinstance(cm["current_spread_bps"], float)
        # OIS-specific snapshot fields use "rate" (not "yield").
        assert "short_tenor_rate" in cm
        assert "long_tenor_rate" in cm
        assert "short_tenor_yield" not in cm
        assert "long_tenor_yield" not in cm
        assert cm["current_z_score"] is not None

        ts = out["time_series"]
        assert len(ts) > 0
        for row in ts:
            assert "date" in row and "spread_bps" in row

    def test_explicit_default_config_matches_auto_loaded(self):
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
            lookback_days=365,
        )
        out_auto = self._run(params, config=None)
        out_explicit = self._run(params, config=load_tool_config(CONFIG_PATH))
        assert out_auto == out_explicit


# ===========================================================================
# 3. Convention overrides
# ===========================================================================

class TestConventionOverrides:
    def _run(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            return calculate_ois_curve_spread(
                engine=None, params=params, config=config,
            )

    @pytest.fixture
    def params(self):
        return OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
            lookback_days=365,
        )

    def test_z_score_window_override_changes_z(self, params):
        out_default = self._run(params, _build_config())
        out_override = self._run(params, _build_config(z_score_window_days=120))
        assert (out_default["current_metrics"]["current_spread_bps"]
                == out_override["current_metrics"]["current_spread_bps"])
        assert (out_default["current_metrics"]["current_z_score"]
                != out_override["current_metrics"]["current_z_score"])

    def test_z_score_ddof_override_changes_z(self, params):
        out_sample = self._run(params, _build_config(z_score_ddof=1))
        out_pop = self._run(params, _build_config(z_score_ddof=0))
        assert (out_sample["current_metrics"]["current_z_score"]
                != out_pop["current_metrics"]["current_z_score"])

    def test_spread_round_decimals_override_changes_precision(self, params):
        out_2dp = self._run(params, _build_config(spread_bps_round_decimals=2))
        out_4dp = self._run(params, _build_config(spread_bps_round_decimals=4))
        ts_2dp = out_2dp["time_series"]
        ts_4dp = out_4dp["time_series"]
        assert len(ts_2dp) == len(ts_4dp)
        differs = any(
            ts_2dp[i]["spread_bps"] != ts_4dp[i]["spread_bps"]
            for i in range(len(ts_2dp))
        )
        assert differs

    def test_zscore_round_decimals_override_changes_precision(self, params):
        out_4 = self._run(params, _build_config(z_score_round_decimals=4))
        out_2 = self._run(params, _build_config(z_score_round_decimals=2))
        ts_4 = out_4["time_series"]
        ts_2 = out_2["time_series"]
        differs = any(
            ts_4[i].get("z_score") != ts_2[i].get("z_score")
            for i in range(len(ts_4))
            if ts_4[i].get("z_score") is not None and ts_2[i].get("z_score") is not None
        )
        assert differs

    def test_zscore_round_decimals_above_4_is_honored(self, params):
        """Regression guard: ``safe_float`` defaults to ``decimals=4``,
        so a previous version of this tool silently truncated z-scores
        back to 4 decimals when the YAML asked for finer precision.
        Compute must pass ``decimals=zscore_round`` explicitly to
        every ``safe_float`` call on the z-score column.

        Verified across all three exposed surfaces:
          1. ``current_metrics.current_z_score``
          2. bespoke ``time_series[i].z_score``
          3. canonical ``time_series_zscore.rows[i].value``
        """
        out_4 = self._run(params, _build_config(z_score_round_decimals=4))
        out_6 = self._run(params, _build_config(z_score_round_decimals=6))

        # 1. current_metrics.current_z_score: a 6dp value rounded to 4dp
        # would truncate; rounding it back to 4 must change the magnitude
        # in at least some cases over the synthetic series (we run the
        # snapshot many times to find at least one observation where the
        # 5th/6th decimal is non-zero).
        cm4 = out_4["current_metrics"]["current_z_score"]
        cm6 = out_6["current_metrics"]["current_z_score"]
        # If 6dp == 4dp at the snapshot date by coincidence, fall back
        # to checking the time_series rows.
        diff_at_snapshot = (cm4 != cm6) or (round(cm6, 4) != cm6)
        # 2. bespoke time_series[i].z_score
        ts_4 = out_4["time_series"]
        ts_6 = out_6["time_series"]
        diff_in_ts = any(
            ts_4[i].get("z_score") != ts_6[i].get("z_score")
            for i in range(len(ts_4))
            if ts_4[i].get("z_score") is not None
            and ts_6[i].get("z_score") is not None
        )
        # 3. canonical time_series_zscore.rows[i].value
        cz_4 = out_4["time_series_zscore"]["rows"]
        cz_6 = out_6["time_series_zscore"]["rows"]
        diff_in_canonical = any(
            cz_4[i].get("value") != cz_6[i].get("value")
            for i in range(len(cz_4))
            if cz_4[i].get("value") is not None
            and cz_6[i].get("value") is not None
        )
        # At least one of the three surfaces must show >4dp precision —
        # if NONE of them do, ``safe_float``'s default-decimals=4 is
        # silently truncating again.
        assert diff_at_snapshot or diff_in_ts or diff_in_canonical, (
            "z_score_round_decimals=6 produced output identical to "
            "z_score_round_decimals=4 across all three surfaces "
            "(current_z_score, bespoke time_series[].z_score, "
            "canonical time_series_zscore.rows[].value).  "
            "safe_float()'s default decimals=4 is silently truncating — "
            "compute must pass decimals=zscore_round explicitly."
        )

        # Cross-surface consistency: the bespoke and canonical z-score
        # series must still agree row-by-row even at >4 decimals.
        for i, (b_row, c_row) in enumerate(zip(ts_6, cz_6)):
            assert b_row.get("z_score") == c_row.get("value"), (
                f"row {i}: bespoke z_score {b_row.get('z_score')!r} "
                f"!= canonical value {c_row.get('value')!r} at "
                "z_score_round_decimals=6"
            )

    def test_rolling_window_days_appears_in_metrics(self, params):
        out = self._run(params, _build_config(z_score_window_days=180))
        assert out["current_metrics"]["rolling_window_days"] == 180

    def test_min_periods_passed_to_rolling_zscore(self, params):
        from shared.analytics.spreads import rolling_zscore as real_rolling
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.rolling_zscore",
            wraps=real_rolling,
        ) as spy:
            calculate_ois_curve_spread(
                engine=None, params=params,
                config=_build_config(z_score_min_periods=200),
            )
        assert spy.call_count >= 1
        assert spy.call_args.kwargs["min_periods"] == 200
        assert spy.call_args.kwargs["window"] == 252

    def test_buffer_multiplier_changes_fetch_start_date(self, params):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ) as spy_default, patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            calculate_ois_curve_spread(
                engine=None, params=params, config=_build_config(),
            )
        default_start = spy_default.call_args.kwargs["start_date"]

        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ) as spy_override, patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            calculate_ois_curve_spread(
                engine=None, params=params,
                config=_build_config(z_score_buffer_multiplier=2.0),
            )
        override_start = spy_override.call_args.kwargs["start_date"]

        assert override_start < default_start
        diff_days = (default_start - override_start).days
        assert diff_days == 126, (
            f"expected 126-day shift from buffer_mult 1.5→2.0, got {diff_days}"
        )

    def test_ffill_limit_passed_to_pivot(self, params):
        from shared.analytics.spreads import pivot_and_align_tenors as real_pivot
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.pivot_and_align_tenors",
            wraps=real_pivot,
        ) as spy:
            calculate_ois_curve_spread(
                engine=None, params=params,
                config=_build_config(ffill_limit_days=2),
            )
        assert spy.call_count >= 1
        assert spy.call_args.kwargs["ffill_limit"] == 2


# ===========================================================================
# 4. Schema-layer field_name behaviour (sentinel pattern)
# ===========================================================================

class TestFieldNameSchemaBehaviour:
    def test_field_name_default_is_none(self):
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
        )
        assert params.field_name is None

    def test_explicit_field_name_passes_through(self):
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
            field_name="PX_BID",
        )
        assert params.field_name == "PX_BID"

    def test_tenors_must_differ_validator(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            OISCurveSpreadInput(
                curve_family="USD_SOFR_OIS",
                short_tenor="5Y", long_tenor="5Y",
            )


class TestFieldNameYamlFallthrough:
    """End-to-end proof that YAML default_swap_rate_field reaches fetch."""

    def _capture_field_name(self, params, config):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ) as spy, patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            calculate_ois_curve_spread(engine=None, params=params, config=config)
        assert spy.call_count == 1
        return spy.call_args.kwargs["field_name"]

    def test_omitted_field_name_uses_yaml_default(self):
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
        )
        passed = self._capture_field_name(
            params, _build_config(default_swap_rate_field="PX_LAST"),
        )
        assert passed == "PX_LAST"

    def test_yaml_override_changes_resolved_field(self):
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
        )
        passed = self._capture_field_name(
            params, _build_config(default_swap_rate_field="PX_BID"),
        )
        assert passed == "PX_BID"

    def test_explicit_field_name_overrides_yaml(self):
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
            field_name="PX_ASK",
        )
        passed = self._capture_field_name(
            params, _build_config(default_swap_rate_field="PX_LAST"),
        )
        assert passed == "PX_ASK"


# ===========================================================================
# 5. Import-path resolution after the per-tool-folder migration
# ===========================================================================

class TestImportPathBackwardCompat:
    def test_calculate_ois_curve_spread_via_package_init(self):
        from rates_agent.ois.tools.curve_spread import (
            calculate_ois_curve_spread as via_package,
        )
        from rates_agent.ois.tools.curve_spread.compute import (
            calculate_ois_curve_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_three_paths(self):
        from rates_agent.ois.tools.curve_spread import (
            OISCurveSpreadInput as via_package,
        )
        from rates_agent.ois.tools.curve_spread.schemas import (
            OISCurveSpreadInput as via_schemas,
        )
        from rates_agent.ois.tools.schemas import (
            OISCurveSpreadInput as via_hub,
        )
        assert via_package is via_schemas
        assert via_package is via_hub

    def test_legacy_single_file_path_is_deleted(self):
        """The legacy single-file ``rates_agent/ois/tools/curve_spread.py``
        and ``rates_agent/ois/tools/schemas/spread.py`` must NOT be
        importable as modules — the per-tool-folder package now owns
        those names."""
        import importlib
        import rates_agent.ois.tools.curve_spread as pkg
        assert hasattr(pkg, "__path__"), (
            "curve_spread must be a package (folder), not a single-file module"
        )
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(
                "rates_agent.ois.tools.schemas.spread"
            )


# ===========================================================================
# 6. Canonical TimeSeries output
# ===========================================================================

class TestCanonicalTimeSeries:
    """Pin the canonical TimeSeries contract on the OIS curve_spread
    surface.  Two canonical fields (``time_series_spread`` BPS and
    ``time_series_zscore`` Z_SCORE) must align point-by-point with
    the bespoke wire-frozen ``time_series`` rows so downstream
    operators consuming the canonical payloads cannot drift from the
    snapshot/UI surface.
    """

    def _run(self, params, config=None):
        raw_df = _synthetic_raw_df()
        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            return calculate_ois_curve_spread(
                engine=None, params=params, config=config,
            )

    def _params(self):
        return OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor="2Y", long_tenor="10Y",
            lookback_days=365,
        )

    def test_time_series_spread_field_present(self):
        out = self._run(self._params())
        assert "time_series_spread" in out
        assert isinstance(out["time_series_spread"], dict)

    def test_time_series_zscore_field_present(self):
        out = self._run(self._params())
        assert "time_series_zscore" in out
        assert isinstance(out["time_series_zscore"], dict)

    def test_spread_series_uses_BPS(self):
        out = self._run(self._params())
        assert out["time_series_spread"]["units"] == "bps"

    def test_zscore_series_uses_Z_SCORE(self):
        out = self._run(self._params())
        assert out["time_series_zscore"]["units"] == "z_score"

    def test_spread_series_name_follows_convention(self):
        out = self._run(self._params())
        assert (
            out["time_series_spread"]["series_name"]
            == "usd_sofr_ois_2y_10y_ois_spread"
        )

    def test_zscore_series_name_follows_convention(self):
        out = self._run(self._params())
        assert (
            out["time_series_zscore"]["series_name"]
            == "usd_sofr_ois_2y_10y_ois_zscore"
        )

    def test_both_series_length_equals_bespoke_length(self):
        out = self._run(self._params())
        spread = out["time_series_spread"]
        zscore = out["time_series_zscore"]
        bespoke = out["time_series"]
        assert len(spread["rows"]) == len(bespoke)
        assert len(zscore["rows"]) == len(bespoke)

    def test_spread_values_match_bespoke_pointwise(self):
        out = self._run(self._params())
        spread = out["time_series_spread"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(spread["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["spread_bps"], (
                f"row {i}: value mismatch"
            )

    def test_zscore_values_match_bespoke_pointwise(self):
        out = self._run(self._params())
        zscore = out["time_series_zscore"]
        bespoke = out["time_series"]
        for i, (c_row, b_row) in enumerate(zip(zscore["rows"], bespoke)):
            assert c_row["date"] == b_row["date"], f"row {i}: date mismatch"
            assert c_row["value"] == b_row["z_score"], (
                f"row {i}: value mismatch"
            )

    def test_both_canonical_series_validate_against_TimeSeries_schema(self):
        from shared.schemas import TimeSeries
        out = self._run(self._params())
        TimeSeries.model_validate(out["time_series_spread"])
        TimeSeries.model_validate(out["time_series_zscore"])


# ===========================================================================
# 7. OIS-specific spread label formatting
# ===========================================================================

class TestSpreadLabelFormatting:
    """The OIS tool preserves the legacy single-file tool's label
    convention: pure-year pairs render as 'NsMs' (e.g. '2s10s'),
    sub-year pairs render as 'short/long' (e.g. '3M/2Y') because
    '3Ms2s' would mislead a reader.
    """

    def _run(self, short_tenor: str, long_tenor: str) -> dict:
        # Build a one-curve, two-tenor synthetic DF with both requested
        # tenors present (whichever they are).
        bdays = pd.bdate_range(date(2024, 1, 1), date(2026, 4, 30))
        rows = []
        rs = np.random.RandomState(7)
        for tenor, init in ((short_tenor, 4.20), (long_tenor, 4.50)):
            v = init + rs.randn(len(bdays)).cumsum() * 0.01
            for d, val in zip(bdays, v):
                rows.append({
                    "trade_date": d.date(), "tenor": tenor, "field_value": val,
                })
        raw_df = pd.DataFrame(rows)
        params = OISCurveSpreadInput(
            curve_family="USD_SOFR_OIS",
            short_tenor=short_tenor, long_tenor=long_tenor,
            lookback_days=365,
        )
        with patch(
            "rates_agent.ois.tools.curve_spread.compute.fetch_tenor_pair",
            return_value=raw_df,
        ), patch(
            "rates_agent.ois.tools.curve_spread.compute.date",
            _FrozenDate,
        ):
            return calculate_ois_curve_spread(engine=None, params=params)

    def test_pure_year_pair_renders_as_2s10s(self):
        out = self._run("2Y", "10Y")
        assert out["current_metrics"]["spread_label"] == "2s10s"

    def test_subyear_short_pair_renders_with_slash(self):
        out = self._run("3M", "2Y")
        assert out["current_metrics"]["spread_label"] == "3M/2Y"

    def test_subyear_both_pair_renders_with_slash(self):
        out = self._run("1M", "6M")
        assert out["current_metrics"]["spread_label"] == "1M/6M"

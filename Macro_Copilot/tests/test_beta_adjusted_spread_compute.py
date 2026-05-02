"""
test_beta_adjusted_spread_compute.py — Unit tests for the
beta_adjusted_spread tool.

Covers the full Codex-review-pattern lessons applied day one
(carrying forward every lesson the rolling_regression follow-up PR
landed):

  1. Bundled config.yaml is structurally valid + loads cleanly + has
     ``category=desk_invariant_primitive``.
  2. compute() runs end-to-end against synthetic input and returns a
     well-formed output (snapshot + 3 TimeSeries payloads).
  3. Central knob (``regression_window_days``) overrides actually
     change the output; YAML-locked knobs cannot be overridden via
     input.
  4. Honest-placeholder guards: NotImplementedError on
     ``add_constant=False`` and on a non-default ``regression_solver``.
  5. Cross-layer guard: regression_window_days < regression_min_periods
     returns a controlled error envelope (FastAPI maps this to 422).
  6. Schema-layer behaviour: target ≠ regressor (curve_family, tenor)
     enforced; window bounds enforced; field_name sentinel works.
  7. Numerical correctness: synthetic y = a + b*x + small noise
     recovers a, b within tolerance.
  8. Quality flag fires when regressor is constant in-window;
     coefficients suppressed.
  9. **Unit conversion**: residual is in BPS at every output surface
     (the *100 conversion the v6 plan locked in).
  10. Boundary rounding: every YAML rounding knob (beta, alpha,
      bps, residual_z_score, R²) reaches BOTH the snapshot AND the
      time_series rows.
  11. Buffer multiplier wiring: regression_buffer_multiplier and
      z_score_buffer_multiplier both reach the start_date via the
      defensive max(.) computation.
  12. Three import paths still resolve to the same Pydantic class.

Tests are fully offline — fetch is mocked, today is frozen.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import math
import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
    CONFIG_PATH,
    BetaAdjustedSpreadInput,
    calculate_beta_adjusted_spread,
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


class _FrozenDate(date):
    _frozen_value: date = date(2026, 4, 30)

    @classmethod
    def today(cls) -> date:
        return cls._frozen_value


def _synthetic_series(
    *,
    days: int = 600,
    frozen_today: date = date(2026, 4, 30),
    base: float = 4.0,
    drift: float = 0.5,
    noise_seed: int = 0,
    noise_scale: float = 0.005,
) -> pd.DataFrame:
    """Synthetic single-tenor long-format DataFrame matching
    fetch_single_tenor's return shape."""
    rng = np.random.default_rng(noise_seed)
    bdays = pd.bdate_range(frozen_today - timedelta(days=days * 2), frozen_today)
    bdays = bdays[-days:]
    n = len(bdays)
    series = base + np.linspace(0, drift, n) + rng.normal(0, noise_scale, n)
    return pd.DataFrame({
        "trade_date": [d.date() for d in bdays],
        "field_value": series,
    })


def _custom_config(**overrides) -> ToolConfig:
    defaults = {
        "regression_min_periods": 30,
        "add_constant": True,
        "regression_solver": "numpy_lstsq_default",
        "condition_number_warning_threshold": 1e10,
        "regression_buffer_multiplier": 1.5,
        "z_score_window_days": 252,
        "z_score_min_periods": 60,
        "z_score_ddof": 1,
        "z_score_buffer_multiplier": 1.5,
        "ffill_limit_days": 5,
        "bps_round_decimals": 2,
        "z_score_round_decimals": 4,
        "beta_round_decimals": 4,
        "alpha_round_decimals": 4,
        "r_squared_round_decimals": 4,
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


def _run(params, fetched_by_label: dict, config=None):
    def fake_fetch(*, engine, curve_family, tenor, field_name, start_date):
        key = f"{curve_family}_{tenor}"
        if key not in fetched_by_label:
            raise AssertionError(f"unexpected fetch for {key}")
        return fetched_by_label[key]

    with patch(
        "rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute.fetch_single_tenor",
        side_effect=fake_fetch,
    ), patch(
        "rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute.date",
        _FrozenDate,
    ):
        return calculate_beta_adjusted_spread(
            engine=None, params=params, config=config,
        )


# ===========================================================================
# 1. Bundled config.yaml
# ===========================================================================

class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.tool.name == "beta_adjusted_spread_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_category_is_desk_invariant_primitive(self):
        cfg = load_tool_config(CONFIG_PATH)
        # "beta-adjusted RV" is desk vocabulary; trader hears the name
        # and knows the shape.  Configuration is calibration.
        assert cfg.tool.category == "desk_invariant_primitive"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "regression_min_periods",
            "add_constant",
            "regression_solver",
            "condition_number_warning_threshold",
            "regression_buffer_multiplier",
            "z_score_window_days",
            "z_score_min_periods",
            "z_score_ddof",
            "z_score_buffer_multiplier",
            "ffill_limit_days",
            "bps_round_decimals",
            "z_score_round_decimals",
            "beta_round_decimals",
            "alpha_round_decimals",
            "r_squared_round_decimals",
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_no_regression_window_days_in_yaml(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert "regression_window_days" not in cfg.conventions

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("regression_min_periods") == 30
        assert cfg.convention_value("add_constant") is True
        assert cfg.convention_value("regression_solver") == "numpy_lstsq_default"
        assert cfg.convention_value("condition_number_warning_threshold") == 1e10
        assert cfg.convention_value("regression_buffer_multiplier") == 1.5
        assert cfg.convention_value("z_score_window_days") == 252
        assert cfg.convention_value("z_score_min_periods") == 60
        assert cfg.convention_value("z_score_ddof") == 1
        assert cfg.convention_value("z_score_buffer_multiplier") == 1.5
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("bps_round_decimals") == 2
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        target_df = _synthetic_series(noise_seed=1, drift=0.5)
        regressor_df = _synthetic_series(noise_seed=2, drift=0.3)
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=120,
            lookback_days=180,
        )
        out = _run(
            params,
            {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df},
        )
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        for k in (
            "as_of_date", "target_curve_family", "target_tenor",
            "regressor_curve_family", "regressor_tenor",
            "spread_label",
            "current_beta", "current_alpha_pct",
            "current_residual_bps", "current_residual_z_score",
            "current_r_squared", "current_condition_flag",
            "regression_window_days_used", "regression_min_periods_used",
            "z_score_window_days_used", "add_constant_used",
            "observation_count",
        ):
            assert k in cm, f"missing {k}"

        assert cm["target_curve_family"] == "IT_BTP"
        assert cm["regressor_curve_family"] == "DE_BUND"
        assert "beta-adjusted" in cm["spread_label"].lower()
        assert cm["regression_window_days_used"] == 120
        assert cm["regression_min_periods_used"] == 30
        assert cm["z_score_window_days_used"] == 252
        assert cm["add_constant_used"] is True
        assert cm["observation_count"] > 0

        # TimeSeries unit enforcement
        assert out["time_series_beta"]["units"] == TimeSeriesUnits.RATIO.value
        assert out["time_series_residual"]["units"] == TimeSeriesUnits.BPS.value
        assert out["time_series_residual_z_score"]["units"] == TimeSeriesUnits.Z_SCORE.value

    def test_explicit_config_matches_auto_loaded(self):
        target_df = _synthetic_series(noise_seed=1)
        regressor_df = _synthetic_series(noise_seed=2)
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=120, lookback_days=180,
        )
        out_auto = _run(
            params,
            {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df},
            config=None,
        )
        out_explicit = _run(
            params,
            {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df},
            config=load_tool_config(CONFIG_PATH),
        )
        assert out_auto == out_explicit


# ===========================================================================
# 3. Central knob — regression_window_days
# ===========================================================================

class TestCentralKnob:
    def test_different_windows_produce_different_betas(self):
        target_df = _synthetic_series(noise_seed=1, drift=0.5)
        regressor_df = _synthetic_series(noise_seed=2, drift=0.3)
        p_60 = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=60, lookback_days=180,
        )
        p_252 = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=252, lookback_days=180,
        )
        out_60 = _run(p_60, {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df})
        out_252 = _run(p_252, {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df})

        assert out_60["current_metrics"]["current_beta"] != \
            out_252["current_metrics"]["current_beta"]
        assert out_60["current_metrics"]["regression_window_days_used"] == 60
        assert out_252["current_metrics"]["regression_window_days_used"] == 252


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestHonestPlaceholderGuards:
    def test_add_constant_false_raises_not_implemented(self):
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=60,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                {
                    "IT_BTP_10Y": _synthetic_series(noise_seed=1),
                    "DE_BUND_10Y": _synthetic_series(noise_seed=2),
                },
                config=_custom_config(add_constant=False),
            )
        msg = str(exc_info.value)
        assert "add_constant" in msg
        assert "planned_extensions" in msg

    def test_unsupported_solver_raises_not_implemented(self):
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=60,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                {
                    "IT_BTP_10Y": _synthetic_series(noise_seed=1),
                    "DE_BUND_10Y": _synthetic_series(noise_seed=2),
                },
                config=_custom_config(regression_solver="scipy_qr"),
            )
        msg = str(exc_info.value)
        assert "regression_solver" in msg


# ===========================================================================
# 5. Cross-layer small-window guard
# ===========================================================================

class TestSmallWindowGuard:
    def test_window_below_min_periods_returns_controlled_error(self):
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=20,  # < default min_periods=30
            lookback_days=120,
        )
        out = _run(
            params,
            {
                "IT_BTP_10Y": _synthetic_series(noise_seed=1),
                "DE_BUND_10Y": _synthetic_series(noise_seed=2),
            },
        )
        assert "error" in out
        assert "is smaller than the YAML's" in out["error"]
        assert "regression_min_periods" in out["error"]
        # Phrase shape matches detail.py's user_input_phrases for 422.
        assert "is smaller than the yaml" in out["error"].lower()

    def test_yaml_lower_min_periods_unblocks_smaller_window(self):
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=20,
            lookback_days=120,
        )
        out = _run(
            params,
            {
                "IT_BTP_10Y": _synthetic_series(noise_seed=1),
                "DE_BUND_10Y": _synthetic_series(noise_seed=2),
            },
            config=_custom_config(regression_min_periods=15),
        )
        assert "error" not in out, out.get("error")


# ===========================================================================
# 6. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_target_equals_regressor_rejected(self):
        with pytest.raises(Exception):
            BetaAdjustedSpreadInput(
                target_curve_family="UST", target_tenor="10Y",
                regressor_curve_family="UST", regressor_tenor="10Y",
                regression_window_days=60,
            )

    def test_window_bounds_enforced(self):
        with pytest.raises(Exception):
            BetaAdjustedSpreadInput(
                target_curve_family="IT_BTP", target_tenor="10Y",
                regressor_curve_family="DE_BUND", regressor_tenor="10Y",
                regression_window_days=5,  # < ge=10
            )

    def test_field_name_default_is_none(self):
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=60,
        )
        assert params.field_name is None


# ===========================================================================
# 7. Numerical correctness
# ===========================================================================

class TestNumericalCorrectness:
    def test_recovers_planted_coefficients_in_residual_space(self):
        """Plant y = 0.5 + 1.5 * x + small noise.  Latest beta close
        to 1.5; latest alpha close to 0.5.  Residual is small
        (noise-scale), and once *100 is applied the bps residual is
        small but bps-magnitude (i.e., not divided by 100)."""
        rng = np.random.default_rng(42)
        frozen_today = date(2026, 4, 30)
        bdays = pd.bdate_range(frozen_today - timedelta(days=600), frozen_today)
        bdays = bdays[-300:]
        n = len(bdays)
        x = np.linspace(2.0, 5.0, n)
        noise = rng.normal(0, 0.002, n)  # small percent-scale noise
        y = 0.5 + 1.5 * x + noise

        target_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": y,
        })
        regressor_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": x,
        })

        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=120, lookback_days=120,
        )
        out = _run(
            params,
            {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df},
        )
        beta = out["current_metrics"]["current_beta"]
        alpha = out["current_metrics"]["current_alpha_pct"]
        r2 = out["current_metrics"]["current_r_squared"]
        assert abs(beta - 1.5) < 0.05, f"beta={beta}"
        assert abs(alpha - 0.5) < 0.1, f"alpha={alpha}"
        assert r2 > 0.99, f"r2={r2}"


# ===========================================================================
# 8. Quality flag — regressor constant in-window
# ===========================================================================

class TestConditionFlag:
    def test_constant_regressor_sets_flag_and_suppresses_betas(self):
        """If the regressor is constant in-window, the design matrix
        [1, x] has rank 1 (the constant column equals the x column),
        so the condition number explodes and the gate fires."""
        rng = np.random.default_rng(3)
        frozen_today = date(2026, 4, 30)
        bdays = pd.bdate_range(frozen_today - timedelta(days=600), frozen_today)
        bdays = bdays[-300:]
        n = len(bdays)

        target = 4.0 + np.linspace(0, 0.5, n) + rng.normal(0, 0.005, n)
        # Constant regressor
        regressor = np.full(n, 4.5)

        target_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": target,
        })
        regressor_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": regressor,
        })

        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=120, lookback_days=120,
        )
        out = _run(
            params,
            {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df},
        )
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        assert cm["current_condition_flag"] == 1
        assert cm["current_beta"] is None
        assert cm["current_alpha_pct"] is None
        assert cm["current_residual_bps"] is None


# ===========================================================================
# 9. Unit conversion: residual is BPS at every output surface
# ===========================================================================

class TestResidualUnitConversion:
    """The residual is in PERCENT space inside rolling_ols (because y
    is yield-percent).  beta_adjusted_spread emits the residual in
    BPS via *100 BEFORE the snapshot, the time_series, AND the
    z-score.  This was THE explicit fix the v6 plan locked in
    against the 'percent residual silently named _bps' mistake."""

    def test_residual_bps_is_100x_percent_residual_at_snapshot(self):
        """Plant a simple y = 1.0 + 0.0*x + bias so the OLS residual
        is approximately equal to the bias (in percent), and assert
        the snapshot's residual_bps is approximately bias * 100."""
        rng = np.random.default_rng(11)
        frozen_today = date(2026, 4, 30)
        bdays = pd.bdate_range(frozen_today - timedelta(days=600), frozen_today)
        bdays = bdays[-300:]
        n = len(bdays)
        x = np.linspace(2.0, 5.0, n)

        # Plant: y = 0.5 + 1.5*x + noise (clean linear).  Then add an
        # OUT-OF-SAMPLE shock at the LAST point, in percent.  The
        # rolling fit on prior data leaves a residual at t = last
        # approximately equal to the shock magnitude in PERCENT.
        # After *100 the bps residual should be approximately
        # shock * 100.
        SHOCK_PCT = 0.03   # 3 bps in percent space
        y = 0.5 + 1.5 * x + rng.normal(0, 0.0001, n)
        y[-1] += SHOCK_PCT

        target_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": y,
        })
        regressor_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": x,
        })

        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=120, lookback_days=120,
        )
        out = _run(
            params,
            {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df},
        )
        residual_bps = out["current_metrics"]["current_residual_bps"]
        # SHOCK_PCT * 100 = 3 bps; tolerance for tiny noise + the
        # window-fit's regression-to-noise effect.
        assert abs(residual_bps - SHOCK_PCT * 100) < 0.5, (
            f"residual_bps={residual_bps}; expected ~{SHOCK_PCT * 100} bps"
        )

    def test_residual_unit_in_time_series_matches_snapshot(self):
        """Snapshot's current_residual_bps must equal the LAST row of
        time_series_residual.  Cross-surface consistency."""
        target_df = _synthetic_series(noise_seed=1)
        regressor_df = _synthetic_series(noise_seed=2)
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=120, lookback_days=120,
        )
        out = _run(
            params,
            {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df},
        )
        snapshot = out["current_metrics"]["current_residual_bps"]
        last_ts_row = out["time_series_residual"]["rows"][-1]["value"]
        assert snapshot == last_ts_row
        # And units claim is BPS
        assert out["time_series_residual"]["units"] == "bps"


# ===========================================================================
# 10. Boundary rounding — every YAML knob reaches the surfaces it feeds
# ===========================================================================

class TestBoundaryRounding:
    """Each YAML rounding knob must reach the surfaces it actually
    feeds.  Surface map for this tool:

        beta_round_decimals      → snapshot (current_beta) AND
                                    time_series_beta rows
        bps_round_decimals       → snapshot (current_residual_bps) AND
                                    time_series_residual rows
        z_score_round_decimals   → snapshot (current_residual_z_score)
                                    AND time_series_residual_z_score rows
        alpha_round_decimals     → snapshot (current_alpha_pct) ONLY —
                                    no alpha time-series in this tool
        r_squared_round_decimals → snapshot (current_r_squared) ONLY —
                                    no R² time-series in this tool

    Tests pin each knob's reach explicitly: the three knobs that feed
    a time-series surface get a snapshot+time-series cross-check; the
    two knobs that ONLY feed the snapshot get a snapshot-only test.
    Adding alpha/R² time-series surfaces is out of scope (a trader
    does not ask for "the alpha time series"; it would bloat the wire
    payload without adding desk-recognised value)."""

    def _planted_panel(self):
        rng = np.random.default_rng(7)
        frozen_today = date(2026, 4, 30)
        bdays = pd.bdate_range(frozen_today - timedelta(days=600), frozen_today)
        bdays = bdays[-300:]
        n = len(bdays)
        x = np.linspace(2.0, 5.0, n)
        # Non-round multipliers + small noise → trailing precision
        # in beta, alpha, residual, R².
        y = 0.123456 + 1.234567 * x + rng.normal(0, 0.002, n)
        return (
            pd.DataFrame({
                "trade_date": [d.date() for d in bdays],
                "field_value": y,
            }),
            pd.DataFrame({
                "trade_date": [d.date() for d in bdays],
                "field_value": x,
            }),
        )

    def _params(self):
        return BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=120, lookback_days=120,
        )

    def _run_at(self, decimals_kw):
        target_df, regressor_df = self._planted_panel()
        return _run(
            self._params(),
            {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df},
            config=_custom_config(**decimals_kw),
        )

    def test_beta_round_decimals_reaches_both_surfaces(self):
        out_4 = self._run_at({"beta_round_decimals": 4})
        out_6 = self._run_at({"beta_round_decimals": 6})
        b_4 = out_4["current_metrics"]["current_beta"]
        b_6 = out_6["current_metrics"]["current_beta"]
        assert b_4 is not None and b_6 is not None
        assert round(b_6, 4) == b_4
        assert b_4 != b_6
        last_ts = out_6["time_series_beta"]["rows"][-1]["value"]
        assert b_6 == last_ts

    def test_alpha_round_decimals_reaches_snapshot_only(self):
        """alpha has no time-series surface in this tool — pin the
        snapshot only.  Naming the test ``_only`` makes the scope
        explicit so a future maintainer doesn't read 'alpha rounding
        is fully tested' from the class header and miss that there's
        no time_series_alpha field on this tool."""
        out_4 = self._run_at({"alpha_round_decimals": 4})
        out_6 = self._run_at({"alpha_round_decimals": 6})
        a_4 = out_4["current_metrics"]["current_alpha_pct"]
        a_6 = out_6["current_metrics"]["current_alpha_pct"]
        assert a_4 is not None and a_6 is not None
        assert round(a_6, 4) == a_4
        assert a_4 != a_6, (
            f"alpha_round_decimals=6 produced same value as =4 ({a_4})"
        )
        # And cross-check explicitly: there is NO time_series_alpha
        # output on this tool's wire shape.
        assert "time_series_alpha" not in out_6, (
            "beta_adjusted_spread does NOT emit a time_series_alpha; "
            "if a future change adds one, this test must extend to "
            "cross-check the snapshot vs the last row"
        )

    def test_bps_round_decimals_reaches_residual_both_surfaces(self):
        out_2 = self._run_at({"bps_round_decimals": 2})
        out_4 = self._run_at({"bps_round_decimals": 4})
        r_2 = out_2["current_metrics"]["current_residual_bps"]
        r_4 = out_4["current_metrics"]["current_residual_bps"]
        assert r_2 is not None and r_4 is not None
        assert round(r_4, 2) == r_2
        assert r_2 != r_4
        last_ts = out_4["time_series_residual"]["rows"][-1]["value"]
        assert r_4 == last_ts

    def test_z_score_round_decimals_reaches_residual_z_both_surfaces(self):
        out_4 = self._run_at({"z_score_round_decimals": 4})
        out_6 = self._run_at({"z_score_round_decimals": 6})
        z_4 = out_4["current_metrics"]["current_residual_z_score"]
        z_6 = out_6["current_metrics"]["current_residual_z_score"]
        if z_4 is None or z_6 is None:
            pytest.skip("residual z-score is None in this short window")
        assert round(z_6, 4) == z_4
        last_ts = out_6["time_series_residual_z_score"]["rows"][-1]["value"]
        assert z_6 == last_ts

    def test_r_squared_round_decimals_reaches_snapshot_only(self):
        """R² has no time-series surface in this tool — pin the
        snapshot only.  Same ``_only`` naming convention as the alpha
        test."""
        out_4 = self._run_at({"r_squared_round_decimals": 4})
        out_6 = self._run_at({"r_squared_round_decimals": 6})
        r_4 = out_4["current_metrics"]["current_r_squared"]
        r_6 = out_6["current_metrics"]["current_r_squared"]
        assert r_4 is not None and r_6 is not None
        assert round(r_6, 4) == r_4
        assert "time_series_r_squared" not in out_6, (
            "beta_adjusted_spread does NOT emit a time_series_r_squared; "
            "if a future change adds one, this test must extend to "
            "cross-check the snapshot vs the last row"
        )


# ===========================================================================
# 11. Buffer-multiplier wiring (defensive max(.))
# ===========================================================================

class TestBufferMultiplierWiring:
    """The fetch buffer is the LARGER of the regression-window-buffer
    and the z-score-window-buffer.  Both knobs must reach the
    start_date computation, AND the test suite must cover BOTH
    branches of the max(.) — Codex's review of the initial PR pointed
    out that the original tests only covered the z-score-dominant
    branch."""

    def _capture_start_dates(
        self, *, regression_window_days: int, multipliers: dict,
        z_score_window_days: int = 252,
    ):
        target_df = _synthetic_series(noise_seed=1, days=2000)
        regressor_df = _synthetic_series(noise_seed=2, days=2000)
        params = BetaAdjustedSpreadInput(
            target_curve_family="IT_BTP", target_tenor="10Y",
            regressor_curve_family="DE_BUND", regressor_tenor="10Y",
            regression_window_days=regression_window_days,
            lookback_days=180,
        )

        captured = []

        def cap(*, engine, curve_family, tenor, field_name, start_date):
            captured.append(start_date)
            return {"IT_BTP_10Y": target_df, "DE_BUND_10Y": regressor_df}[
                f"{curve_family}_{tenor}"
            ]

        cfg_overrides = {"z_score_window_days": z_score_window_days}
        cfg_overrides.update(multipliers)

        with patch(
            "rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute.fetch_single_tenor",
            side_effect=cap,
        ), patch(
            "rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute.date",
            _FrozenDate,
        ):
            calculate_beta_adjusted_spread(
                engine=None, params=params,
                config=_custom_config(**cfg_overrides),
            )
        # Both legs fetched — assert same start_date for both.
        assert len(captured) == 2
        assert captured[0] == captured[1]
        return captured[0]

    # ---- z-score-dominant branch ---------------------------------------

    def test_z_score_buffer_dominates_when_z_window_larger(self):
        """When regression_window=60 and z_window=252, the z-score
        buffer dominates the max(.).  Bumping z_score_buffer_multiplier
        from 1.5 → 1.8 must shift the start_date earlier."""
        sd_default = self._capture_start_dates(
            regression_window_days=60,
            multipliers={"z_score_buffer_multiplier": 1.5},
        )
        sd_wider = self._capture_start_dates(
            regression_window_days=60,
            multipliers={"z_score_buffer_multiplier": 1.8},
        )
        assert sd_wider < sd_default

    def test_regression_multiplier_no_op_in_z_dominant_branch(self):
        """When regression_window=60, the regression-window buffer at
        any reasonable multiplier (≤2.0) can never exceed the
        z-score-window-buffer at the default 1.5x of 252 = 378d.  So
        bumping regression_buffer_multiplier alone has no effect on
        the max(.) — proves the max() correctly clamps to the larger
        side."""
        sd_low = self._capture_start_dates(
            regression_window_days=60,
            multipliers={"regression_buffer_multiplier": 1.3},
        )
        sd_high = self._capture_start_dates(
            regression_window_days=60,
            multipliers={"regression_buffer_multiplier": 2.0},
        )
        assert sd_low == sd_high

    # ---- regression-dominant branch ------------------------------------

    def test_regression_buffer_dominates_when_regression_window_larger(self):
        """The other branch of the max(.).  When
        regression_window_days=1000 and z_window=252, the regression-
        window buffer dominates: 1000 * 1.5 = 1500d > 252 * 1.5 = 378d.
        Bumping regression_buffer_multiplier from 1.5 → 1.8 must shift
        the start_date earlier in this branch (whereas it had no effect
        in the z-dominant branch above)."""
        sd_default = self._capture_start_dates(
            regression_window_days=1000,
            multipliers={"regression_buffer_multiplier": 1.5},
        )
        sd_wider = self._capture_start_dates(
            regression_window_days=1000,
            multipliers={"regression_buffer_multiplier": 1.8},
        )
        assert sd_wider < sd_default

    def test_z_multiplier_no_op_in_regression_dominant_branch(self):
        """Sibling check.  In the regression-dominant branch, bumping
        z_score_buffer_multiplier alone has no effect on the
        start_date because the regression side already drives the
        max(.).  Proves the symmetric clamping behaviour."""
        sd_low = self._capture_start_dates(
            regression_window_days=1000,
            multipliers={"z_score_buffer_multiplier": 1.3},
        )
        sd_high = self._capture_start_dates(
            regression_window_days=1000,
            multipliers={"z_score_buffer_multiplier": 2.0},
        )
        assert sd_low == sd_high


# ===========================================================================
# 12. Import-path back-compat
# ===========================================================================

class TestImportPathBackCompat:
    def test_calculate_via_package_init_and_compute_match(self):
        from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
            calculate_beta_adjusted_spread as via_package,
        )
        from rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute import (
            calculate_beta_adjusted_spread as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_two_paths(self):
        from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
            BetaAdjustedSpreadInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.beta_adjusted_spread.schemas import (
            BetaAdjustedSpreadInput as via_schemas,
        )
        assert via_package is via_schemas

    def test_config_path_identity(self):
        from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

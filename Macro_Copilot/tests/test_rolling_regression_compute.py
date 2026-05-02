"""
test_rolling_regression_compute.py — Unit tests for the
rolling_regression tool.

Covers:
  1. Bundled config.yaml is structurally valid + loads cleanly + has
     ``category=quant_standard_analytic``.
  2. compute() runs end-to-end against synthetic input and returns a
     well-formed output (snapshot + 5 TimeSeries payloads).
  3. Central knob (``regression_window_days``) overrides actually
     change the output; YAML-locked knobs cannot be overridden via
     input.
  4. Honest-placeholder guards: NotImplementedError on
     ``add_constant=False`` and on a non-default
     ``regression_solver``.
  5. Cross-layer guard: regression_window_days < regression_min_periods
     returns a controlled error envelope (FastAPI maps this to 422
     via the user_input_phrases path).
  6. Schema-layer behaviour: target ≠ regressor (curve_family, tenor)
     enforced; window bounds enforced; field_name sentinel works.
  7. Numerical correctness: synthetic y = a + b*x + small noise
     recovers a, b within tolerance.
  8. Quality flag fires for multicollinearity (two identical
     regressors) and coefficients are suppressed.
  9. Boundary rounding: every YAML rounding knob (beta, alpha,
     residual, r_squared) reaches both the snapshot AND the
     time_series rows.
  10. Three import paths still resolve to the same Pydantic class.

Tests are fully offline — fetch is mocked, today is frozen.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import math
import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.rolling_regression import (
    CONFIG_PATH,
    RollingRegressionInput,
    calculate_rolling_regression,
)
from shared.config import (
    Convention,
    MethodologyMeta,
    ToolConfig,
    ToolMeta,
    clear_tool_config_cache,
    load_tool_config,
)
from shared.schemas import SeriesSpec, TimeSeriesUnits


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
    """Build a single-tenor long-format DataFrame matching the shape
    fetch_single_tenor returns.  Used to build synthetic target +
    regressor series for the rolling_regression tests."""
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
    """Build a ToolConfig from defaults + any overrides.  Defaults
    mirror config.yaml exactly so tests that override one knob
    measure a single-variable change."""
    defaults = {
        "regression_min_periods": 30,
        "add_constant": True,
        "regression_solver": "numpy_lstsq_default",
        "condition_number_warning_threshold": 1e10,
        "regression_buffer_multiplier": 1.5,
        "ffill_limit_days": 5,
        "beta_round_decimals": 4,
        "alpha_round_decimals": 4,
        "residual_round_decimals": 4,
        "r_squared_round_decimals": 4,
        "default_field_name": "YLD_YTM_MID",
    }
    defaults.update(overrides)
    return ToolConfig(
        tool=ToolMeta(
            name="t",
            domain="d",
            description="x",
            category="quant_standard_analytic",
        ),
        methodology=MethodologyMeta(what_it_does="x"),
        conventions={
            k: Convention(value=v, source="test", rationale="test")
            for k, v in defaults.items()
        },
    )


def _run(params, fetched_series_by_label: dict, config=None):
    """Mock fetch_single_tenor to return one of the synthetic
    DataFrames keyed by ``"{curve_family}_{tenor}"``."""

    def fake_fetch(*, engine, curve_family, tenor, field_name, start_date):
        key = f"{curve_family}_{tenor}"
        if key not in fetched_series_by_label:
            raise AssertionError(f"unexpected fetch for {key}")
        return fetched_series_by_label[key]

    with patch(
        "rates_agent.sovereign_bonds.tools.rolling_regression.compute.fetch_single_tenor",
        side_effect=fake_fetch,
    ), patch(
        "rates_agent.sovereign_bonds.tools.rolling_regression.compute.date",
        _FrozenDate,
    ):
        return calculate_rolling_regression(
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
        assert cfg.tool.name == "rolling_regression_tool"
        assert cfg.tool.domain == "sovereign_bonds"

    def test_category_is_quant_standard_analytic(self):
        cfg = load_tool_config(CONFIG_PATH)
        # rolling_regression is a textbook quant primitive — universal
        # mathematics, but configuration must be specified before use.
        assert cfg.tool.category == "quant_standard_analytic"

    def test_required_conventions_present(self):
        cfg = load_tool_config(CONFIG_PATH)
        required = {
            "regression_min_periods",
            "add_constant",
            "regression_solver",
            "condition_number_warning_threshold",
            "regression_buffer_multiplier",
            "ffill_limit_days",
            "beta_round_decimals",
            "alpha_round_decimals",
            "residual_round_decimals",
            "r_squared_round_decimals",
            "default_field_name",
        }
        missing = required - set(cfg.conventions.keys())
        assert not missing, f"missing: {sorted(missing)}"

    def test_no_regression_window_days_in_yaml(self):
        """``regression_window_days`` is the user's central knob and must
        NOT live in YAML."""
        cfg = load_tool_config(CONFIG_PATH)
        assert "regression_window_days" not in cfg.conventions

    def test_convention_defaults(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert cfg.convention_value("regression_min_periods") == 30
        assert cfg.convention_value("add_constant") is True
        assert cfg.convention_value("regression_solver") == "numpy_lstsq_default"
        assert cfg.convention_value("condition_number_warning_threshold") == 1e10
        assert cfg.convention_value("regression_buffer_multiplier") == 1.5
        assert cfg.convention_value("ffill_limit_days") == 5
        assert cfg.convention_value("default_field_name") == "YLD_YTM_MID"

    def test_methodology_planned_extensions_populated(self):
        cfg = load_tool_config(CONFIG_PATH)
        assert len(cfg.methodology.planned_extensions) >= 3
        joined = " | ".join(cfg.methodology.planned_extensions)
        assert "no-intercept" in joined.lower() or "robust" in joined.lower()


# ===========================================================================
# 2. End-to-end happy path
# ===========================================================================

class TestComputeHappyPath:
    def test_default_config_returns_well_formed_output(self):
        # Two distinct synthetic series — y modestly correlated with x
        target_df = _synthetic_series(noise_seed=1, drift=0.5)
        regressor_df = _synthetic_series(noise_seed=2, drift=0.3)
        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=120,
            lookback_days=180,
        )
        out = _run(
            params,
            {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df},
        )
        assert "error" not in out, out.get("error")
        cm = out["current_metrics"]
        for k in (
            "as_of_date", "target_label", "regressor_labels",
            "current_alpha_pct", "current_betas", "current_residual_pct",
            "current_r_squared", "current_condition_flag",
            "regression_window_days_used", "regression_min_periods_used",
            "add_constant_used", "observation_count",
        ):
            assert k in cm, f"missing {k}"

        assert cm["target_label"] == "UST_10Y"
        assert cm["regressor_labels"] == ["DE_BUND_10Y"]
        assert cm["regression_window_days_used"] == 120
        assert cm["regression_min_periods_used"] == 30
        assert cm["add_constant_used"] is True
        assert cm["observation_count"] > 0
        assert cm["current_condition_flag"] == 0
        assert "DE_BUND_10Y" in cm["current_betas"]

        # TimeSeries payloads
        assert len(out["time_series_betas"]) == 1
        beta_ts = out["time_series_betas"][0]
        assert beta_ts["units"] == TimeSeriesUnits.RATIO.value
        assert beta_ts["series_name"] == "UST_10Y_on_DE_BUND_10Y_beta_120d"
        assert len(beta_ts["rows"]) == cm["observation_count"]

        assert out["time_series_alpha"]["units"] == TimeSeriesUnits.PERCENT.value
        assert out["time_series_residual"]["units"] == TimeSeriesUnits.PERCENT.value
        assert out["time_series_r_squared"]["units"] == TimeSeriesUnits.RATIO.value
        assert out["time_series_condition_flag"]["units"] == TimeSeriesUnits.COUNT.value

    def test_explicit_config_matches_auto_loaded(self):
        target_df = _synthetic_series(noise_seed=1, drift=0.5)
        regressor_df = _synthetic_series(noise_seed=2, drift=0.3)
        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=120,
            lookback_days=180,
        )
        out_auto = _run(
            params,
            {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df},
            config=None,
        )
        out_explicit = _run(
            params,
            {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df},
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
        p_60 = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=60,
            lookback_days=180,
        )
        p_252 = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=252,
            lookback_days=180,
        )
        out_60 = _run(p_60, {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df})
        out_252 = _run(p_252, {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df})

        assert out_60["current_metrics"]["current_betas"]["DE_BUND_10Y"] != \
            out_252["current_metrics"]["current_betas"]["DE_BUND_10Y"]
        assert out_60["current_metrics"]["regression_window_days_used"] == 60
        assert out_252["current_metrics"]["regression_window_days_used"] == 252


# ===========================================================================
# 4. Honest-placeholder guards
# ===========================================================================

class TestHonestPlaceholderGuards:
    def test_add_constant_false_raises_not_implemented(self):
        target_df = _synthetic_series()
        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=60,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                {"UST_10Y": target_df, "DE_BUND_10Y": _synthetic_series(noise_seed=2)},
                config=_custom_config(add_constant=False),
            )
        msg = str(exc_info.value)
        assert "add_constant" in msg
        assert "planned_extensions" in msg

    def test_unsupported_solver_raises_not_implemented(self):
        target_df = _synthetic_series()
        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=60,
        )
        with pytest.raises(NotImplementedError) as exc_info:
            _run(
                params,
                {"UST_10Y": target_df, "DE_BUND_10Y": _synthetic_series(noise_seed=2)},
                config=_custom_config(regression_solver="scipy_qr"),
            )
        msg = str(exc_info.value)
        assert "regression_solver" in msg
        assert "scipy_qr" in msg


# ===========================================================================
# 5. Cross-layer small-window guard
# ===========================================================================

class TestSmallWindowGuard:
    def test_window_below_min_periods_returns_controlled_error(self):
        target_df = _synthetic_series()
        regressor_df = _synthetic_series(noise_seed=2)
        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=20,  # < default min_periods=30
            lookback_days=120,
        )
        out = _run(params, {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df})
        assert "error" in out
        assert "is smaller than the YAML's" in out["error"]
        assert "regression_min_periods" in out["error"]
        # Phrase shape matches detail.py's user_input_phrases for 422.
        assert "is smaller than the yaml" in out["error"].lower()

    def test_yaml_lower_min_periods_unblocks_smaller_window(self):
        target_df = _synthetic_series()
        regressor_df = _synthetic_series(noise_seed=2)
        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=20,
            lookback_days=120,
        )
        out = _run(
            params,
            {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df},
            config=_custom_config(regression_min_periods=15),
        )
        # With min_periods=15, window=20 is fine.
        assert "error" not in out, out.get("error")


# ===========================================================================
# 6. Schema-layer behaviour
# ===========================================================================

class TestSchemaBehaviour:
    def test_target_equals_regressor_rejected(self):
        with pytest.raises(Exception) as exc_info:
            RollingRegressionInput(
                target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                regressor_specs=[SeriesSpec(curve_family="UST", tenor="10Y")],
                regression_window_days=60,
            )
        assert "same" in str(exc_info.value).lower() or \
               "degenerate" in str(exc_info.value).lower()

    def test_field_name_difference_does_not_save_collision(self):
        """Even with different field_name, target == regressor by
        (curve_family, tenor) is rejected."""
        with pytest.raises(Exception):
            RollingRegressionInput(
                target_spec=SeriesSpec(
                    curve_family="UST", tenor="10Y", field_name="YLD_YTM_MID"),
                regressor_specs=[SeriesSpec(
                    curve_family="UST", tenor="10Y", field_name="YLD_BID")],
                regression_window_days=60,
            )

    def test_window_bounds_enforced(self):
        with pytest.raises(Exception):
            RollingRegressionInput(
                target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
                regression_window_days=5,  # < ge=10
            )
        with pytest.raises(Exception):
            RollingRegressionInput(
                target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
                regression_window_days=3000,  # > le=2520
            )

    def test_at_least_one_regressor_required(self):
        with pytest.raises(Exception):
            RollingRegressionInput(
                target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                regressor_specs=[],
                regression_window_days=60,
            )

    def test_field_name_default_is_none_in_seriesspec(self):
        spec = SeriesSpec(curve_family="UST", tenor="10Y")
        assert spec.field_name is None


# ===========================================================================
# 7. Numerical correctness
# ===========================================================================

class TestNumericalCorrectness:
    def test_recovers_planted_coefficients(self):
        """Plant y = 0.5 + 1.5 * x + small noise.  Latest beta should
        be close to 1.5; latest alpha close to 0.5."""
        rng = np.random.default_rng(42)
        frozen_today = date(2026, 4, 30)
        bdays = pd.bdate_range(frozen_today - timedelta(days=600), frozen_today)
        bdays = bdays[-300:]
        n = len(bdays)
        x = np.linspace(2.0, 5.0, n)
        noise = rng.normal(0, 0.005, n)
        y = 0.5 + 1.5 * x + noise

        target_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": y,
        })
        regressor_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": x,
        })

        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=120,
            lookback_days=120,
        )
        out = _run(
            params,
            {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df},
        )
        beta = out["current_metrics"]["current_betas"]["DE_BUND_10Y"]
        alpha = out["current_metrics"]["current_alpha_pct"]
        r2 = out["current_metrics"]["current_r_squared"]
        assert abs(beta - 1.5) < 0.05, f"beta={beta}"
        assert abs(alpha - 0.5) < 0.1, f"alpha={alpha}"
        assert r2 > 0.99, f"r2={r2}"


# ===========================================================================
# 8. Quality flag — multicollinearity
# ===========================================================================

class TestConditionFlag:
    def test_perfectly_collinear_regressors_set_flag_and_suppress_betas(self):
        """Two regressors that are EXACTLY identical produce a singular
        design matrix; the condition-number gate must fire and emit
        NaN coefficients with flag=1."""
        target_df = _synthetic_series(noise_seed=1)
        x_df = _synthetic_series(noise_seed=2)
        # Second regressor is exactly the same series, but to the
        # schema layer it's a different (curve_family, tenor) so it
        # passes input validation.  Multicollinearity is detected by
        # the condition-number gate inside rolling_ols.
        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[
                SeriesSpec(curve_family="DE_BUND", tenor="10Y"),
                SeriesSpec(curve_family="DE_BUND", tenor="20Y"),
            ],
            regression_window_days=120,
            lookback_days=120,
        )
        # Same DataFrame returned for both regressors → identical
        # columns at panel-build time → singular X.
        out = _run(
            params,
            {"UST_10Y": target_df, "DE_BUND_10Y": x_df, "DE_BUND_20Y": x_df},
        )
        assert "error" not in out, out.get("error")
        # Latest row's flag is 1 — design matrix singular.
        assert out["current_metrics"]["current_condition_flag"] == 1
        # Coefficients suppressed.
        assert out["current_metrics"]["current_alpha_pct"] is None
        assert out["current_metrics"]["current_betas"]["DE_BUND_10Y"] is None
        assert out["current_metrics"]["current_betas"]["DE_BUND_20Y"] is None
        assert out["current_metrics"]["current_residual_pct"] is None
        assert out["current_metrics"]["current_r_squared"] is None


# ===========================================================================
# 9. Boundary rounding
# ===========================================================================

class TestBoundaryRounding:
    """Each YAML rounding knob must reach BOTH the snapshot AND the
    time_series rows.  Same boundary-shadowing class as the
    field_name and z_score_round_decimals fixes.

    Codex's review of the initial PR pointed out that only
    beta_round_decimals had an explicit precision-propagation test,
    while alpha / residual / r_squared were untested even though the
    class docstring claimed otherwise.  This expanded class pins all
    four rounding knobs end-to-end.
    """

    def _planted_panel(self):
        """Build a synthetic panel where every output (beta, alpha,
        residual, R²) has trailing precision beyond 4 decimals so
        decimals=6 vs decimals=4 actually differ."""
        rng = np.random.default_rng(7)
        frozen_today = date(2026, 4, 30)
        bdays = pd.bdate_range(frozen_today - timedelta(days=600), frozen_today)
        bdays = bdays[-300:]
        n = len(bdays)
        x = np.linspace(2.0, 5.0, n)
        # Non-round multipliers + small noise → trailing precision.
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
        return RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=120,
            lookback_days=120,
        )

    def _run_at(self, decimals_kw: dict):
        target_df, regressor_df = self._planted_panel()
        return _run(
            self._params(),
            {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df},
            config=_custom_config(**decimals_kw),
        )

    def test_beta_round_decimals_reaches_both_surfaces(self):
        rng = np.random.default_rng(7)
        frozen_today = date(2026, 4, 30)
        bdays = pd.bdate_range(frozen_today - timedelta(days=600), frozen_today)
        bdays = bdays[-300:]
        n = len(bdays)
        x = np.linspace(2.0, 5.0, n)
        # Add a non-round multiplier so beta has trailing precision
        y = 0.123456 + 1.234567 * x + rng.normal(0, 0.002, n)

        target_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": y,
        })
        regressor_df = pd.DataFrame({
            "trade_date": [d.date() for d in bdays],
            "field_value": x,
        })

        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=120,
            lookback_days=120,
        )
        out_4 = _run(
            params,
            {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df},
            config=_custom_config(beta_round_decimals=4),
        )
        out_6 = _run(
            params,
            {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df},
            config=_custom_config(beta_round_decimals=6),
        )
        beta_4 = out_4["current_metrics"]["current_betas"]["DE_BUND_10Y"]
        beta_6 = out_6["current_metrics"]["current_betas"]["DE_BUND_10Y"]
        assert beta_4 is not None and beta_6 is not None
        assert round(beta_6, 4) == beta_4
        # Strong: at decimals=6 the snapshot value should differ from
        # decimals=4 (synthetic noise + non-round multiplier guarantees
        # sub-4-decimal precision is observable).
        assert beta_4 != beta_6, (
            f"beta_round_decimals=6 produced same value as =4 ({beta_4}); "
            "boundary rounding may not be reaching the snapshot."
        )

        # And cross-check: snapshot at decimals=6 matches the LAST row
        # of the time_series at decimals=6, proving both surfaces use
        # the same rounding.
        last_ts = out_6["time_series_betas"][0]["rows"][-1]["value"]
        assert beta_6 == last_ts, (
            f"snapshot beta {beta_6} disagrees with last time_series "
            f"row {last_ts}"
        )

    def test_alpha_round_decimals_reaches_both_surfaces(self):
        out_4 = self._run_at({"alpha_round_decimals": 4})
        out_6 = self._run_at({"alpha_round_decimals": 6})
        a_4 = out_4["current_metrics"]["current_alpha_pct"]
        a_6 = out_6["current_metrics"]["current_alpha_pct"]
        assert a_4 is not None and a_6 is not None
        assert round(a_6, 4) == a_4
        assert a_4 != a_6, (
            f"alpha_round_decimals=6 produced same value as =4 ({a_4}); "
            "boundary rounding may not be reaching the snapshot."
        )
        last_ts = out_6["time_series_alpha"]["rows"][-1]["value"]
        assert a_6 == last_ts, (
            f"snapshot alpha {a_6} disagrees with last time_series "
            f"row {last_ts}"
        )

    def test_residual_round_decimals_reaches_both_surfaces(self):
        out_4 = self._run_at({"residual_round_decimals": 4})
        out_6 = self._run_at({"residual_round_decimals": 6})
        r_4 = out_4["current_metrics"]["current_residual_pct"]
        r_6 = out_6["current_metrics"]["current_residual_pct"]
        assert r_4 is not None and r_6 is not None
        assert round(r_6, 4) == r_4
        assert r_4 != r_6, (
            f"residual_round_decimals=6 produced same value as =4 ({r_4}); "
            "boundary rounding may not be reaching the snapshot."
        )
        last_ts = out_6["time_series_residual"]["rows"][-1]["value"]
        assert r_6 == last_ts, (
            f"snapshot residual {r_6} disagrees with last time_series "
            f"row {last_ts}"
        )

    def test_r_squared_round_decimals_reaches_both_surfaces(self):
        out_4 = self._run_at({"r_squared_round_decimals": 4})
        out_6 = self._run_at({"r_squared_round_decimals": 6})
        rsq_4 = out_4["current_metrics"]["current_r_squared"]
        rsq_6 = out_6["current_metrics"]["current_r_squared"]
        assert rsq_4 is not None and rsq_6 is not None
        assert round(rsq_6, 4) == rsq_4
        assert rsq_4 != rsq_6, (
            f"r_squared_round_decimals=6 produced same value as =4 "
            f"({rsq_4}); boundary rounding may not be reaching the "
            "snapshot."
        )
        last_ts = out_6["time_series_r_squared"]["rows"][-1]["value"]
        assert rsq_6 == last_ts, (
            f"snapshot r_squared {rsq_6} disagrees with last time_series "
            f"row {last_ts}"
        )


class TestBufferMultiplierIsConfigDriven:
    """The fetch buffer multiplier is a CALIBRATION knob, not a
    structural choice — different desks could reasonably tune it.
    Codex's review of the initial PR caught it as hard-coded; the
    follow-up promoted it to YAML.  This test pins the wiring:
    overriding the YAML value changes the start_date passed to
    fetch_single_tenor."""

    def test_buffer_multiplier_changes_fetch_start_date(self):
        target_df = _synthetic_series(noise_seed=1, drift=0.5)
        regressor_df = _synthetic_series(noise_seed=2, drift=0.3)
        params = RollingRegressionInput(
            target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
            regressor_specs=[SeriesSpec(curve_family="DE_BUND", tenor="10Y")],
            regression_window_days=120,
            lookback_days=180,
        )

        captured_start_dates = []

        def capture_fetch(*, engine, curve_family, tenor, field_name, start_date):
            captured_start_dates.append(start_date)
            key = f"{curve_family}_{tenor}"
            return {"UST_10Y": target_df, "DE_BUND_10Y": regressor_df}[key]

        for multiplier in (1.3, 1.8):
            captured_start_dates.clear()
            with patch(
                "rates_agent.sovereign_bonds.tools.rolling_regression.compute.fetch_single_tenor",
                side_effect=capture_fetch,
            ), patch(
                "rates_agent.sovereign_bonds.tools.rolling_regression.compute.date",
                _FrozenDate,
            ):
                calculate_rolling_regression(
                    engine=None,
                    params=params,
                    config=_custom_config(regression_buffer_multiplier=multiplier),
                )
            assert len(captured_start_dates) == 2
            # Both fetches use the SAME start_date for the same call.
            assert captured_start_dates[0] == captured_start_dates[1]

            # Compute the expected start_date using the multiplier.
            expected_buffer_days = int(120 * multiplier)
            expected_start = _FrozenDate.today() - timedelta(
                days=180 + expected_buffer_days
            )
            assert captured_start_dates[0] == expected_start, (
                f"multiplier={multiplier}: expected start_date "
                f"{expected_start}, got {captured_start_dates[0]}"
            )


# ===========================================================================
# 10. Import-path back-compat
# ===========================================================================

class TestImportPathBackCompat:
    def test_calculate_via_package_init_and_compute_match(self):
        from rates_agent.sovereign_bonds.tools.rolling_regression import (
            calculate_rolling_regression as via_package,
        )
        from rates_agent.sovereign_bonds.tools.rolling_regression.compute import (
            calculate_rolling_regression as via_compute,
        )
        assert via_package is via_compute

    def test_input_schema_via_two_paths(self):
        from rates_agent.sovereign_bonds.tools.rolling_regression import (
            RollingRegressionInput as via_package,
        )
        from rates_agent.sovereign_bonds.tools.rolling_regression.schemas import (
            RollingRegressionInput as via_schemas,
        )
        assert via_package is via_schemas

    def test_config_path_identity(self):
        from rates_agent.sovereign_bonds.tools.rolling_regression import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.rolling_regression.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

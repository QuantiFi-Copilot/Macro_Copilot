"""tests/test_operator_rolling_regression.py — Phase 2A operator.

Covers the relationship-analysis surface of the
``regime_conditioned_relationship`` archetype:

  1. Bundled config loads + name-checks; defaults match design.
  2. Schema validators: window >= min_periods, lhs_basis == rhs_basis.
  3. Happy path: lhs = (a*rhs + b + noise), level_change basis →
     beta should converge to ``a`` (within tolerance) on stable
     synthetic data.
  4. Output SeriesSet shape: keys = {beta, alpha, r_squared}, units
     = {RATIO, effective_lhs_unit, RATIO}, common_index = panel index.
  5. PERCENT × level_change → BPS unit transition for alpha.
  6. raw_value basis vs level_change basis produce DIFFERENT betas.
  7. Cross-calendar inner-join: indexes auto-align via intersection.
  8. Empty intersection refusal with clear diagnostic.
  9. Lineage: set head = rolling_regression step; rhs lineage in
     auxiliary_lineages; per-key upstream_lineage propagated.
 10. Registry entry registered.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from shared.artifacts.lineage import Lineage, OperatorStep, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Series, SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfigError,
    load_operator_config,
)
from shared.operators.rolling_regression import (
    CONFIG_PATH,
    rolling_regression,
    RollingRegressionError,
    RollingRegressionParams,
)
from shared.workflow.registry import OPERATOR_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _primitive_lineage(series_key: str) -> Lineage:
    step = PrimitiveStep.build(
        name="synthetic_primitive",
        version="1.0.0",
        params={"series_key": series_key},
        tool_config_hash="test_config_hash",
        output_field="time_series",
        as_of_date="2025-01-01",
    )
    return Lineage.from_steps([step])


def _make_series(
    *,
    series_key: str,
    values: pd.Series,
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    frequency: str = "B",
) -> Series:
    return Series(
        series_key=series_key,
        payload=values,
        units=units,
        frequency=frequency,
        missingness_policy=RawNoCleaning(),
        lineage=_primitive_lineage(series_key),
    )


def _synthetic_pair_with_known_beta(
    *,
    n: int = 600,
    true_beta: float = 1.5,
    true_alpha_pct: float = 0.10,
    rhs_drift: float = 0.5,
    noise: float = 0.005,
    seed: int = 11,
):
    """Build (lhs, rhs) Series so that lhs CHANGE = beta * rhs CHANGE
    + alpha (in PERCENT space).  Both PERCENT-typed.  ``level_change``
    rolling regression of lhs on rhs should converge close to
    ``true_beta`` once the warmup is past.
    """
    rs = np.random.RandomState(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    rhs_vals = np.linspace(4.0, 4.0 + rhs_drift, n) + rs.randn(n) * 0.005
    rhs_diff = np.diff(rhs_vals, prepend=rhs_vals[0])
    lhs_vals = (
        true_alpha_pct
        + np.cumsum(true_beta * rhs_diff)
        + rs.randn(n) * noise
        + 4.30
    )
    lhs = _make_series(
        series_key="lhs",
        values=pd.Series(lhs_vals, index=idx, name="lhs"),
        units=TimeSeriesUnits.PERCENT,
    )
    rhs = _make_series(
        series_key="rhs",
        values=pd.Series(rhs_vals, index=idx, name="rhs"),
        units=TimeSeriesUnits.PERCENT,
    )
    return lhs, rhs


# ===========================================================================
# 1. Bundled config
# ===========================================================================


class TestBundledConfig:
    def test_config_yaml_exists(self):
        assert CONFIG_PATH.is_file()

    def test_config_loads_with_correct_name(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.operator.name == "rolling_regression"
        assert cfg.operator.method_family == "aggregation"

    def test_required_defaults_present(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.default_value("min_periods") == 30
        assert cfg.default_value("add_constant") is True
        assert cfg.default_value("lhs_basis") == "level_change"
        assert cfg.default_value("rhs_basis") == "level_change"


# ===========================================================================
# 2. Schema validators
# ===========================================================================


class TestSchemaValidators:
    def test_window_must_exceed_min_periods(self):
        with pytest.raises(ValueError, match=r"window \(\d+\) must be >="):
            RollingRegressionParams(window=20, min_periods=30)

    def test_lhs_and_rhs_basis_must_match(self):
        with pytest.raises(ValueError, match="lhs_basis == rhs_basis"):
            RollingRegressionParams(
                window=60, min_periods=30,
                lhs_basis="level_change",
                rhs_basis="raw_value",
            )

    def test_min_window_is_2(self):
        with pytest.raises(ValueError, match="greater than or equal"):
            RollingRegressionParams(window=1, min_periods=1)


# ===========================================================================
# 3. Happy-path beta recovery
# ===========================================================================


class TestBetaRecovery:
    def test_converges_to_true_beta_on_synthetic_data(self):
        lhs, rhs = _synthetic_pair_with_known_beta(
            n=600, true_beta=1.5, noise=0.001,
        )
        out = rolling_regression(
            lhs, rhs,
            params=RollingRegressionParams(
                window=120, min_periods=30,
                lhs_basis="level_change", rhs_basis="level_change",
            ),
        )
        # The last (well-warmed-up) beta should be close to 1.5.
        beta = out.series_by_key["beta"]
        last_beta = beta.dropna().iloc[-1]
        assert 1.45 < last_beta < 1.55, (
            f"beta failed to converge to 1.5 (got {last_beta:.4f})"
        )

    def test_beta_recovers_when_true_beta_is_negative(self):
        lhs, rhs = _synthetic_pair_with_known_beta(
            n=400, true_beta=-0.7, noise=0.001,
        )
        out = rolling_regression(
            lhs, rhs,
            params=RollingRegressionParams(
                window=80, min_periods=30,
                lhs_basis="level_change", rhs_basis="level_change",
            ),
        )
        last_beta = out.series_by_key["beta"].dropna().iloc[-1]
        assert -0.8 < last_beta < -0.6, (
            f"negative beta failed to converge (got {last_beta:.4f})"
        )


# ===========================================================================
# 4. Output SeriesSet shape
# ===========================================================================


class TestSeriesSetShape:
    def test_keys_are_beta_alpha_r_squared(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
            ),
        )
        assert isinstance(out, SeriesSet)
        assert set(out.series_by_key.keys()) == {
            "beta", "alpha", "r_squared",
        }

    def test_beta_units_are_RATIO(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
            ),
        )
        assert out.units_by_key["beta"] == TimeSeriesUnits.RATIO

    def test_r_squared_units_are_RATIO(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
            ),
        )
        assert out.units_by_key["r_squared"] == TimeSeriesUnits.RATIO

    def test_alpha_units_are_BPS_for_PERCENT_inputs_with_level_change(self):
        """PERCENT inputs in level_change mode pick up the
        methodology-owned PERCENT→BPS transition (×100), so alpha is
        BPS — same rule event_windows uses."""
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
                lhs_basis="level_change", rhs_basis="level_change",
            ),
        )
        assert out.units_by_key["alpha"] == TimeSeriesUnits.BPS

    def test_alpha_units_match_lhs_when_basis_is_raw_value(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
                lhs_basis="raw_value", rhs_basis="raw_value",
            ),
        )
        assert out.units_by_key["alpha"] == TimeSeriesUnits.PERCENT

    def test_common_index_matches_panel_after_basis(self):
        """level_change drops the first row, so the SeriesSet's
        common_index has length = len(input) - 1 (the first NaN
        row from .diff() is dropped)."""
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
                lhs_basis="level_change", rhs_basis="level_change",
            ),
        )
        assert len(out.common_index) == len(lhs.payload) - 1


# ===========================================================================
# 5. raw_value vs level_change behavioural difference
# ===========================================================================


class TestBasisModesDiffer:
    def test_raw_value_vs_level_change_produce_different_betas(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=600)
        out_lvl = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=120, min_periods=30,
                lhs_basis="level_change", rhs_basis="level_change",
            ),
        )
        out_raw = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=120, min_periods=30,
                lhs_basis="raw_value", rhs_basis="raw_value",
            ),
        )
        beta_lvl = out_lvl.series_by_key["beta"].dropna().iloc[-1]
        beta_raw = out_raw.series_by_key["beta"].dropna().iloc[-1]
        # The two betas must differ — they measure structurally
        # different quantities.
        assert beta_lvl != beta_raw


# ===========================================================================
# 6. Cross-calendar inner-join
# ===========================================================================


class TestCrossCalendar:
    def test_partial_overlap_aligns_via_intersection(self):
        rs = np.random.RandomState(7)
        idx_lhs = pd.bdate_range("2025-01-01", periods=200)
        idx_rhs = pd.bdate_range("2025-02-01", periods=200)
        lhs = _make_series(
            series_key="lhs",
            values=pd.Series(rs.randn(200) * 0.01 + 4.0, index=idx_lhs),
            units=TimeSeriesUnits.PERCENT,
        )
        rhs = _make_series(
            series_key="rhs",
            values=pd.Series(rs.randn(200) * 0.01 + 4.0, index=idx_rhs),
            units=TimeSeriesUnits.PERCENT,
        )

        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=40, min_periods=30,
            ),
        )
        # Output index is a subset of both inputs' indexes.
        for d in out.common_index:
            assert d in idx_lhs
            assert d in idx_rhs

    def test_zero_overlap_raises(self):
        rs = np.random.RandomState(7)
        idx_lhs = pd.bdate_range("2024-01-01", periods=100)
        idx_rhs = pd.bdate_range("2030-01-01", periods=100)
        lhs = _make_series(
            series_key="lhs",
            values=pd.Series(rs.randn(100) + 4.0, index=idx_lhs),
            units=TimeSeriesUnits.PERCENT,
        )
        rhs = _make_series(
            series_key="rhs",
            values=pd.Series(rs.randn(100) + 4.0, index=idx_rhs),
            units=TimeSeriesUnits.PERCENT,
        )
        with pytest.raises(RollingRegressionError, match="NO dates in common"):
            rolling_regression(
                lhs, rhs, params=RollingRegressionParams(
                    window=40, min_periods=30,
                ),
            )


# ===========================================================================
# 7. Lineage propagation
# ===========================================================================


class TestLineagePropagation:
    def test_set_lineage_head_is_rolling_regression_step(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
            ),
        )
        head = out.lineage.steps[-1]
        assert isinstance(head, OperatorStep)
        assert head.name == "rolling_regression"

    def test_step_records_effective_units(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
                lhs_basis="level_change", rhs_basis="level_change",
            ),
        )
        head = out.lineage.steps[-1]
        # PERCENT × level_change → BPS for both effective units.
        assert head.params["effective_lhs_unit"] == "bps"
        assert head.params["effective_rhs_unit"] == "bps"

    def test_rhs_lineage_in_auxiliary(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
            ),
        )
        head = out.lineage.steps[-1]
        assert len(head.auxiliary_lineages) == 1
        assert (
            head.auxiliary_lineages[0].head_hash
            == rhs.lineage.head_hash
        )

    def test_per_key_upstream_lineage_populated(self):
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
            ),
        )
        # Each output member has an upstream_lineage that propagates
        # the lhs's (target's) chain.
        for k in ("beta", "alpha", "r_squared"):
            assert (
                out.upstream_lineage_by_key[k].head_hash
                == lhs.lineage.head_hash
            )

    def test_get_series_extracts_with_alignment_step_appended(self):
        """SeriesSet.get_series(key) returns a Series whose lineage
        chain is upstream_lineage + this op step (per the SeriesSet
        contract from build plan v5 / R2)."""
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        out = rolling_regression(
            lhs, rhs, params=RollingRegressionParams(
                window=60, min_periods=30,
            ),
        )
        beta = out.get_series("beta")
        names = [step.name for step in beta.lineage.steps]
        assert "rolling_regression" in names
        assert "synthetic_primitive" in names


# ===========================================================================
# 8. Config-discipline parity
# ===========================================================================


class TestConfigDiscipline:
    def test_wrong_config_name_raises(self):
        from shared.operators.align_series import (
            CONFIG_PATH as OTHER_CONFIG_PATH,
        )
        align_cfg = load_operator_config(OTHER_CONFIG_PATH)
        lhs, rhs = _synthetic_pair_with_known_beta(n=400)
        with pytest.raises(OperatorConfigError, match="config name mismatch"):
            rolling_regression(
                lhs, rhs,
                params=RollingRegressionParams(
                    window=60, min_periods=30,
                ),
                config=align_cfg,
            )


# ===========================================================================
# 9. Registry entry
# ===========================================================================


class TestRegistryEntry:
    def test_registered(self):
        assert "rolling_regression" in OPERATOR_REGISTRY

    def test_input_slots(self):
        spec = OPERATOR_REGISTRY["rolling_regression"]
        assert spec.input_slots == {"lhs": "Series", "rhs": "Series"}

    def test_output_type(self):
        spec = OPERATOR_REGISTRY["rolling_regression"]
        assert spec.output_type == "SeriesSet"

    def test_callable_resolves(self):
        spec = OPERATOR_REGISTRY["rolling_regression"]
        assert spec.callable is rolling_regression

    def test_params_class_resolves(self):
        spec = OPERATOR_REGISTRY["rolling_regression"]
        assert spec.params_class is RollingRegressionParams

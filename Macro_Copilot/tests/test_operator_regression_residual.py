"""Tests for shared.operators.regression_residual — Track-A A2 operator.

Covers the contract surface every standard operator must satisfy:

  - happy path vs an independent numpy.linalg.lstsq reference
  - OLS property tests: with an intercept the residuals are mean-zero
    and orthogonal to the regressor
  - OPR2 constant output type (always Series, in LHS units)
  - OPR8 params=None resolves from config and succeeds (head-hash
    equality vs explicit config resolution); schema mirrors YAML
  - OPR9 typed I/O; non-commutativity (lhs↔rhs swap changes output)
  - OPR10 lineage extension with fitted α/β/R²/n_obs diagnostics +
    OPR14 rerun determinism
  - OPR11 strict-by-default frequency + missingness (with opt-outs and
    CombinedMissingnessV1); unit-INVARIANCE across inputs with honest
    LHS-units passthrough on the output
  - OPR13 typed refusals: misaligned index, insufficient overlap,
    zero-variance rhs, ill-conditioned design matrix
  - OPR6 finance-blindness (non-rates synthetic data)
  - composition: the type-gate accepts regression_residual and a DAG
    (incl. the North-Star-adjacent residual→rolling_zscore chain)
    executes end-to-end
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    FetchStep,
    Lineage,
    Series,
    TimeSeriesUnits,
)
from shared.artifacts.missingness import CombinedMissingnessV1
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.regression_residual import (
    CONFIG_PATH,
    RegressionResidualError,
    RegressionResidualParams,
    regression_residual,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


# ===========================================================================
# Helpers
# ===========================================================================


def _series(
    series_key: str,
    *,
    dates,
    values,
    units: TimeSeriesUnits = TimeSeriesUnits.PERCENT,
    frequency=None,
    missingness_policy=None,
) -> Series:
    fetch = FetchStep.build(
        name="fetch_single_tenor", version="1.0.0",
        params={"series_key": series_key},
    )
    adapter = AdapterStep.build(
        name="raw_dataframe_to_artifact_series", version="1.0.0",
        params={"series_key": series_key, "units": units.value},
        input_hashes=(fetch.hash,),
    )
    payload = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=frequency,
        missingness_policy=missingness_policy or CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


_N = 60
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _pair(seed: int = 5, noise: float = 0.5):
    rng = np.random.RandomState(seed)
    xv = rng.randn(_N).cumsum() + 4.0
    yv = 1.5 + 0.8 * xv + rng.randn(_N) * noise
    return (
        _series("y", dates=_DATES, values=yv),
        _series("x", dates=_DATES, values=xv),
        yv,
        xv,
    )


def _lstsq_residuals(yv, xv, *, add_constant=True):
    mask = np.isfinite(yv) & np.isfinite(xv)
    x, y = xv[mask], yv[mask]
    design = (
        np.column_stack([np.ones_like(x), x]) if add_constant
        else x.reshape(-1, 1)
    )
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    alpha, beta = (coef[0], coef[1]) if add_constant else (0.0, coef[0])
    res = yv - (alpha + beta * xv)
    res[~mask] = np.nan
    return res, float(alpha), float(beta)


# ===========================================================================
# 1. Happy path vs independent reference + OLS properties
# ===========================================================================


class TestHappyPath:
    def test_matches_lstsq_reference(self):
        lhs, rhs, yv, xv = _pair()
        out = regression_residual(lhs, rhs)
        expected, _, _ = _lstsq_residuals(yv, xv)
        np.testing.assert_allclose(
            out.payload.to_numpy(), expected, rtol=0, atol=1e-10,
        )
        assert isinstance(out, Series)  # OPR2 constant output type

    def test_residuals_mean_zero_with_intercept(self):
        lhs, rhs, _, _ = _pair()
        out = regression_residual(lhs, rhs)
        assert abs(float(out.payload.mean())) < 1e-9

    def test_residuals_orthogonal_to_regressor(self):
        lhs, rhs, _, xv = _pair()
        out = regression_residual(lhs, rhs)
        res = out.payload.to_numpy()
        assert abs(float(np.dot(res, xv))) < 1e-6

    def test_through_origin_variant(self):
        lhs, rhs, yv, xv = _pair()
        out = regression_residual(
            lhs, rhs, params=RegressionResidualParams(add_constant=False),
        )
        expected, _, _ = _lstsq_residuals(yv, xv, add_constant=False)
        np.testing.assert_allclose(
            out.payload.to_numpy(), expected, rtol=0, atol=1e-10,
        )
        # through-origin residuals are generally NOT mean-zero
        assert abs(float(out.payload.mean())) > 1e-9

    def test_not_commutative(self):
        lhs, rhs, _, _ = _pair()
        out_xy = regression_residual(lhs, rhs)
        out_yx = regression_residual(rhs, lhs)
        assert not np.allclose(
            out_xy.payload.to_numpy(), out_yx.payload.to_numpy(),
        )

    def test_nan_bearing_inputs_pairwise_nan_propagation(self):
        lhs, rhs, yv, xv = _pair()
        yv2, xv2 = yv.copy(), xv.copy()
        yv2[[4, 11]] = np.nan
        xv2[[11, 30]] = np.nan
        lhs2 = _series("y2", dates=_DATES, values=yv2)
        rhs2 = _series("x2", dates=_DATES, values=xv2)
        out = regression_residual(lhs2, rhs2)
        expected, _, _ = _lstsq_residuals(yv2, xv2)
        np.testing.assert_allclose(
            out.payload.to_numpy(), expected, rtol=0, atol=1e-10,
        )
        assert out.payload.isna().sum() == 3  # union of NaN positions

    def test_perfect_fit_gives_zero_residuals(self):
        xv = np.linspace(1.0, 6.0, _N)
        yv = 2.0 + 3.0 * xv
        lhs = _series("py", dates=_DATES, values=yv)
        rhs = _series("px", dates=_DATES, values=xv)
        out = regression_residual(lhs, rhs)
        np.testing.assert_allclose(
            out.payload.to_numpy(), 0.0, atol=1e-9,
        )

    def test_series_key_names_both_inputs(self):
        lhs, rhs, _, _ = _pair()
        out = regression_residual(lhs, rhs)
        assert out.series_key == "residual__y__x"


# ===========================================================================
# 2. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        lhs, rhs, _, _ = _pair()
        out_default = regression_residual(lhs, rhs)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = RegressionResidualParams(
            add_constant=bool(cfg.default_value("add_constant")),
            min_periods=int(cfg.default_value("min_periods")),
            condition_number_threshold=float(
                cfg.default_value("condition_number_threshold")
            ),
        )
        out_explicit = regression_residual(lhs, rhs, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = RegressionResidualParams()
        assert p.add_constant == bool(cfg.default_value("add_constant"))
        assert p.min_periods == int(cfg.default_value("min_periods"))
        assert p.condition_number_threshold == float(
            cfg.default_value("condition_number_threshold")
        )

    def test_min_periods_floor_enforced(self):
        with pytest.raises(ValueError):
            RegressionResidualParams(min_periods=2)


# ===========================================================================
# 3. OPR13 — typed refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        lhs, _, _, _ = _pair()
        with pytest.raises(RegressionResidualError, match="must be Series"):
            regression_residual(lhs, [1, 2, 3])  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        lhs, _, _, _ = _pair()
        other = _series(
            "z", dates=pd.bdate_range("2027-01-04", periods=_N),
            values=list(range(_N)),
        )
        with pytest.raises(
            RegressionResidualError, match="identical DatetimeIndex",
        ):
            regression_residual(lhs, other)

    def test_insufficient_overlap_raises(self):
        yv = np.full(_N, np.nan)
        yv[:2] = [1.0, 2.0]
        lhs = _series("sy", dates=_DATES, values=yv)
        _, rhs, _, _ = _pair()
        with pytest.raises(RegressionResidualError, match="min_periods"):
            regression_residual(lhs, rhs)

    def test_zero_variance_rhs_raises(self):
        lhs, _, _, _ = _pair()
        const = _series("c", dates=_DATES, values=[7.0] * _N)
        with pytest.raises(RegressionResidualError, match="zero variance"):
            regression_residual(lhs, const)

    def test_ill_conditioned_design_raises(self):
        lhs, _, _, _ = _pair()
        # rhs nearly constant: huge mean, vanishing variance → enormous
        # condition number for [1, x] design.
        xv = 1e12 + np.linspace(0.0, 1e-4, _N)
        rhs = _series("nc", dates=_DATES, values=xv)
        with pytest.raises(RegressionResidualError, match="condition number"):
            regression_residual(lhs, rhs)


# ===========================================================================
# 4. OPR11 — units / frequency / missingness
# ===========================================================================


class TestStructuralMetadata:
    def test_output_units_are_lhs_passthrough(self):
        lhs, rhs, _, _ = _pair()
        lhs_bps = _series(
            "yb", dates=_DATES, values=lhs.payload.to_numpy(),
            units=TimeSeriesUnits.BPS,
        )
        out = regression_residual(lhs_bps, rhs)
        assert out.units == TimeSeriesUnits.BPS

    def test_cross_unit_inputs_accepted_units_recorded(self):
        """Unit-INVARIANT across inputs: β absorbs the regressor's
        units, so a PERCENT-on-BPS regression is meaningful and must
        NOT raise — both units recorded in lineage (OPR11)."""
        lhs, rhs, _, _ = _pair()
        rhs_bps = _series(
            "xb", dates=_DATES, values=rhs.payload.to_numpy(),
            units=TimeSeriesUnits.BPS,
        )
        out = regression_residual(lhs, rhs_bps)
        head = out.lineage.steps[-1]
        assert head.params["lhs_units"] == "percent"
        assert head.params["rhs_units"] == "bps"
        assert out.units == TimeSeriesUnits.PERCENT

    def test_frequency_mismatch_strict_raises_and_opt_in(self):
        lhs, rhs, _, _ = _pair()
        l2 = _series("l2", dates=_DATES, values=lhs.payload.to_numpy(),
                     frequency="B")
        r2 = _series("r2", dates=_DATES, values=rhs.payload.to_numpy(),
                     frequency="W")
        with pytest.raises(
            RegressionResidualError, match="incompatible frequencies",
        ):
            regression_residual(l2, r2)
        out = regression_residual(
            l2, r2,
            params=RegressionResidualParams(require_matching_frequency=False),
        )
        assert out.frequency is None  # disagreement → None

    def test_missingness_mismatch_strict_raises_and_combined_on_opt_out(self):
        from shared.artifacts import RawNoCleaning
        lhs, rhs, _, _ = _pair()
        r2 = _series(
            "r2", dates=_DATES, values=rhs.payload.to_numpy(),
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(RegressionResidualError, match="missingness"):
            regression_residual(lhs, r2)
        out = regression_residual(
            lhs, r2,
            params=RegressionResidualParams(
                require_matching_missingness=False,
            ),
        )
        assert isinstance(out.missingness_policy, CombinedMissingnessV1)


# ===========================================================================
# 5. OPR10 lineage diagnostics + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_and_records_fit_diagnostics(self):
        lhs, rhs, yv, xv = _pair()
        out = regression_residual(lhs, rhs)
        assert len(out.lineage.steps) == len(lhs.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "regression_residual"
        assert head.version == "1.1.0"  # OPR14d — fit_scope bump
        assert head.auxiliary_lineages == (rhs.lineage,)
        _, alpha, beta = _lstsq_residuals(yv, xv)
        assert head.params["alpha"] == pytest.approx(alpha)
        assert head.params["beta"] == pytest.approx(beta)
        assert 0.0 < head.params["r_squared"] <= 1.0
        assert head.params["n_obs"] == _N

    def test_lineage_discloses_full_sample_look_ahead(self):
        """P5 look-ahead disclosure (mirrors detrend's trend_scope /
        winsorize's quantile_scope): the executed lineage carries
        fit_scope='full_sample' and the bumped 1.1.0 version (OPR14d).
        The math is unchanged — disclosure-only."""
        lhs, rhs, yv, xv = _pair()
        out = regression_residual(lhs, rhs)
        head = out.lineage.steps[-1]
        assert head.params["fit_scope"] == "full_sample"
        assert head.version == "1.1.0"
        # disclosure-only: residuals still equal the lstsq reference
        expected, _, _ = _lstsq_residuals(yv, xv)
        np.testing.assert_allclose(
            out.payload.to_numpy(), expected, rtol=0, atol=1e-10,
        )

    def test_zero_variance_lhs_records_r_squared_none(self):
        """cov with constant lhs: fit is defined (β=0, α=ȳ) but R² is
        0/0 — the sanitiser must record None, never NaN (OPR10)."""
        _, rhs, _, _ = _pair()
        const_lhs = _series("cl", dates=_DATES, values=[3.0] * _N)
        out = regression_residual(const_lhs, rhs)
        head = out.lineage.steps[-1]
        assert head.params["r_squared"] is None
        np.testing.assert_allclose(out.payload.to_numpy(), 0.0, atol=1e-12)

    def test_rerun_is_deterministic(self):
        lhs, rhs, _, _ = _pair()
        out1 = regression_residual(lhs, rhs)
        out2 = regression_residual(lhs, rhs)
        assert out1.lineage.head_hash == out2.lineage.head_hash

    def test_add_constant_changes_identity(self):
        lhs, rhs, _, _ = _pair()
        h1 = regression_residual(
            lhs, rhs, params=RegressionResidualParams(add_constant=True),
        ).lineage.head_hash
        h0 = regression_residual(
            lhs, rhs, params=RegressionResidualParams(add_constant=False),
        ).lineage.head_hash
        assert h1 != h0


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(17)
    n = 100
    x = rng.randn(n).cumsum()
    y = -2.0 + 0.3 * x + rng.randn(n) * 0.2
    dates = pd.bdate_range("2026-01-01", periods=n)
    lhs = _series("sy", dates=dates, values=y, units=TimeSeriesUnits.Z_SCORE)
    rhs = _series("sx", dates=dates, values=x, units=TimeSeriesUnits.Z_SCORE)
    out = regression_residual(lhs, rhs)
    expected, _, _ = _lstsq_residuals(y, x)
    np.testing.assert_allclose(
        out.payload.to_numpy(), expected, rtol=0, atol=1e-10,
    )


# ===========================================================================
# 7. Composition — type-gate + DAG execution (residual → rolling_zscore)
# ===========================================================================


class TestComposition:
    @staticmethod
    def _synth_resolver(tmp_path):
        from shared.schemas import TimeSeries, TimeSeriesRow
        from shared.workflow import PrimitiveSpec

        class _SynthInput(BaseModel):
            series_name: str = "s"
            n_rows: int = 50
            base_value: float = 100.0
            drift: float = 0.5
            units: str = "bps"

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            bdays = pd.bdate_range("2026-04-01", periods=params.n_rows)
            # deterministic, non-collinear walk so the fit is well-posed
            values = [
                params.base_value
                + i * params.drift
                + ((i * 7919) % 13) * 0.25
                for i in range(params.n_rows)
            ]
            rows = [
                TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=v)
                for d, v in zip(bdays, values)
            ]
            return {
                "current_metrics": {"as_of_date": rows[-1].date},
                "time_series": {
                    "series_name": params.series_name,
                    "units": params.units,
                    "description": "synthetic",
                    "rows": [r.model_dump() for r in rows],
                },
            }

        cfg = tmp_path / "synthetic_config.yaml"
        cfg.write_text(
            "tool:\n"
            "  name: synthetic_primitive_tool\n"
            "  domain: synthetic\n"
            "  description: Synthetic primitive for composition tests.\n"
            "  category: desk_invariant_primitive\n"
            "conventions:\n"
            "  ffill_limit_days:\n"
            "    value: 5\n"
            "    source: substrate_test_default\n"
            "    rationale: synthetic config for substrate tests\n"
            "methodology:\n"
            "  what_it_does: deterministic walk for composition tests.\n"
        )
        spec = PrimitiveSpec(
            tool_name="synthetic_primitive_tool",
            callable=_synth,
            input_class=_SynthInput,
            output_class=_SynthOutput,
            config_path=cfg,
        )

        def _resolve(tool_name: str) -> PrimitiveSpec:
            if tool_name != "synthetic_primitive_tool":
                raise KeyError(tool_name)
            return spec

        return _resolve

    @staticmethod
    def _dag():
        from shared.workflow import (
            OperatorNode,
            PrimitiveNode,
            Workflow,
            WorkflowEdge,
        )
        return Workflow(
            workflow_id="regression_residual_composition",
            nodes=[
                PrimitiveNode(
                    node_id="py", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_y", "drift": 0.9},
                ),
                PrimitiveNode(
                    node_id="px", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_x", "drift": 0.4},
                ),
                OperatorNode(node_id="align", operator_name="align_series"),
                OperatorNode(
                    node_id="sel_y", operator_name="select_from_series_set",
                    params={"series_key": "s_y"},
                ),
                OperatorNode(
                    node_id="sel_x", operator_name="select_from_series_set",
                    params={"series_key": "s_x"},
                ),
                OperatorNode(
                    node_id="resid", operator_name="regression_residual",
                ),
                OperatorNode(
                    node_id="z", operator_name="rolling_zscore",
                    params={"window": 20},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="py", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="px", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="sel_y",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="align", target_node_id="sel_x",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="sel_y", target_node_id="resid",
                             target_input_slot="lhs"),
                WorkflowEdge(source_node_id="sel_x", target_node_id="resid",
                             target_input_slot="rhs"),
                WorkflowEdge(source_node_id="resid", target_node_id="z",
                             target_input_slot="series"),
            ],
            terminal_node_id="z",
        )

    def test_type_gate_accepts_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        validate_workflow(
            self._dag(), primitive_resolver=self._synth_resolver(tmp_path),
        )

    def test_dag_executes_residual_then_zscore(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        result = execute_workflow(
            self._dag(), engine=None,
            primitive_resolver=self._synth_resolver(tmp_path),
        )
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.Z_SCORE
        # the residual node's artifact is preserved and is the
        # lstsq residual of the two deterministic walks
        resid = result.node_artifacts["resid"]
        n = 50
        yv = np.array([
            100.0 + i * 0.9 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        xv = np.array([
            100.0 + i * 0.4 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        expected, _, _ = _lstsq_residuals(yv, xv)
        np.testing.assert_allclose(
            resid.payload.to_numpy(), expected, rtol=0, atol=1e-9,
        )
        assert resid.units == TimeSeriesUnits.BPS  # lhs passthrough
        clear_tool_config_cache()

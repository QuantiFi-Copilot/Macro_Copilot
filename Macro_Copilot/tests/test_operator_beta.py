"""Tests for shared.operators.beta — Track-A A2 operator.

Covers the contract surface every standard operator must satisfy:

  - happy path vs the closed-form Sxy/Sxx reference
  - scale-equivariance: β(y, k·x) == β(y, x) / k (β absorbs the
    regressor's units — the unit-invariance proof)
  - OPR2 constant output type (always ScalarMetric, RATIO)
  - OPR8 params=None succeeds and matches explicit config resolution;
    schema mirrors YAML
  - OPR9 typed I/O; non-commutativity
  - OPR10 lineage with fitted α/β/R²/n_obs diagnostics + OPR14 rerun
    determinism + variant-changes-identity
  - OPR11 strict-by-default frequency + missingness with opt-outs;
    cross-unit inputs accepted with both units recorded
  - OPR13 typed refusals: misaligned index, insufficient overlap,
    zero-variance rhs, ill-conditioned design
  - OPR6 finance-blindness (non-rates synthetic data)
  - composition: type-gate accepts beta and a DAG executes end-to-end
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
    ScalarMetric,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.beta import (
    CONFIG_PATH,
    BetaError,
    BetaParams,
    beta,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


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


_N = 50
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _pair(seed: int = 9):
    rng = np.random.RandomState(seed)
    xv = rng.randn(_N).cumsum() + 3.0
    yv = -0.7 + 1.9 * xv + rng.randn(_N) * 0.6
    return (
        _series("y", dates=_DATES, values=yv),
        _series("x", dates=_DATES, values=xv),
        yv,
        xv,
    )


def _closed_form_beta(yv, xv):
    mask = np.isfinite(yv) & np.isfinite(xv)
    x, y = xv[mask], yv[mask]
    sxy = float(np.sum((x - x.mean()) * (y - y.mean())))
    sxx = float(np.sum((x - x.mean()) ** 2))
    return sxy / sxx


# ===========================================================================
# 1. Happy path + closed-form reference + scale-equivariance
# ===========================================================================


class TestHappyPath:
    def test_matches_closed_form(self):
        lhs, rhs, yv, xv = _pair()
        out = beta(lhs, rhs)
        assert isinstance(out, ScalarMetric)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.value == pytest.approx(_closed_form_beta(yv, xv))

    def test_scale_equivariance(self):
        """β absorbs the regressor's units: β(y, k·x) == β(y, x)/k —
        the mathematical proof of unit-invariance across inputs."""
        lhs, rhs, yv, xv = _pair()
        rhs_scaled = _series("xk", dates=_DATES, values=xv * 100.0)
        b1 = beta(lhs, rhs).value
        b2 = beta(lhs, rhs_scaled).value
        assert b2 == pytest.approx(b1 / 100.0)

    def test_lhs_scale_equivariance(self):
        lhs, rhs, yv, xv = _pair()
        lhs_scaled = _series("yk", dates=_DATES, values=yv * 100.0)
        b1 = beta(lhs, rhs).value
        b2 = beta(lhs_scaled, rhs).value
        assert b2 == pytest.approx(b1 * 100.0)

    def test_not_commutative(self):
        lhs, rhs, _, _ = _pair()
        assert beta(lhs, rhs).value != pytest.approx(beta(rhs, lhs).value)

    def test_through_origin_variant(self):
        lhs, rhs, yv, xv = _pair()
        out = beta(lhs, rhs, params=BetaParams(add_constant=False))
        expected = float(np.sum(xv * yv) / np.sum(xv * xv))
        assert out.value == pytest.approx(expected)
        assert out.value != pytest.approx(beta(lhs, rhs).value)

    def test_nan_pairs_dropped(self):
        lhs, rhs, yv, xv = _pair()
        yv2, xv2 = yv.copy(), xv.copy()
        yv2[[2, 9]] = np.nan
        xv2[[9, 17]] = np.nan
        l2 = _series("y2", dates=_DATES, values=yv2)
        r2 = _series("x2", dates=_DATES, values=xv2)
        out = beta(l2, r2)
        assert out.value == pytest.approx(_closed_form_beta(yv2, xv2))

    def test_metric_key_names_both_inputs_in_order(self):
        lhs, rhs, _, _ = _pair()
        assert beta(lhs, rhs).metric_key == "beta__y__x"
        assert beta(rhs, lhs).metric_key == "beta__x__y"

    def test_beta_equals_cov_over_var(self):
        """Cross-check against the committed covariance sibling:
        β = cov(y, x) / var(x) (population or sample consistently)."""
        lhs, rhs, yv, xv = _pair()
        out = beta(lhs, rhs)
        expected = (
            pd.Series(yv).cov(pd.Series(xv), ddof=1)
            / pd.Series(xv).var(ddof=1)
        )
        assert out.value == pytest.approx(float(expected))


# ===========================================================================
# 2. OPR8 — config defaults + schema mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_succeeds_and_matches_explicit_resolution(self):
        lhs, rhs, _, _ = _pair()
        out_default = beta(lhs, rhs)
        cfg = load_operator_config(CONFIG_PATH)
        explicit = BetaParams(
            add_constant=bool(cfg.default_value("add_constant")),
            min_periods=int(cfg.default_value("min_periods")),
            condition_number_threshold=float(
                cfg.default_value("condition_number_threshold")
            ),
        )
        out_explicit = beta(lhs, rhs, params=explicit)
        assert out_default.lineage.head_hash == out_explicit.lineage.head_hash
        assert out_default.value == out_explicit.value

    def test_schema_defaults_mirror_yaml_defaults(self):
        cfg = load_operator_config(CONFIG_PATH)
        p = BetaParams()
        assert p.add_constant == bool(cfg.default_value("add_constant"))
        assert p.min_periods == int(cfg.default_value("min_periods"))
        assert p.condition_number_threshold == float(
            cfg.default_value("condition_number_threshold")
        )

    def test_min_periods_floor_enforced(self):
        with pytest.raises(ValueError):
            BetaParams(min_periods=2)


# ===========================================================================
# 3. OPR13 — typed refusals
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        lhs, _, _, _ = _pair()
        with pytest.raises(BetaError, match="must be Series"):
            beta(lhs, 3.0)  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        lhs, _, _, _ = _pair()
        other = _series(
            "z", dates=pd.bdate_range("2027-01-04", periods=_N),
            values=list(range(_N)),
        )
        with pytest.raises(BetaError, match="identical DatetimeIndex"):
            beta(lhs, other)

    def test_insufficient_overlap_raises(self):
        yv = np.full(_N, np.nan)
        yv[:2] = [1.0, 2.0]
        lhs = _series("sy", dates=_DATES, values=yv)
        _, rhs, _, _ = _pair()
        with pytest.raises(BetaError, match="min_periods"):
            beta(lhs, rhs)

    def test_zero_variance_rhs_raises(self):
        lhs, _, _, _ = _pair()
        const = _series("c", dates=_DATES, values=[7.0] * _N)
        with pytest.raises(BetaError, match="zero variance"):
            beta(lhs, const)

    def test_ill_conditioned_design_raises(self):
        lhs, _, _, _ = _pair()
        xv = 1e12 + np.linspace(0.0, 1e-4, _N)
        rhs = _series("nc", dates=_DATES, values=xv)
        with pytest.raises(BetaError, match="condition number"):
            beta(lhs, rhs)


# ===========================================================================
# 4. OPR11 — units / frequency / missingness
# ===========================================================================


class TestStructuralMetadata:
    def test_cross_unit_inputs_accepted_units_recorded(self):
        lhs, rhs, _, xv = _pair()
        rhs_bps = _series(
            "xb", dates=_DATES, values=xv, units=TimeSeriesUnits.BPS,
        )
        out = beta(lhs, rhs_bps)
        head = out.lineage.steps[-1]
        assert head.params["lhs_units"] == "percent"
        assert head.params["rhs_units"] == "bps"
        assert out.units == TimeSeriesUnits.RATIO

    def test_frequency_mismatch_strict_raises_and_opt_in(self):
        lhs, rhs, yv, xv = _pair()
        l2 = _series("l2", dates=_DATES, values=yv, frequency="B")
        r2 = _series("r2", dates=_DATES, values=xv, frequency="W")
        with pytest.raises(BetaError, match="incompatible frequencies"):
            beta(l2, r2)
        out = beta(
            l2, r2, params=BetaParams(require_matching_frequency=False),
        )
        assert isinstance(out, ScalarMetric)

    def test_missingness_mismatch_strict_raises_and_opt_in(self):
        from shared.artifacts import RawNoCleaning
        lhs, rhs, _, xv = _pair()
        r2 = _series(
            "r2", dates=_DATES, values=xv,
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(BetaError, match="missingness"):
            beta(lhs, r2)
        out = beta(
            lhs, r2, params=BetaParams(require_matching_missingness=False),
        )
        assert isinstance(out, ScalarMetric)


# ===========================================================================
# 5. OPR10 lineage diagnostics + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_and_records_fit_diagnostics(self):
        lhs, rhs, yv, xv = _pair()
        out = beta(lhs, rhs)
        assert len(out.lineage.steps) == len(lhs.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "beta"
        assert head.auxiliary_lineages == (rhs.lineage,)
        assert head.params["beta"] == pytest.approx(out.value)
        assert 0.0 < head.params["r_squared"] <= 1.0
        assert head.params["n_obs"] == _N
        # alpha matches the closed form: ȳ − β·x̄
        expected_alpha = float(yv.mean() - out.value * xv.mean())
        assert head.params["alpha"] == pytest.approx(expected_alpha)

    def test_rerun_is_deterministic(self):
        lhs, rhs, _, _ = _pair()
        out1 = beta(lhs, rhs)
        out2 = beta(lhs, rhs)
        assert out1.lineage.head_hash == out2.lineage.head_hash
        assert out1.value == out2.value

    def test_add_constant_changes_identity(self):
        lhs, rhs, _, _ = _pair()
        h1 = beta(
            lhs, rhs, params=BetaParams(add_constant=True),
        ).lineage.head_hash
        h0 = beta(
            lhs, rhs, params=BetaParams(add_constant=False),
        ).lineage.head_hash
        assert h1 != h0


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(29)
    n = 90
    x = rng.randn(n).cumsum()
    y = 4.0 - 1.2 * x + rng.randn(n) * 0.3
    dates = pd.bdate_range("2026-01-01", periods=n)
    lhs = _series("sy", dates=dates, values=y, units=TimeSeriesUnits.Z_SCORE)
    rhs = _series("sx", dates=dates, values=x, units=TimeSeriesUnits.Z_SCORE)
    out = beta(lhs, rhs)
    assert out.value == pytest.approx(_closed_form_beta(y, x))
    assert out.value < 0  # negatively related by construction


# ===========================================================================
# 7. Composition — type-gate + DAG execution
# ===========================================================================


class TestComposition:
    @staticmethod
    def _dag_and_resolver(tmp_path):
        from shared.schemas import TimeSeries, TimeSeriesRow
        from shared.workflow import (
            OperatorNode,
            PrimitiveNode,
            PrimitiveSpec,
            Workflow,
            WorkflowEdge,
        )

        class _SynthInput(BaseModel):
            series_name: str = "s"
            n_rows: int = 40
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

        wf = Workflow(
            workflow_id="beta_composition",
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
                OperatorNode(node_id="b", operator_name="beta"),
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
                WorkflowEdge(source_node_id="sel_y", target_node_id="b",
                             target_input_slot="lhs"),
                WorkflowEdge(source_node_id="sel_x", target_node_id="b",
                             target_input_slot="rhs"),
            ],
            terminal_node_id="b",
        )
        return wf, _resolve

    def test_type_gate_accepts_beta_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_and_matches_closed_form(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        n = 40
        yv = np.array([
            100.0 + i * 0.9 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        xv = np.array([
            100.0 + i * 0.4 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        assert terminal.value == pytest.approx(_closed_form_beta(yv, xv))
        assert terminal.lineage.steps[-1].name == "beta"
        clear_tool_config_cache()

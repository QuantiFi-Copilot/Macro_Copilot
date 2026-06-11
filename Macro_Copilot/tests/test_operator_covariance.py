"""Tests for shared.operators.covariance — Track-A A2 operator.

Covers the contract surface every standard operator must satisfy:

  - happy path + ddof variants (sample vs population) vs pandas
  - OPR2 constant output type (always ScalarMetric)
  - OPR8 params=None resolves from config; schema defaults mirror YAML
  - OPR9 typed I/O (non-Series input refused)
  - OPR10 lineage extension (N -> N+1; one OperatorStep; right chain in
    auxiliary_lineages) + OPR14 rerun determinism (stable head_hash)
  - OPR11 strict-by-default units + frequency + missingness, each with
    explicit opt-in; the unit-BEARING divergence from correlation
  - OPR13 typed CovarianceError on every degenerate input (misaligned
    index, insufficient overlap) — and the deliberate NON-error: a
    constant (zero-variance) input yields covariance 0.0
  - OPR6 finance-blindness (runs on non-rates synthetic data)
  - composition: the type-gate accepts covariance as a DAG node and a
    small synthetic-primitive DAG using it executes end-to-end
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
from shared.operators.covariance import (
    CONFIG_PATH,
    CovarianceError,
    CovarianceParams,
    covariance,
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
    """Build a Series artifact with a synthesised fetch+adapter lineage
    (same pattern as the rest of the operator test-suite)."""
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


_DATES = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]


# ===========================================================================
# 1. Happy path + ddof variants + constant output type
# ===========================================================================


class TestHappyPath:
    def test_matches_pandas_sample_covariance(self):
        av = [1.0, 3.0, 2.0, 5.0, 4.0]
        bv = [2.0, 1.0, 4.0, 3.0, 6.0]
        a = _series("a", dates=_DATES, values=av)
        b = _series("b", dates=_DATES, values=bv)
        expected = pd.Series(av).cov(pd.Series(bv), ddof=1)
        out = covariance(a, b)
        assert isinstance(out, ScalarMetric)  # OPR2 constant output type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.value == pytest.approx(float(expected))

    def test_population_covariance_ddof_zero(self):
        av = [1.0, 3.0, 2.0, 5.0, 4.0]
        bv = [2.0, 1.0, 4.0, 3.0, 6.0]
        a = _series("a", dates=_DATES, values=av)
        b = _series("b", dates=_DATES, values=bv)
        expected = pd.Series(av).cov(pd.Series(bv), ddof=0)
        out = covariance(a, b, params=CovarianceParams(ddof=0))
        assert out.value == pytest.approx(float(expected))
        # sample vs population genuinely differ on this input
        sample = covariance(a, b, params=CovarianceParams(ddof=1))
        assert out.value != pytest.approx(sample.value)

    def test_commutative(self):
        a = _series("a", dates=_DATES, values=[1.0, 3.0, 2.0, 5.0, 4.0])
        b = _series("b", dates=_DATES, values=[2.0, 1.0, 4.0, 3.0, 6.0])
        assert covariance(a, b).value == pytest.approx(covariance(b, a).value)

    def test_covariance_with_self_is_variance(self):
        av = [1.0, 3.0, 2.0, 5.0, 4.0]
        a = _series("a", dates=_DATES, values=av)
        a2 = _series("a2", dates=_DATES, values=av)
        out = covariance(a, a2)
        assert out.value == pytest.approx(float(pd.Series(av).var(ddof=1)))

    def test_zero_variance_input_is_defined_not_an_error(self):
        """Deliberate divergence from correlation: cov(X, const) = 0.0
        is mathematically defined — a typed refusal here would be
        dishonest."""
        a = _series("a", dates=_DATES, values=[3.0, 3.0, 3.0, 3.0, 3.0])
        b = _series("b", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        out = covariance(a, b)
        assert out.value == pytest.approx(0.0)

    def test_metric_key_names_both_inputs(self):
        a = _series("k1", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("k2", dates=_DATES, values=[2.0, 4.0, 6.0, 8.0, 10.0])
        out = covariance(a, b)
        assert "k1" in out.metric_key and "k2" in out.metric_key

    def test_nan_rows_dropped_pairwise(self):
        av = [1.0, np.nan, 2.0, 5.0, 4.0]
        bv = [2.0, 1.0, np.nan, 3.0, 6.0]
        a = _series("a", dates=_DATES, values=av)
        b = _series("b", dates=_DATES, values=bv)
        mask = ~(pd.Series(av).isna() | pd.Series(bv).isna())
        expected = pd.Series(av)[mask].cov(pd.Series(bv)[mask], ddof=1)
        out = covariance(a, b, params=CovarianceParams(min_periods=3))
        assert out.value == pytest.approx(float(expected))


# ===========================================================================
# 2. OPR8 — config defaults + schema-default mirror
# ===========================================================================


class TestParamsAndConfig:
    def test_params_none_resolves_from_config(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("b", dates=_DATES, values=[5.0, 4.0, 3.0, 2.0, 1.0])
        out = covariance(a, b, params=None)
        expected = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]).cov(
            pd.Series([5.0, 4.0, 3.0, 2.0, 1.0]), ddof=1,
        )
        assert out.value == pytest.approx(float(expected))

    def test_schema_defaults_mirror_yaml_defaults(self):
        """OPR8: a param is schema-default OR YAML-authoritative, never
        silently divergent."""
        cfg = load_operator_config(CONFIG_PATH)
        p = CovarianceParams()
        assert p.ddof == int(cfg.default_value("ddof"))
        assert p.min_periods == int(cfg.default_value("min_periods"))

    def test_ddof_bounds_enforced(self):
        with pytest.raises(ValueError):
            CovarianceParams(ddof=2)
        with pytest.raises(ValueError):
            CovarianceParams(ddof=-1)


# ===========================================================================
# 3. OPR13 — typed refusals on degenerate inputs
# ===========================================================================


class TestRefusals:
    def test_non_series_input_raises(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        with pytest.raises(CovarianceError, match="must be Series"):
            covariance(a, [1, 2, 3])  # type: ignore[arg-type]

    def test_misaligned_index_raises(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series(
            "b",
            dates=["2026-02-02", "2026-02-05", "2026-02-06", "2026-02-07", "2026-02-08"],
            values=[10.0, 20.0, 30.0, 40.0, 50.0],
        )
        with pytest.raises(CovarianceError, match="identical DatetimeIndex"):
            covariance(a, b)

    def test_insufficient_overlap_raises(self):
        a = _series("a", dates=["2026-01-02"], values=[1.0])
        b = _series("b", dates=["2026-01-02"], values=[2.0])
        with pytest.raises(CovarianceError, match="min_periods"):
            covariance(a, b)

    def test_insufficient_overlap_after_nan_drop_raises(self):
        a = _series("a", dates=_DATES, values=[1.0, np.nan, np.nan, np.nan, np.nan])
        b = _series("b", dates=_DATES, values=[2.0, 1.0, 4.0, 3.0, 6.0])
        with pytest.raises(CovarianceError, match="min_periods"):
            covariance(a, b)


# ===========================================================================
# 4. OPR11 — units / frequency / missingness strict-by-default
# ===========================================================================


class TestStructuralMetadata:
    def test_cross_unit_strict_raises(self):
        """The unit-BEARING divergence from correlation: a covariance's
        unit is the product of its inputs' units, so mixed units are
        refused by default with the convert_units remedy named."""
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=_DATES, values=[10.0, 20.0, 30.0, 40.0, 50.0],
                    units=TimeSeriesUnits.BPS)
        with pytest.raises(CovarianceError, match="convert_units"):
            covariance(a, b)

    def test_cross_unit_opt_in_succeeds_and_records_units(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0],
                    units=TimeSeriesUnits.PERCENT)
        b = _series("b", dates=_DATES, values=[10.0, 20.0, 30.0, 40.0, 50.0],
                    units=TimeSeriesUnits.BPS)
        out = covariance(
            a, b, params=CovarianceParams(require_matching_units=False),
        )
        head = out.lineage.steps[-1]
        assert head.params["left_units"] == "percent"
        assert head.params["right_units"] == "bps"
        assert head.params["require_matching_units"] is False

    def test_frequency_mismatch_strict_raises(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0], frequency="B")
        b = _series("b", dates=_DATES, values=[5.0, 4.0, 3.0, 2.0, 1.0], frequency="W")
        with pytest.raises(CovarianceError, match="incompatible frequencies"):
            covariance(a, b)

    def test_frequency_mismatch_opt_in_succeeds(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0], frequency="B")
        b = _series("b", dates=_DATES, values=[5.0, 4.0, 3.0, 2.0, 1.0], frequency="W")
        out = covariance(
            a, b, params=CovarianceParams(require_matching_frequency=False),
        )
        assert isinstance(out, ScalarMetric)

    def test_missingness_mismatch_strict_raises(self):
        from shared.artifacts import RawNoCleaning
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series(
            "b", dates=_DATES, values=[5.0, 4.0, 3.0, 2.0, 1.0],
            missingness_policy=RawNoCleaning(),
        )
        with pytest.raises(CovarianceError, match="missingness"):
            covariance(a, b)


# ===========================================================================
# 5. OPR10 lineage + OPR14 determinism
# ===========================================================================


class TestLineageAndDeterminism:
    def test_lineage_extends_by_one_step(self):
        a = _series("a", dates=_DATES, values=[1.0, 2.0, 3.0, 4.0, 5.0])
        b = _series("b", dates=_DATES, values=[2.0, 4.0, 6.0, 8.0, 10.0])
        out = covariance(a, b)
        assert len(out.lineage.steps) == len(a.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "covariance"
        assert head.version == "1.0.0"
        assert head.auxiliary_lineages == (b.lineage,)
        assert head.params["ddof"] == 1
        assert head.params["n_obs"] == 5
        assert head.params["left_units"] == "percent"
        assert head.params["right_units"] == "percent"

    def test_rerun_is_deterministic(self):
        a = _series("a", dates=_DATES, values=[1.0, 3.0, 2.0, 5.0, 4.0])
        b = _series("b", dates=_DATES, values=[2.0, 1.0, 4.0, 3.0, 6.0])
        out1 = covariance(a, b)
        out2 = covariance(a, b)
        assert out1.lineage.head_hash == out2.lineage.head_hash
        assert out1.value == out2.value

    def test_ddof_changes_identity(self):
        """ART10: every content-defining choice is folded into
        step.params, so different ddof → different head_hash."""
        a = _series("a", dates=_DATES, values=[1.0, 3.0, 2.0, 5.0, 4.0])
        b = _series("b", dates=_DATES, values=[2.0, 1.0, 4.0, 3.0, 6.0])
        h1 = covariance(a, b, params=CovarianceParams(ddof=1)).lineage.head_hash
        h0 = covariance(a, b, params=CovarianceParams(ddof=0)).lineage.head_hash
        assert h1 != h0


# ===========================================================================
# 6. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(7)
    x = rng.randn(60).cumsum()
    y = 2.5 * x + rng.randn(60) * 0.5
    dates = pd.bdate_range("2026-01-01", periods=60)
    a = _series("synthetic_x", dates=dates, values=x, units=TimeSeriesUnits.Z_SCORE)
    b = _series("synthetic_y", dates=dates, values=y, units=TimeSeriesUnits.Z_SCORE)
    out = covariance(a, b)
    assert isinstance(out, ScalarMetric)
    expected = pd.Series(x).cov(pd.Series(y), ddof=1)
    assert out.value == pytest.approx(float(expected))
    # strongly positively related by construction
    assert out.value > 0


# ===========================================================================
# 7. Composition — the type-gate accepts covariance and a DAG executes
# ===========================================================================


class TestComposition:
    """Plan §10 composition proof: validate_workflow accepts covariance
    as a DAG node with the right artifact types in/out, and a small DAG
    using it executes through the real executor (synthetic primitives,
    no DB — operators are pure)."""

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
            n_rows: int = 30
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
                params.base_value + i * params.drift
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
            "  description: Synthetic primitive for covariance composition test.\n"
            "  category: desk_invariant_primitive\n"
            "conventions:\n"
            "  ffill_limit_days:\n"
            "    value: 5\n"
            "    source: substrate_test_default\n"
            "    rationale: synthetic config for substrate tests\n"
            "methodology:\n"
            "  what_it_does: >-\n"
            "    Deterministic linear walk for composition tests.\n"
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
            workflow_id="covariance_composition",
            nodes=[
                PrimitiveNode(
                    node_id="pa", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_a", "drift": 0.5},
                ),
                PrimitiveNode(
                    node_id="pb", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_b", "drift": -0.25},
                ),
                OperatorNode(node_id="align", operator_name="align_series"),
                OperatorNode(
                    node_id="sel_a", operator_name="select_from_series_set",
                    params={"series_key": "s_a"},
                ),
                OperatorNode(
                    node_id="sel_b", operator_name="select_from_series_set",
                    params={"series_key": "s_b"},
                ),
                OperatorNode(node_id="cov", operator_name="covariance"),
            ],
            edges=[
                WorkflowEdge(source_node_id="pa", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="pb", target_node_id="align",
                             target_input_slot="series_list"),
                WorkflowEdge(source_node_id="align", target_node_id="sel_a",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="align", target_node_id="sel_b",
                             target_input_slot="series_set"),
                WorkflowEdge(source_node_id="sel_a", target_node_id="cov",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="sel_b", target_node_id="cov",
                             target_input_slot="right"),
            ],
            terminal_node_id="cov",
        )
        return wf, _resolve

    def test_type_gate_accepts_covariance_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        # No exception == the type-gate accepts the DAG.
        validate_workflow(wf, primitive_resolver=resolver)

    @staticmethod
    def _mixed_unit_dag_and_resolver(tmp_path, *, node_params):
        """Two synthetic primitives with DECLARED, mismatched
        output_field_units wired straight into covariance — exercises
        the validate-time unit hook (OPR11 at the type-gate)."""
        from shared.workflow import (
            OperatorNode,
            PrimitiveNode,
            PrimitiveSpec,
            Workflow,
            WorkflowEdge,
        )

        class _In(BaseModel):
            series_name: str = "s"

        class _Metrics(BaseModel):
            as_of_date: str

        class _Out(BaseModel):
            current_metrics: _Metrics
            time_series: dict

        cfg = tmp_path / "mixed_unit_config.yaml"
        cfg.write_text(
            "tool:\n"
            "  name: synthetic_units_tool\n"
            "  domain: synthetic\n"
            "  description: Declared-units synthetic for unit-gate tests.\n"
            "  category: desk_invariant_primitive\n"
            "conventions:\n"
            "  ffill_limit_days:\n"
            "    value: 5\n"
            "    source: substrate_test_default\n"
            "    rationale: synthetic config for substrate tests\n"
            "methodology:\n"
            "  what_it_does: unit-gate test stub (never executed).\n"
        )

        def _stub(*, engine, params, config) -> dict:  # pragma: no cover
            raise AssertionError("validate-time test must not execute")

        specs = {
            "tool_percent": PrimitiveSpec(
                tool_name="tool_percent", callable=_stub,
                input_class=_In, output_class=_Out, config_path=cfg,
                output_field_units={"time_series": "percent"},
            ),
            "tool_bps": PrimitiveSpec(
                tool_name="tool_bps", callable=_stub,
                input_class=_In, output_class=_Out, config_path=cfg,
                output_field_units={"time_series": "bps"},
            ),
        }

        def _resolve(tool_name: str) -> PrimitiveSpec:
            return specs[tool_name]

        wf = Workflow(
            workflow_id="covariance_mixed_units",
            nodes=[
                PrimitiveNode(node_id="pa", tool_name="tool_percent",
                              output_field="time_series", params={}),
                PrimitiveNode(node_id="pb", tool_name="tool_bps",
                              output_field="time_series", params={}),
                OperatorNode(node_id="cov", operator_name="covariance",
                             params=node_params),
            ],
            edges=[
                WorkflowEdge(source_node_id="pa", target_node_id="cov",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="pb", target_node_id="cov",
                             target_input_slot="right"),
            ],
            terminal_node_id="cov",
        )
        return wf, _resolve

    def test_declared_unit_mismatch_caught_at_validate_time(self, tmp_path):
        """Critic finding (OPR11 at the type-gate): a DAG wiring two
        primitives with declared, mismatched output_field_units into
        covariance must fail validate_workflow (E_UNIT_MISMATCH) —
        not die at execute time."""
        from shared.workflow import WorkflowValidationError, validate_workflow
        wf, resolver = self._mixed_unit_dag_and_resolver(
            tmp_path, node_params={},
        )
        with pytest.raises(WorkflowValidationError, match="matching units"):
            validate_workflow(wf, primitive_resolver=resolver)

    def test_declared_unit_mismatch_opt_out_passes_validate_time(self, tmp_path):
        """The explicit opt-out (require_matching_units: false in node
        params) is honoured by the validate-time hook, mirroring the
        runtime contract."""
        from shared.workflow import validate_workflow
        wf, resolver = self._mixed_unit_dag_and_resolver(
            tmp_path, node_params={"require_matching_units": False},
        )
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_and_matches_direct_call(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        # Independently reproduce: two deterministic linear walks.
        n = 30
        xs = pd.Series([100.0 + i * 0.5 for i in range(n)])
        ys = pd.Series([100.0 - i * 0.25 for i in range(n)])
        assert terminal.value == pytest.approx(float(xs.cov(ys, ddof=1)))
        # Lineage: fetch-side steps + align + select + covariance.
        names = [s.name for s in terminal.lineage.steps]
        assert names[-1] == "covariance"
        clear_tool_config_cache()

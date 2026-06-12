"""Tests for shared.operators.normality_test — Track-A A5.

Covers the contract surface every standard operator must satisfy:

  - parity vs statsmodels jarque_bera (statistic exact; p/skew/
    raw-kurtosis in lineage)
  - directional sanity: a lognormal sample strongly rejects
    normality; a normal sample does not
  - the kurtosis convention named (kurtosis_raw — normal == 3, never
    the Fisher excess form)
  - NaN dropna (order-insensitive — count in lineage, no interior
    audit)
  - refusals: non-Series; < 3 finite; zero variance
  - OPR8 params=None (zero-knob model); OPR14 determinism
  - OPR6 non-rates case; composition diff→normality_test DAG
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel
from statsmodels.stats.stattools import jarque_bera

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    FetchStep,
    Lineage,
    ScalarMetric,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.normality_test import (
    NormalityTestError,
    NormalityTestParams,
    normality_test,
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
    units: TimeSeriesUnits = TimeSeriesUnits.BPS,
    frequency=None,
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
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


_N = 200
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _normal(seed: int = 167):
    rng = np.random.RandomState(seed)
    vals = rng.randn(_N)
    return _series("x", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Parity + directional sanity
# ===========================================================================


class TestHappyPath:
    def test_matches_statsmodels_exactly(self):
        s, vals = _normal()
        out = normality_test(s)
        ref_stat, ref_p, ref_skew, ref_kurt = jarque_bera(vals)
        assert out.value == pytest.approx(float(ref_stat), rel=1e-12)
        assert isinstance(out, ScalarMetric)  # OPR2 constant type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.metric_key == "normality_test__x"
        head = out.lineage.steps[-1]
        assert head.params["p_value"] == pytest.approx(float(ref_p))
        assert head.params["skewness"] == pytest.approx(float(ref_skew))
        assert head.params["kurtosis_raw"] == pytest.approx(
            float(ref_kurt),
        )

    def test_normal_sample_not_rejected(self):
        s, _ = _normal()
        out = normality_test(s)
        assert out.lineage.steps[-1].params["p_value"] > 0.05

    def test_lognormal_strongly_rejected(self):
        rng = np.random.RandomState(173)
        vals = np.exp(rng.randn(_N))
        s = _series("ln", dates=_DATES, values=vals)
        out = normality_test(s)
        assert out.lineage.steps[-1].params["p_value"] < 1e-10
        assert out.value > 100.0

    def test_kurtosis_convention_is_raw_normal_three(self):
        """The convention pin: a large normal sample's kurtosis_raw
        sits near 3 (raw), NOT near 0 (Fisher excess)."""
        rng = np.random.RandomState(179)
        vals = rng.randn(5000)
        s = _series(
            "big", dates=pd.bdate_range("2010-01-04", periods=5000),
            values=vals,
        )
        out = normality_test(s)
        k = out.lineage.steps[-1].params["kurtosis_raw"]
        assert 2.7 < k < 3.3

    def test_full_sample_scope_in_lineage(self):
        s, _ = _normal()
        out = normality_test(s)
        assert out.lineage.steps[-1].params["test_scope"] == "full_sample"


# ===========================================================================
# 2. NaN policy (order-insensitive dropna)
# ===========================================================================


class TestNan:
    def test_nan_dropped_and_counted(self):
        vals = np.random.RandomState(3).randn(_N)
        vals[10] = np.nan
        vals[100] = np.nan
        s = _series("g", dates=_DATES, values=vals)
        out = normality_test(s)
        head = out.lineage.steps[-1]
        assert head.params["n_dropped"] == 2
        assert head.params["n_obs"] == _N - 2
        # Parity against the cleaned values — dropna is exact for
        # order-insensitive moments.
        ref_stat = float(jarque_bera(vals[np.isfinite(vals)])[0])
        assert out.value == pytest.approx(ref_stat, rel=1e-12)


# ===========================================================================
# 3. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(NormalityTestError, match="must be a Series"):
            normality_test(None)  # type: ignore[arg-type]

    def test_fewer_than_three_finite_refused(self):
        s = _series("two", dates=_DATES[:2], values=[1.0, 2.0])
        with pytest.raises(NormalityTestError, match="at least 3"):
            normality_test(s)

    def test_zero_variance_refused(self):
        s = _series("const", dates=_DATES[:30], values=[7.0] * 30)
        with pytest.raises(NormalityTestError, match="zero variance"):
            normality_test(s)

    def test_params_none_equals_empty_params(self):
        s, _ = _normal()
        assert (
            normality_test(s).lineage.head_hash
            == normality_test(
                s, params=NormalityTestParams(),
            ).lineage.head_hash
        )

    def test_extra_params_forbidden_at_schema(self):
        with pytest.raises(ValueError):
            NormalityTestParams(test="shapiro")  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        s, _ = _normal()
        assert (
            normality_test(s).lineage.head_hash
            == normality_test(s).lineage.head_hash
        )

    def test_lineage_extends_by_one_step(self):
        s, _ = _normal()
        out = normality_test(s)
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        assert out.lineage.steps[-1].name == "normality_test"


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(181)
    vals = rng.uniform(-1, 1, size=_N)  # uniform: platykurtic
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO)
    out = normality_test(s)
    ref_stat = float(jarque_bera(vals)[0])
    assert out.value == pytest.approx(ref_stat, rel=1e-12)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 5. Composition — diff → normality_test (change-distribution check)
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
            n_rows: int = 60
            base_value: float = 100.0
            drift: float = 0.5
            pattern_mult: int = 7919
            pattern_mod: int = 13
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
                + ((i * params.pattern_mult) % params.pattern_mod) * 0.25
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

        # 'Are the day-over-day changes of A normally distributed?'
        wf = Workflow(
            workflow_id="normality_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="d", operator_name="series_arithmetic",
                    params={"op": "diff"},
                ),
                OperatorNode(
                    node_id="nt", operator_name="normality_test",
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="d",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="d", target_node_id="nt",
                             target_input_slot="series"),
            ],
            terminal_node_id="nt",
        )
        return wf, _resolve

    def test_type_gate_accepts_normality_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_change_normality(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.RATIO
        n = 60
        vals = pd.Series([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        diffed = vals.diff().dropna().to_numpy()
        ref_stat = float(jarque_bera(diffed)[0])
        assert terminal.value == pytest.approx(ref_stat, rel=1e-9)
        clear_tool_config_cache()

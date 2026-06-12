"""Tests for shared.operators.hurst_exponent — Track-A A5 (final).

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (value = H; uncorrected slope +
    fit R² + scale trail ride in lineage)
  - directional sanity: random-walk level → persistent H>0.5;
    anti-persistent → H<0.5
  - NaN dropna + interior-drop audit (order-sensitive R/S)
  - refusals: non-Series; < 128 finite rows; zero variance
  - OPR8 zero-knob params; OPR14 determinism
  - OPR6 non-rates case; composition primitive→hurst_exponent DAG
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
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.hurst_exponent import (
    HurstExponentError,
    HurstExponentParams,
    hurst_exponent,
)
from shared.quant.hurst import hurst_rs


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_operator_config_cache()
    yield
    clear_operator_config_cache()


def _series(
    series_key: str,
    *,
    values,
    units: TimeSeriesUnits = TimeSeriesUnits.BPS,
    frequency=None,
    start: str = "2015-01-01",
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
    dates = pd.bdate_range(start, periods=len(values))
    payload = pd.Series(values, index=pd.DatetimeIndex(dates), dtype=float)
    return Series(
        series_key=series_key,
        payload=payload,
        units=units,
        frequency=frequency,
        missingness_policy=CleanSingleSeriesV1(ffill_limit=5),
        lineage=Lineage.from_steps([fetch, adapter]),
    )


_N = 2000


def _random_walk(seed: int = 1):
    vals = np.cumsum(np.random.RandomState(seed).randn(_N))
    return _series("x", values=vals), vals


# ===========================================================================
# 1. Parity + directional sanity
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_numeric(self):
        s, vals = _random_walk()
        out = hurst_exponent(s)
        ref = hurst_rs(vals)
        assert out.value == pytest.approx(ref.h, rel=1e-12)
        assert isinstance(out, ScalarMetric)  # OPR2 constant type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.metric_key == "hurst_exponent__x"
        head = out.lineage.steps[-1]
        assert head.params["hurst_exponent"] == pytest.approx(ref.h)
        assert head.params["h_uncorrected"] == pytest.approx(
            ref.h_uncorrected,
        )
        assert head.params["r_squared"] == pytest.approx(ref.r_squared)
        assert head.params["n_scales"] == ref.n_scales
        assert head.params["estimator"] == "classical_rs"
        assert head.params["correction"] == "anis_lloyd"
        assert head.params["differenced"] is False

    def test_random_walk_level_persistent(self):
        s, _ = _random_walk(seed=7)
        out = hurst_exponent(s)
        assert out.value > 0.7  # strongly persistent

    def test_anti_persistent_below_half(self):
        rng = np.random.RandomState(11)
        vals = np.zeros(_N)
        for i in range(1, _N):
            vals[i] = -0.6 * vals[i - 1] + rng.randn()
        s = _series("ap", values=vals)
        out = hurst_exponent(s)
        assert out.value < 0.45

    def test_full_sample_scope_in_lineage(self):
        s, _ = _random_walk()
        assert (
            hurst_exponent(s).lineage.steps[-1].params["test_scope"]
            == "full_sample"
        )


# ===========================================================================
# 2. NaN audit
# ===========================================================================


class TestNanAudit:
    def test_interior_gaps_disclosed(self):
        vals = np.cumsum(np.random.RandomState(3).randn(_N))
        vals[1000] = np.nan
        vals[-3:] = np.nan  # edge
        s = _series("g", values=vals)
        out = hurst_exponent(s)
        head = out.lineage.steps[-1]
        assert head.params["n_dropped"] == 4
        assert head.params["n_interior_dropped"] == 1
        # Parity against the cleaned values.
        ref = hurst_rs(pd.Series(vals).dropna().to_numpy())
        assert out.value == pytest.approx(ref.h, rel=1e-12)


# ===========================================================================
# 3. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(HurstExponentError, match="must be a Series"):
            hurst_exponent(0.5)  # type: ignore[arg-type]

    def test_too_few_rows_refused(self):
        s = _series("few", values=np.cumsum(
            np.random.RandomState(9).randn(100)))
        with pytest.raises(HurstExponentError, match="at least 128"):
            hurst_exponent(s)

    def test_zero_variance_refused(self):
        s = _series("const", values=[2.0] * 200)
        with pytest.raises(HurstExponentError, match="zero variance"):
            hurst_exponent(s)

    def test_params_none_equals_empty_params(self):
        s, _ = _random_walk()
        assert (
            hurst_exponent(s).lineage.head_hash
            == hurst_exponent(s, params=HurstExponentParams()).lineage.head_hash
        )

    def test_extra_params_forbidden_at_schema(self):
        with pytest.raises(ValueError):
            HurstExponentParams(method="dfa")  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        s, _ = _random_walk()
        assert (
            hurst_exponent(s).lineage.head_hash
            == hurst_exponent(s).lineage.head_hash
        )

    def test_lineage_extends_by_one_step(self):
        s, _ = _random_walk()
        out = hurst_exponent(s)
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        assert out.lineage.steps[-1].name == "hurst_exponent"


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    vals = np.cumsum(np.random.RandomState(181).randn(_N))
    s = _series("sensor", values=vals, units=TimeSeriesUnits.RATIO)
    out = hurst_exponent(s)
    ref = hurst_rs(vals)
    assert out.value == pytest.approx(ref.h, rel=1e-12)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 5. Composition — primitive → hurst_exponent (level consumed directly)
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
            n_rows: int = 300
            base_value: float = 100.0
            drift: float = 0.0
            pattern_mult: int = 7919
            pattern_mod: int = 13
            units: str = "bps"

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            bdays = pd.bdate_range("2024-01-01", periods=params.n_rows)
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

        # 'What is the Hurst exponent of series A?' — hurst_exponent
        # consumes the series directly (>= 128 rows).
        wf = Workflow(
            workflow_id="hurst_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s", "n_rows": 300, "drift": 0.0},
                ),
                OperatorNode(
                    node_id="he", operator_name="hurst_exponent",
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="he",
                             target_input_slot="series"),
            ],
            terminal_node_id="he",
        )
        return wf, _resolve

    def test_type_gate_accepts_hurst_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_hurst_estimate(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.RATIO
        vals = np.array([
            100.0 + ((i * 7919) % 13) * 0.25 for i in range(300)
        ])
        ref = hurst_rs(vals)
        assert terminal.value == pytest.approx(ref.h, rel=1e-9)
        clear_tool_config_cache()

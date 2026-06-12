"""Tests for shared.operators.stationarity_adf — Track-A A5.

Covers the contract surface every standard operator must satisfy:

  - parity vs statsmodels adfuller (statistic exact; p-value, lags
    and critical values in lineage)
  - directional sanity: white noise strongly rejects a unit root
    (stat far below the 5% critical value); a random walk does not
  - the A5 emit-the-STATISTIC convention (RATIO units; metric_key)
  - NaN dropna + the interior-drop audit (n_interior_dropped)
  - refusals: non-Series; < 12 finite; zero variance
  - OPR8 params=None (zero-knob model); OPR14 determinism
  - OPR6 non-rates case; composition spread→stationarity_adf DAG
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel
from statsmodels.tsa.stattools import adfuller

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
from shared.operators.stationarity_adf import (
    StationarityAdfError,
    StationarityAdfParams,
    stationarity_adf,
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


_N = 120
_DATES = pd.bdate_range("2026-01-02", periods=_N)


def _noise(seed: int = 131):
    rng = np.random.RandomState(seed)
    vals = rng.randn(_N)
    return _series("x", dates=_DATES, values=vals), vals


# ===========================================================================
# 1. Parity + directional sanity
# ===========================================================================


class TestHappyPath:
    def test_matches_statsmodels_exactly(self):
        s, vals = _noise()
        out = stationarity_adf(s)
        ref = adfuller(vals, regression="c", autolag="AIC")
        assert out.value == pytest.approx(float(ref[0]), rel=1e-12)
        assert isinstance(out, ScalarMetric)  # OPR2 constant type
        assert out.units == TimeSeriesUnits.RATIO
        assert out.metric_key == "stationarity_adf__x"
        head = out.lineage.steps[-1]
        assert head.params["p_value"] == pytest.approx(float(ref[1]))
        assert head.params["lags_used"] == int(ref[2])
        assert head.params["critical_values"]["5%"] == pytest.approx(
            float(ref[4]["5%"]),
        )

    def test_white_noise_rejects_unit_root(self):
        s, _ = _noise()
        out = stationarity_adf(s)
        crit5 = out.lineage.steps[-1].params["critical_values"]["5%"]
        assert out.value < crit5  # strongly stationary

    def test_random_walk_does_not_reject(self):
        rng = np.random.RandomState(11)  # a clean no-reject draw
        walk = rng.randn(_N).cumsum()
        s = _series("rw", dates=_DATES, values=walk)
        out = stationarity_adf(s)
        crit5 = out.lineage.steps[-1].params["critical_values"]["5%"]
        assert out.value > crit5  # cannot reject the unit root

    def test_locked_spec_in_lineage(self):
        s, _ = _noise()
        out = stationarity_adf(s)
        head = out.lineage.steps[-1]
        assert head.params["regression"] == "c"
        assert head.params["autolag"] == "AIC"
        assert head.params["test_scope"] == "full_sample"


# ===========================================================================
# 2. NaN policy + interior-drop audit
# ===========================================================================


class TestNanAudit:
    def test_interior_gaps_disclosed(self):
        vals = np.random.RandomState(3).randn(_N)
        vals[40] = np.nan
        vals[41] = np.nan
        vals[:5] = np.nan  # edge — not interior
        s = _series("g", dates=_DATES, values=vals)
        out = stationarity_adf(s)
        head = out.lineage.steps[-1]
        assert head.params["n_dropped"] == 7
        assert head.params["n_interior_dropped"] == 2
        assert head.params["n_obs"] == _N - 7

    def test_clean_input_zero_interior(self):
        s, _ = _noise()
        out = stationarity_adf(s)
        assert out.lineage.steps[-1].params["n_interior_dropped"] == 0


# ===========================================================================
# 3. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(StationarityAdfError, match="must be a Series"):
            stationarity_adf([1.0])  # type: ignore[arg-type]

    def test_fewer_than_twelve_finite_refused(self):
        s = _series("few", dates=_DATES[:11], values=np.random.randn(11))
        with pytest.raises(StationarityAdfError, match="at least 12"):
            stationarity_adf(s)

    def test_zero_variance_refused(self):
        s = _series("const", dates=_DATES[:20], values=[5.0] * 20)
        with pytest.raises(StationarityAdfError, match="zero variance"):
            stationarity_adf(s)

    def test_perfect_ramp_refused(self):
        """Critic finding (OPR14): constant first differences make the
        ADF regressand zero-variance — the statistic would be float
        noise (a 120-row ramp probes at −8.77/p≈0, a confidently wrong
        'stationary' verdict on a pure trend).  Typed refusal."""
        s = _series(
            "ramp", dates=_DATES, values=np.arange(_N, dtype=float),
        )
        with pytest.raises(StationarityAdfError, match="first differences"):
            stationarity_adf(s)
        # The cumulative-over-constant composition shape too.
        s2 = _series(
            "cum", dates=_DATES,
            values=np.cumsum(np.full(_N, 2.5)),
        )
        with pytest.raises(StationarityAdfError, match="first differences"):
            stationarity_adf(s2)

    def test_params_none_equals_empty_params(self):
        s, _ = _noise()
        assert (
            stationarity_adf(s).lineage.head_hash
            == stationarity_adf(
                s, params=StationarityAdfParams(),
            ).lineage.head_hash
        )

    def test_extra_params_forbidden_at_schema(self):
        with pytest.raises(ValueError):
            StationarityAdfParams(regression="ct")  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        s, _ = _noise()
        assert (
            stationarity_adf(s).lineage.head_hash
            == stationarity_adf(s).lineage.head_hash
        )

    def test_lineage_extends_by_one_step(self):
        s, _ = _noise()
        out = stationarity_adf(s)
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        assert out.lineage.steps[-1].name == "stationarity_adf"


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(139)
    vals = np.sin(np.arange(_N) / 3.0) + rng.randn(_N) * 0.1
    s = _series("sensor", dates=_DATES, values=vals,
                units=TimeSeriesUnits.RATIO)
    out = stationarity_adf(s)
    ref = adfuller(vals, regression="c", autolag="AIC")
    assert out.value == pytest.approx(float(ref[0]), rel=1e-12)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 5. Composition — spread → stationarity_adf (the canonical ask)
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

        # 'Is the spread between A and B stationary?'
        wf = Workflow(
            workflow_id="adf_composition",
            nodes=[
                PrimitiveNode(
                    node_id="a", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "u1", "drift": 0.5,
                            "pattern_mult": 7919, "pattern_mod": 13},
                ),
                PrimitiveNode(
                    node_id="b", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "u2", "drift": 0.5,
                            "pattern_mult": 104729, "pattern_mod": 17},
                ),
                OperatorNode(
                    node_id="spread", operator_name="series_arithmetic",
                    params={"op": "subtract"},
                ),
                OperatorNode(
                    node_id="adf", operator_name="stationarity_adf",
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="a", target_node_id="spread",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="b", target_node_id="spread",
                             target_input_slot="right"),
                WorkflowEdge(source_node_id="spread", target_node_id="adf",
                             target_input_slot="series"),
            ],
            terminal_node_id="adf",
        )
        return wf, _resolve

    def test_type_gate_accepts_adf_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_spread_stationarity(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.RATIO
        n = 60
        s1 = np.array([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        s2 = np.array([
            100.0 + i * 0.5 + ((i * 104729) % 17) * 0.25 for i in range(n)
        ])
        spread = s1 - s2  # drift cancels — bounded periodic spread
        ref = adfuller(spread, regression="c", autolag="AIC")
        assert terminal.value == pytest.approx(float(ref[0]), rel=1e-9)
        clear_tool_config_cache()

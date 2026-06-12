"""Tests for shared.operators.fit_ou — Track-A A4 (model-fit opener).

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (value = half-life; the full OU
    fit in lineage)
  - directional sanity: a mean-reverting series fits; a trending one
    is REFUSED (the model-fit honesty crux)
  - COUNT units (not RATIO); metric_key fit_ou__<key>
  - NaN dropna + interior-drop audit (lag-based AR(1))
  - refusals: non-Series; < 12 finite rows; zero variance; not
    mean-reverting
  - OPR8 zero-knob params; OPR14 determinism
  - OPR6 non-rates case; composition primitive→fit_ou DAG
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
from shared.operators.fit_ou import (
    FitOuError,
    FitOuParams,
    fit_ou,
)
from shared.quant.ou import fit_ou_ar1


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
    start: str = "2020-01-01",
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


def _ou(seed=1, n=2000, phi=0.8, mu=5.0):
    rng = np.random.RandomState(seed)
    x = np.zeros(n)
    x[0] = mu
    for i in range(1, n):
        x[i] = mu * (1 - phi) + phi * x[i - 1] + rng.randn()
    return _series("x", values=x), x


# ===========================================================================
# 1. Parity + directional sanity
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_numeric(self):
        s, vals = _ou()
        out = fit_ou(s)
        ref = fit_ou_ar1(vals)
        assert out.value == pytest.approx(ref.half_life, rel=1e-12)
        assert isinstance(out, ScalarMetric)  # OPR2 constant type
        assert out.units == TimeSeriesUnits.COUNT  # half-life is a count
        assert out.metric_key == "fit_ou__x"
        head = out.lineage.steps[-1]
        assert head.params["half_life"] == pytest.approx(ref.half_life)
        assert head.params["phi"] == pytest.approx(ref.phi)
        assert head.params["equilibrium"] == pytest.approx(ref.mu)
        assert head.params["r_squared"] == pytest.approx(ref.r_squared)
        assert head.params["is_mean_reverting"] is True
        assert head.params["estimation_method"] == "ar1_ols"

    def test_half_life_sensible_for_known_phi(self):
        # phi=0.8 -> half_life = -ln2/ln(0.8) = 3.106 obs.
        s, _ = _ou(phi=0.8, n=5000)
        out = fit_ou(s)
        assert out.value == pytest.approx(3.106, rel=0.15)

    def test_full_sample_scope_in_lineage(self):
        s, _ = _ou()
        assert (
            fit_ou(s).lineage.steps[-1].params["test_scope"]
            == "full_sample"
        )


# ===========================================================================
# 2. NaN audit
# ===========================================================================


class TestNanAudit:
    def test_interior_gaps_disclosed(self):
        _s, vals = _ou()
        vals = vals.copy()
        vals[1000] = np.nan
        vals[-3:] = np.nan  # edge
        s = _series("g", values=vals)
        out = fit_ou(s)
        head = out.lineage.steps[-1]
        assert head.params["n_dropped"] == 4
        assert head.params["n_interior_dropped"] == 1
        ref = fit_ou_ar1(pd.Series(vals).dropna().to_numpy())
        assert out.value == pytest.approx(ref.half_life, rel=1e-12)


# ===========================================================================
# 3. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(FitOuError, match="must be a Series"):
            fit_ou(0.5)  # type: ignore[arg-type]

    def test_too_few_rows_refused(self):
        s = _series("few", values=np.linspace(0, 1, 8))
        with pytest.raises(FitOuError, match="at least 12"):
            fit_ou(s)

    def test_zero_variance_refused(self):
        s = _series("const", values=[2.0] * 30)
        with pytest.raises(FitOuError, match="zero variance"):
            fit_ou(s)

    def test_trending_series_refused_as_not_mean_reverting(self):
        s = _series("ramp", values=np.arange(200, dtype=float))
        with pytest.raises(FitOuError, match="NOT mean-reverting"):
            fit_ou(s)

    def test_explosive_series_refused(self):
        rng = np.random.RandomState(1)
        n = 200
        x = np.zeros(n)
        x[0] = 1.0
        for i in range(1, n):
            x[i] = 1.05 * x[i - 1] + 0.01 * rng.randn()
        s = _series("exp", values=x)
        with pytest.raises(FitOuError, match="NOT mean-reverting"):
            fit_ou(s)

    def test_params_none_equals_empty_params(self):
        s, _ = _ou()
        assert (
            fit_ou(s).lineage.head_hash
            == fit_ou(s, params=FitOuParams()).lineage.head_hash
        )

    def test_extra_params_forbidden_at_schema(self):
        with pytest.raises(ValueError):
            FitOuParams(method="mle")  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        s, _ = _ou()
        assert (
            fit_ou(s).lineage.head_hash == fit_ou(s).lineage.head_hash
        )

    def test_lineage_extends_by_one_step(self):
        s, _ = _ou()
        out = fit_ou(s)
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        assert out.lineage.steps[-1].name == "fit_ou"


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    s, vals = _ou(seed=181)
    s = _series("sensor", values=vals, units=TimeSeriesUnits.RATIO)
    out = fit_ou(s)
    ref = fit_ou_ar1(vals)
    assert out.value == pytest.approx(ref.half_life, rel=1e-12)
    assert out.units == TimeSeriesUnits.COUNT


# ===========================================================================
# 5. Composition — primitive → fit_ou (mean-reverting series)
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
            n_rows: int = 200
            base_value: float = 100.0
            phi: float = 0.7
            pattern_mult: int = 7919
            pattern_mod: int = 13
            units: str = "bps"

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            # Deterministic mean-reverting AR(1) around base_value.
            bdays = pd.bdate_range("2024-01-01", periods=params.n_rows)
            values = []
            x = params.base_value
            for i in range(params.n_rows):
                innov = (((i * params.pattern_mult) % params.pattern_mod) - 6) * 0.5
                x = params.base_value * (1 - params.phi) + params.phi * x + innov
                values.append(x)
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
            "  what_it_does: deterministic AR(1) for composition tests.\n"
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
            workflow_id="fit_ou_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="ou", operator_name="fit_ou",
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="ou",
                             target_input_slot="series"),
            ],
            terminal_node_id="ou",
        )
        return wf, _resolve

    def test_type_gate_accepts_fit_ou_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_half_life(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.COUNT
        # Independent parity: re-build the synthetic AR(1) and fit.
        n, phi, base = 200, 0.7, 100.0
        vals = []
        x = base
        for i in range(n):
            innov = (((i * 7919) % 13) - 6) * 0.5
            x = base * (1 - phi) + phi * x + innov
            vals.append(x)
        ref = fit_ou_ar1(np.array(vals))
        assert terminal.value == pytest.approx(ref.half_life, rel=1e-9)
        clear_tool_config_cache()

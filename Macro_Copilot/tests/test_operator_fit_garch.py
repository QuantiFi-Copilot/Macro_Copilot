"""Tests for shared.operators.fit_garch — Track-A A4.

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (the in-sample vol path; the
    fitted params in lineage)
  - THE FORECAST GUARD: output index == input index, no sigma_{T+1}
  - COUNT-free units passthrough; series_key garch_vol__<key>
  - two-tier NaN: interior refused; edge warmup tolerated + passed
    through
  - refusals: non-Series; < 12 finite; zero variance; interior NaN
  - OPR8 zero-knob params; OPR14 determinism; OPR6 non-rates;
    composition primitive->fit_garch DAG
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
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.fit_garch import (
    FitGarchError,
    FitGarchParams,
    fit_garch,
)
from shared.quant.garch import fit_garch_11


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
    start: str = "2018-01-01",
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


def _sim_garch(n=2000, omega=0.05, alpha=0.08, beta=0.90, seed=0):
    rng = np.random.RandomState(seed)
    z = rng.randn(n)
    eps = np.zeros(n)
    s2 = np.zeros(n)
    s2[0] = omega / (1 - alpha - beta)
    eps[0] = np.sqrt(s2[0]) * z[0]
    for t in range(1, n):
        s2[t] = omega + alpha * eps[t - 1] ** 2 + beta * s2[t - 1]
        eps[t] = np.sqrt(s2[t]) * z[t]
    return eps


# ===========================================================================
# 1. Parity + the forecast guard
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_and_in_sample_only(self):
        vals = _sim_garch()
        s = _series("x", values=vals)
        out = fit_garch(s)
        ref = fit_garch_11(vals)
        assert isinstance(out, Series)  # OPR2 constant output type
        # THE FORECAST GUARD: output index == input index (no sigma_T+1).
        assert out.payload.index.equals(s.payload.index)
        assert len(out.payload) == len(s.payload)
        np.testing.assert_allclose(
            out.payload.to_numpy(), ref.conditional_vol,
        )
        assert out.units == TimeSeriesUnits.BPS  # passthrough
        assert out.series_key == "garch_vol__x"
        head = out.lineage.steps[-1]
        assert head.params["omega"] == pytest.approx(ref.omega)
        assert head.params["alpha"] == pytest.approx(ref.alpha)
        assert head.params["beta"] == pytest.approx(ref.beta)
        assert head.params["persistence"] == pytest.approx(ref.persistence)
        assert head.params["converged"] is True

    def test_lineage_records_locks_and_scope(self):
        s = _series("x", values=_sim_garch())
        out = fit_garch(s)
        head = out.lineage.steps[-1]
        assert head.name == "fit_garch"
        assert head.params["order"] == "(1,1)"
        assert head.params["distribution"] == "gaussian"
        assert head.params["mean_model"] == "constant"
        assert head.params["estimation_method"] == "gaussian_mle"
        assert head.params["differenced"] is False
        assert head.params["fit_scope"] == "full_sample"

    def test_low_sample_flag(self):
        s = _series("x", values=_sim_garch(n=60))
        out = fit_garch(s)
        assert out.lineage.steps[-1].params["low_sample_warning"] is True


# ===========================================================================
# 2. Two-tier NaN
# ===========================================================================


class TestNan:
    def test_interior_nan_refused_with_remedy(self):
        vals = _sim_garch()
        vals[1000] = np.nan
        s = _series("g", values=vals)
        with pytest.raises(FitGarchError, match="INTERIOR"):
            fit_garch(s)

    def test_edge_warmup_nan_tolerated(self):
        vals = _sim_garch()
        vals[:10] = np.nan
        vals[-4:] = np.nan
        s = _series("w", values=vals)
        out = fit_garch(s)
        assert out.payload.iloc[:10].isna().all()
        assert out.payload.iloc[-4:].isna().all()
        assert out.payload.iloc[10:-4].notna().all()
        head = out.lineage.steps[-1]
        assert head.params["n_leading_nan"] == 10
        assert head.params["n_trailing_nan"] == 4
        # Parity on the interior block.
        ref = fit_garch_11(vals[10:-4])
        np.testing.assert_allclose(
            out.payload.iloc[10:-4].to_numpy(), ref.conditional_vol,
        )


# ===========================================================================
# 3. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(FitGarchError, match="must be a Series"):
            fit_garch(0.5)  # type: ignore[arg-type]

    def test_too_few_rows_refused(self):
        s = _series("few", values=np.linspace(-1, 1, 8))
        with pytest.raises(FitGarchError, match="at least 12"):
            fit_garch(s)

    def test_zero_variance_refused(self):
        s = _series("const", values=[2.0] * 40)
        with pytest.raises(FitGarchError, match="zero variance"):
            fit_garch(s)

    def test_params_none_equals_empty_params(self):
        s = _series("x", values=_sim_garch())
        assert (
            fit_garch(s).lineage.head_hash
            == fit_garch(s, params=FitGarchParams()).lineage.head_hash
        )

    def test_extra_params_forbidden_at_schema(self):
        with pytest.raises(ValueError):
            FitGarchParams(order="(2,2)")  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        s = _series("x", values=_sim_garch())
        assert (
            fit_garch(s).lineage.head_hash == fit_garch(s).lineage.head_hash
        )

    def test_lineage_extends_by_one_step(self):
        s = _series("x", values=_sim_garch())
        out = fit_garch(s)
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1


# ===========================================================================
# 4. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    vals = _sim_garch(seed=181)
    s = _series("sensor", values=vals, units=TimeSeriesUnits.RATIO)
    out = fit_garch(s)
    ref = fit_garch_11(vals)
    np.testing.assert_allclose(out.payload.to_numpy(), ref.conditional_vol)
    assert out.units == TimeSeriesUnits.RATIO


# ===========================================================================
# 5. Composition — primitive → fit_garch (stationary returns synthetic)
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
            seed: int = 0
            omega: float = 0.05
            alpha: float = 0.08
            beta: float = 0.90
            units: str = "bps"

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            # A deterministic GARCH-like returns series (seeded).
            rng = np.random.RandomState(params.seed)
            z = rng.randn(params.n_rows)
            eps = np.zeros(params.n_rows)
            s2 = np.zeros(params.n_rows)
            s2[0] = params.omega / (1 - params.alpha - params.beta)
            eps[0] = np.sqrt(s2[0]) * z[0]
            for t in range(1, params.n_rows):
                s2[t] = (params.omega + params.alpha * eps[t - 1] ** 2
                         + params.beta * s2[t - 1])
                eps[t] = np.sqrt(s2[t]) * z[t]
            bdays = pd.bdate_range("2022-01-01", periods=params.n_rows)
            rows = [
                TimeSeriesRow(date=d.strftime("%Y-%m-%d"), value=float(v))
                for d, v in zip(bdays, eps)
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
            "  what_it_does: deterministic GARCH returns for tests.\n"
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
            workflow_id="fit_garch_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="g", operator_name="fit_garch",
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="g",
                             target_input_slot="series"),
            ],
            terminal_node_id="g",
        )
        return wf, _resolve

    def test_type_gate_accepts_fit_garch_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_vol_path(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.units == TimeSeriesUnits.BPS
        # In-sample: the vol path spans the 300 input rows, no projection.
        assert len(terminal.payload) == 300
        assert terminal.payload.notna().all()
        clear_tool_config_cache()

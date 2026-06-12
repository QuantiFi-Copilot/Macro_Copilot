"""Tests for shared.operators.streak — Track-A A6.

Covers the contract surface every standard operator must satisfy:

  - the signed run-length semantics pinned exactly on hand-worked
    sequences (sign changes, zero boundaries, NaN breaks)
  - COUNT units regardless of input units (the ordinal precedent)
  - longest-run lineage diagnostics; design locks lineage-stamped
  - refusals: non-Series; all-NaN
  - OPR8 params=None (zero-knob model); OPR14 determinism
  - OPR6 non-rates case; composition subtract→streak→last DAG (the
    'current consecutive days below level' read)
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
)
from shared.operators.streak import StreakError, StreakParams, streak


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


_DATES_12 = pd.bdate_range("2026-01-02", periods=12)


# ===========================================================================
# 1. Semantics pins
# ===========================================================================


class TestSemantics:
    def test_signed_runs_and_sign_changes(self):
        vals = [1.0, 2.0, 3.0, -1.0, -2.0, 4.0, 5.0, -3.0]
        s = _series("x", dates=_DATES_12[:8], values=vals)
        out = streak(s)
        np.testing.assert_array_equal(
            out.payload.to_numpy(),
            [1.0, 2.0, 3.0, -1.0, -2.0, 1.0, 2.0, -1.0],
        )
        assert isinstance(out, Series)  # OPR2 constant output type

    def test_zero_is_a_boundary_not_a_member(self):
        """The Warden's pinned case: zero kills a run; the next sign
        starts at ±1 — never a continuation through zero."""
        vals = [-2.0, -1.0, 0.0, 1.0, 2.0]
        s = _series("z", dates=_DATES_12[:5], values=vals)
        out = streak(s)
        np.testing.assert_array_equal(
            out.payload.to_numpy(), [-1.0, -2.0, 0.0, 1.0, 2.0],
        )

    def test_nan_emits_nan_and_breaks_the_run(self):
        vals = [1.0, 1.0, np.nan, 1.0, 1.0]
        s = _series("g", dates=_DATES_12[:5], values=vals)
        out = streak(s)
        arr = out.payload.to_numpy()
        assert arr[0] == 1.0 and arr[1] == 2.0
        assert np.isnan(arr[2])
        assert arr[3] == 1.0 and arr[4] == 2.0  # restarted at +1

    def test_leading_nan_then_run(self):
        vals = [np.nan, np.nan, -1.0, -2.0]
        s = _series("h", dates=_DATES_12[:4], values=vals)
        out = streak(s)
        arr = out.payload.to_numpy()
        assert np.isnan(arr[0]) and np.isnan(arr[1])
        assert arr[2] == -1.0 and arr[3] == -2.0

    def test_count_units_regardless_of_input(self):
        vals = [1.0, 2.0, -1.0]
        for units in (TimeSeriesUnits.BPS, TimeSeriesUnits.Z_SCORE):
            s = _series("u", dates=_DATES_12[:3], values=vals, units=units)
            out = streak(s)
            assert out.units == TimeSeriesUnits.COUNT
            head = out.lineage.steps[-1]
            assert head.params["input_units"] == units.value
            assert head.params["output_units"] == "count"

    def test_longest_runs_in_lineage(self):
        vals = [1.0, 1.0, 1.0, -1.0, -1.0, 1.0]
        s = _series("L", dates=_DATES_12[:6], values=vals)
        out = streak(s)
        head = out.lineage.steps[-1]
        assert head.params["longest_positive_run"] == 3
        assert head.params["longest_negative_run"] == 2
        assert head.params["nan_policy"] == "break"

    def test_metadata_passthrough_and_key(self):
        vals = [1.0, -1.0, 1.0]
        s = _series("y", dates=_DATES_12[:3], values=vals, frequency="B")
        out = streak(s)
        assert out.frequency == "B"
        assert out.missingness_policy == s.missingness_policy
        assert out.series_key == "streak__y"


# ===========================================================================
# 2. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(StreakError, match="must be a Series"):
            streak("nope")  # type: ignore[arg-type]

    def test_all_nan_refused(self):
        s = _series("nn", dates=_DATES_12[:4], values=[np.nan] * 4)
        with pytest.raises(StreakError, match="no finite values"):
            streak(s)

    def test_params_none_equals_empty_params(self):
        vals = [1.0, -1.0, 1.0]
        s = _series("p", dates=_DATES_12[:3], values=vals)
        assert (
            streak(s).lineage.head_hash
            == streak(s, params=StreakParams()).lineage.head_hash
        )

    def test_extra_params_forbidden_at_schema(self):
        with pytest.raises(ValueError):
            StreakParams(threshold=0.5)  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        vals = [1.0, 2.0, -1.0, 0.0, 3.0]
        s = _series("d", dates=_DATES_12[:5], values=vals)
        assert streak(s).lineage.head_hash == streak(s).lineage.head_hash

    def test_lineage_extends_by_one_step(self):
        vals = [1.0, -1.0]
        s = _series("ln", dates=_DATES_12[:2], values=vals)
        out = streak(s)
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        assert out.lineage.steps[-1].name == "streak"


# ===========================================================================
# 3. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    rng = np.random.RandomState(113)
    vals = rng.randn(30)
    s = _series(
        "sensor", dates=pd.bdate_range("2026-02-02", periods=30),
        values=vals, units=TimeSeriesUnits.RATIO,
    )
    out = streak(s)
    # Independent reference: per-position signed run length.
    expected = []
    run = 0
    for v in vals:
        if v > 0:
            run = run + 1 if run > 0 else 1
        elif v < 0:
            run = run - 1 if run < 0 else -1
        else:
            run = 0
        expected.append(float(run))
    np.testing.assert_array_equal(out.payload.to_numpy(), expected)
    assert out.units == TimeSeriesUnits.COUNT


# ===========================================================================
# 4. Composition — subtract level → streak → last ('current streak')
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
            n_rows: int = 30
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

        # 'How many consecutive rows has series A been above the flat
        # reference level?' — the level leg is a second (constant)
        # series, since subtract is strict two-Series unit algebra.
        wf = Workflow(
            workflow_id="streak_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                PrimitiveNode(
                    node_id="lvl", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "level", "base_value": 105.0,
                            "drift": 0.0, "pattern_mod": 1},
                ),
                OperatorNode(
                    node_id="cond", operator_name="series_arithmetic",
                    params={"op": "subtract"},
                ),
                OperatorNode(node_id="st", operator_name="streak"),
                OperatorNode(
                    node_id="cur", operator_name="summarize_series",
                    params={"statistic": "last", "dispersion": "none"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="cond",
                             target_input_slot="left"),
                WorkflowEdge(source_node_id="lvl", target_node_id="cond",
                             target_input_slot="right"),
                WorkflowEdge(source_node_id="cond", target_node_id="st",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="st", target_node_id="cur",
                             target_input_slot="series"),
            ],
            terminal_node_id="cur",
        )
        return wf, _resolve

    def test_type_gate_accepts_streak_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_current_streak_above_level(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, ScalarMetric)
        assert terminal.units == TimeSeriesUnits.COUNT
        n = 30
        vals = np.array([
            100.0 + i * 0.5 + ((i * 7919) % 13) * 0.25 for i in range(n)
        ])
        cond = vals - 105.0
        run = 0
        for v in cond:
            if v > 0:
                run = run + 1 if run > 0 else 1
            elif v < 0:
                run = run - 1 if run < 0 else -1
            else:
                run = 0
        assert terminal.value == pytest.approx(float(run))
        clear_tool_config_cache()

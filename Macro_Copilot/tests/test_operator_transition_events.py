"""Tests for shared.operators.transition_events — Track-A A6.

Covers the contract surface every standard operator must satisfy:

  - the event convention pinned on hand-worked label sequences
    (stamped at the first bar of the NEW label; first row never an
    event; from/to metadata exact)
  - NaN suppression (no events across gaps); repeated labels emit
    nothing; zero transitions → valid EMPTY EventSet
  - integer-valued enforcement (soft states refused, never rounded)
  - frequency propagation to the EventSet; lineage label inventory
  - refusals: non-Series; all-NaN
  - OPR8 params=None (zero-knob model); OPR14 determinism
  - OPR6 non-rates case; composition transition_events→event_windows
    DAG (the canonical event-study consumer)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from shared.artifacts import (
    AdapterStep,
    CleanSingleSeriesV1,
    EventSet,
    FetchStep,
    Lineage,
    Series,
    TimeSeriesUnits,
)
from shared.config.operator_config import clear_operator_config_cache
from shared.operators.transition_events import (
    TransitionEventsError,
    TransitionEventsParams,
    transition_events,
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
    units: TimeSeriesUnits = TimeSeriesUnits.COUNT,
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


_DATES = pd.bdate_range("2026-01-02", periods=12)


# ===========================================================================
# 1. Event-convention pins
# ===========================================================================


class TestSemantics:
    def test_transitions_stamped_at_first_bar_of_new_label(self):
        vals = [1.0, 1.0, 1.0, 2.0, 2.0, 1.0]
        s = _series("x", dates=_DATES[:6], values=vals)
        out = transition_events(s)
        assert isinstance(out, EventSet)  # OPR2 constant output type
        assert out.event_dates == [_DATES[3], _DATES[5]]
        assert out.per_event_metadata == [
            {"from_value": 1, "to_value": 2},
            {"from_value": 2, "to_value": 1},
        ]
        np.testing.assert_array_equal(
            out.mask.to_numpy(),
            [False, False, False, True, False, True],
        )

    def test_first_row_never_an_event(self):
        vals = [5.0, 5.0, 5.0]
        s = _series("f", dates=_DATES[:3], values=vals)
        out = transition_events(s)
        assert out.event_dates == []
        assert out.per_event_metadata == []
        assert not out.mask.any()

    def test_nan_suppresses_events_across_gaps(self):
        # 1 → NaN → 2: the change cannot be dated to adjacent rows.
        vals = [1.0, np.nan, 2.0, 2.0, 3.0]
        s = _series("g", dates=_DATES[:5], values=vals)
        out = transition_events(s)
        assert out.event_dates == [_DATES[4]]  # only the 2→3 change
        assert out.per_event_metadata == [
            {"from_value": 2, "to_value": 3},
        ]

    def test_non_contiguous_labels_fine(self):
        vals = [2.0, 7.0, 2.0, 7.0]
        s = _series("nc", dates=_DATES[:4], values=vals)
        out = transition_events(s)
        assert len(out.event_dates) == 3
        head = out.lineage.steps[-1]
        assert head.params["labels"] == [2, 7]
        assert head.params["n_distinct_labels"] == 2

    def test_negative_labels_fine(self):
        vals = [-1.0, -1.0, 1.0]
        s = _series("neg", dates=_DATES[:3], values=vals)
        out = transition_events(s)
        assert out.per_event_metadata == [
            {"from_value": -1, "to_value": 1},
        ]

    def test_frequency_propagates(self):
        vals = [1.0, 2.0]
        s = _series("fr", dates=_DATES[:2], values=vals, frequency="B")
        out = transition_events(s)
        assert out.frequency == "B"
        assert out.source_series_key == "fr"


# ===========================================================================
# 2. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_non_series_input_raises(self):
        with pytest.raises(TransitionEventsError, match="must be a Series"):
            transition_events(42)  # type: ignore[arg-type]

    def test_all_nan_refused(self):
        s = _series("nn", dates=_DATES[:4], values=[np.nan] * 4)
        with pytest.raises(TransitionEventsError, match="no finite values"):
            transition_events(s)

    def test_soft_states_refused_never_rounded(self):
        vals = [0.0, 0.9, 1.0]
        s = _series("soft", dates=_DATES[:3], values=vals)
        with pytest.raises(
            TransitionEventsError, match="integer-valued",
        ):
            transition_events(s)

    def test_params_none_equals_empty_params(self):
        vals = [1.0, 2.0, 1.0]
        s = _series("p", dates=_DATES[:3], values=vals)
        assert (
            transition_events(s).lineage.head_hash
            == transition_events(
                s, params=TransitionEventsParams(),
            ).lineage.head_hash
        )

    def test_extra_params_forbidden_at_schema(self):
        with pytest.raises(ValueError):
            TransitionEventsParams(ignore=[1])  # type: ignore[call-arg]

    def test_rerun_is_deterministic(self):
        vals = [1.0, 2.0, 2.0, 3.0]
        s = _series("d", dates=_DATES[:4], values=vals)
        assert (
            transition_events(s).lineage.head_hash
            == transition_events(s).lineage.head_hash
        )

    def test_lineage_extends_by_one_step_with_convention(self):
        vals = [1.0, 2.0]
        s = _series("ln", dates=_DATES[:2], values=vals)
        out = transition_events(s)
        assert len(out.lineage.steps) == len(s.lineage.steps) + 1
        head = out.lineage.steps[-1]
        assert head.name == "transition_events"
        assert "NEW label" in head.params["event_convention"]
        assert head.params["n_events"] == 1


# ===========================================================================
# 3. OPR6 — finance-blindness (non-rates synthetic labels)
# ===========================================================================


def test_runs_on_non_rates_synthetic_labels():
    rng = np.random.RandomState(127)
    vals = rng.randint(0, 3, size=30).astype(float)
    s = _series(
        "sensor_state", dates=pd.bdate_range("2026-02-02", periods=30),
        values=vals, units=TimeSeriesUnits.RATIO,
    )
    out = transition_events(s)
    expected_dates = [
        s.payload.index[i]
        for i in range(1, 30)
        if vals[i] != vals[i - 1]
    ]
    assert out.event_dates == expected_dates


# ===========================================================================
# 4. Composition — transition_events → event_windows (event study)
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
            pattern_mult: int = 7919
            pattern_mod: int = 13
            units: str = "bps"
            quantize_levels: int = 0  # >0: emit integer labels

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            bdays = pd.bdate_range("2026-04-01", periods=params.n_rows)
            if params.quantize_levels > 0:
                values = [
                    float((i // 10) % params.quantize_levels)
                    for i in range(params.n_rows)
                ]
            else:
                values = [
                    params.base_value
                    + i * params.drift
                    + ((i * params.pattern_mult) % params.pattern_mod)
                    * 0.25
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

        # Event study: behaviour of series B around the label changes
        # of a discrete state series.
        wf = Workflow(
            workflow_id="transition_composition",
            nodes=[
                PrimitiveNode(
                    node_id="labels",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "state", "units": "count",
                            "quantize_levels": 2},
                ),
                PrimitiveNode(
                    node_id="target",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "b"},
                ),
                OperatorNode(
                    node_id="ev", operator_name="transition_events",
                ),
                OperatorNode(
                    node_id="win", operator_name="event_windows",
                    params={"pre_window": 2, "post_window": 2},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="labels", target_node_id="ev",
                             target_input_slot="series"),
                WorkflowEdge(source_node_id="ev", target_node_id="win",
                             target_input_slot="events"),
                WorkflowEdge(source_node_id="target", target_node_id="win",
                             target_input_slot="target"),
            ],
            terminal_node_id="win",
        )
        return wf, _resolve

    def test_type_gate_accepts_transition_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_event_study_around_changes(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        ev = result.node_artifacts["ev"]
        assert isinstance(ev, EventSet)
        # labels flip every 10 rows over 40 rows → changes at 10,20,30.
        assert len(ev.event_dates) == 3
        assert ev.per_event_metadata[0] == {"from_value": 0, "to_value": 1}
        terminal = result.terminal_artifact
        assert type(terminal).__name__ == "WindowedPanel"
        clear_tool_config_cache()

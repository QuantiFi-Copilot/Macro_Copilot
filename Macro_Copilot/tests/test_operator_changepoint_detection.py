"""Tests for shared.operators.changepoint_detection — Track-A A4.

Covers the contract surface every standard operator must satisfy:

  - parity vs the shared/quant numeric (break dates = mapped
    breakpoints; gain + segment means in per-event metadata)
  - planted recovery + the honest partial/empty path (fewer events
    than requested, never a refusal — the EventSet honesty divergence)
  - EventSet ART11 (event_dates == mask True; metadata length match)
  - two-tier NaN: interior refused; edge warmup tolerated
  - refusals: non-Series; params=None (n_changepoints); too few; zero
    variance; interior NaN
  - full-sample look-ahead stamp; OPR8 min_size config default;
    OPR14 determinism; OPR6 non-rates; composition DAG
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
from shared.config.operator_config import (
    clear_operator_config_cache,
    load_operator_config,
)
from shared.operators.changepoint_detection import (
    CONFIG_PATH,
    ChangepointDetectionError,
    ChangepointDetectionParams,
    changepoint_detection,
)
from shared.quant.changepoint import binary_segmentation


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


def _planted(seed=0):
    rng = np.random.RandomState(seed)
    return np.concatenate([
        rng.randn(100) * 0.3,
        5.0 + rng.randn(150) * 0.3,
        -3.0 + rng.randn(150) * 0.3,
    ])


# ===========================================================================
# 1. Parity + planted recovery
# ===========================================================================


class TestHappyPath:
    def test_matches_quant_and_recovers_breaks(self):
        vals = _planted()
        s = _series("x", values=vals)
        out = changepoint_detection(
            s, params=ChangepointDetectionParams(n_changepoints=2),
        )
        assert isinstance(out, EventSet)  # OPR2 constant output type
        ref = binary_segmentation(vals, n_changepoints=2, min_size=2)
        expected_dates = [s.payload.index[bp] for bp in ref.breakpoints]
        assert out.event_dates == expected_dates
        assert out.source_series_key == "x"
        # per-event metadata carries gain + segment means.
        assert out.per_event_metadata[0]["gain"] == pytest.approx(ref.gains[0])
        assert out.per_event_metadata[0]["segment_mean_after"] == \
            pytest.approx(ref.segment_means[1], abs=0.2)

    def test_art11_mask_matches_event_dates(self):
        s = _series("x", values=_planted())
        out = changepoint_detection(
            s, params=ChangepointDetectionParams(n_changepoints=2),
        )
        mask_dates = list(out.mask.index[out.mask.to_numpy()])
        assert out.event_dates == mask_dates
        assert len(out.per_event_metadata) == len(out.event_dates) == 2

    def test_lineage_records_locks_and_scope(self):
        s = _series("x", values=_planted())
        out = changepoint_detection(
            s, params=ChangepointDetectionParams(n_changepoints=2),
        )
        head = out.lineage.steps[-1]
        assert head.name == "changepoint_detection"
        assert head.params["cost_model"] == "l2_mean_shift"
        assert head.params["algorithm"] == "binary_segmentation"
        assert head.params["detection_scope"] == "full_sample"
        assert head.params["n_changepoints_requested"] == 2
        assert head.params["n_changepoints_found"] == 2
        assert head.params["n_events"] == 2


# ===========================================================================
# 2. The honest partial / empty path (EventSet has an empty channel)
# ===========================================================================


def test_fewer_breaks_than_requested_is_not_a_refusal():
    # One real break, request 3 -> emits 1 event, never raises.
    s = _series("step", values=np.array([0.0] * 50 + [5.0] * 50))
    out = changepoint_detection(
        s, params=ChangepointDetectionParams(n_changepoints=3),
    )
    assert isinstance(out, EventSet)
    assert len(out.event_dates) == 1
    assert out.lineage.steps[-1].params["n_changepoints_found"] == 1


# ===========================================================================
# 3. Two-tier NaN
# ===========================================================================


class TestNan:
    def test_interior_nan_refused_with_remedy(self):
        vals = _planted()
        vals[120] = np.nan
        s = _series("g", values=vals)
        with pytest.raises(ChangepointDetectionError, match="INTERIOR"):
            changepoint_detection(
                s, params=ChangepointDetectionParams(n_changepoints=2),
            )

    def test_edge_warmup_nan_tolerated(self):
        vals = _planted()
        vals[:10] = np.nan  # leading warmup
        vals[-5:] = np.nan  # trailing
        s = _series("w", values=vals)
        out = changepoint_detection(
            s, params=ChangepointDetectionParams(n_changepoints=2),
        )
        head = out.lineage.steps[-1]
        assert head.params["n_leading_nan"] == 10
        assert head.params["n_trailing_nan"] == 5
        # Edges are never events.
        assert out.mask.iloc[:10].sum() == 0
        assert out.mask.iloc[-5:].sum() == 0


# ===========================================================================
# 4. OPR13 refusals + OPR8/OPR14
# ===========================================================================


class TestRefusalsAndDiscipline:
    def test_params_none_refused_naming_field(self):
        s = _series("x", values=_planted())
        with pytest.raises(ChangepointDetectionError, match="n_changepoints"):
            changepoint_detection(s)

    def test_non_series_input_raises(self):
        with pytest.raises(ChangepointDetectionError, match="must be a Series"):
            changepoint_detection(
                0.5,  # type: ignore[arg-type]
                params=ChangepointDetectionParams(n_changepoints=1),
            )

    def test_too_few_rows_refused(self):
        s = _series("few", values=np.linspace(0, 1, 8))
        with pytest.raises(ChangepointDetectionError, match="at least"):
            changepoint_detection(
                s, params=ChangepointDetectionParams(n_changepoints=2),
            )

    def test_zero_variance_refused(self):
        s = _series("const", values=[2.0] * 40)
        with pytest.raises(ChangepointDetectionError, match="zero variance"):
            changepoint_detection(
                s, params=ChangepointDetectionParams(n_changepoints=1),
            )

    def test_n_changepoints_bounds_at_schema(self):
        with pytest.raises(ValueError):
            ChangepointDetectionParams(n_changepoints=0)
        with pytest.raises(ValueError):
            ChangepointDetectionParams()  # type: ignore[call-arg]

    def test_min_size_default_resolves_from_config(self):
        cfg = load_operator_config(CONFIG_PATH)
        assert cfg.default_value("min_size") == 2
        # None resolves to the config default; an explicit 2 matches.
        s = _series("x", values=_planted())
        h_auto = changepoint_detection(
            s, params=ChangepointDetectionParams(n_changepoints=2),
        ).lineage.head_hash
        h_explicit = changepoint_detection(
            s, params=ChangepointDetectionParams(n_changepoints=2, min_size=2),
        ).lineage.head_hash
        assert h_auto == h_explicit

    def test_rerun_is_deterministic(self):
        s = _series("x", values=_planted())
        p = ChangepointDetectionParams(n_changepoints=2)
        assert (
            changepoint_detection(s, params=p).lineage.head_hash
            == changepoint_detection(s, params=p).lineage.head_hash
        )


# ===========================================================================
# 5. OPR6 — finance-blindness (non-rates synthetic data)
# ===========================================================================


def test_runs_on_non_rates_synthetic_data():
    s = _series("sensor", values=_planted(seed=7),
                units=TimeSeriesUnits.RATIO)
    out = changepoint_detection(
        s, params=ChangepointDetectionParams(n_changepoints=2),
    )
    assert isinstance(out, EventSet)
    assert len(out.event_dates) == 2


# ===========================================================================
# 6. Composition — primitive → changepoint_detection (step series)
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
            n_rows: int = 120
            base_value: float = 100.0
            shift: float = 10.0
            pattern_mult: int = 7919
            pattern_mod: int = 13
            units: str = "bps"

        class _SynthMetrics(BaseModel):
            as_of_date: str

        class _SynthOutput(BaseModel):
            current_metrics: _SynthMetrics
            time_series: TimeSeries

        def _synth(*, engine, params: _SynthInput, config) -> dict:
            # A clear step at the midpoint + small deterministic noise.
            bdays = pd.bdate_range("2024-01-01", periods=params.n_rows)
            half = params.n_rows // 2
            values = [
                params.base_value
                + (params.shift if i >= half else 0.0)
                + (((i * params.pattern_mult) % params.pattern_mod) - 6) * 0.1
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
            "  what_it_does: deterministic step for composition tests.\n"
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
            workflow_id="changepoint_composition",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="cp", operator_name="changepoint_detection",
                    params={"n_changepoints": 1},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="p", target_node_id="cp",
                             target_input_slot="series"),
            ],
            terminal_node_id="cp",
        )
        return wf, _resolve

    def test_type_gate_accepts_changepoint_dag(self, tmp_path):
        from shared.workflow import validate_workflow
        wf, resolver = self._dag_and_resolver(tmp_path)
        validate_workflow(wf, primitive_resolver=resolver)

    def test_dag_executes_break_detection(self, tmp_path):
        from shared.config import clear_tool_config_cache
        from shared.workflow import execute_workflow

        clear_tool_config_cache()
        wf, resolver = self._dag_and_resolver(tmp_path)
        result = execute_workflow(wf, engine=None, primitive_resolver=resolver)
        terminal = result.terminal_artifact
        assert isinstance(terminal, EventSet)
        # The step is at the midpoint (index 60 of 120) -> one break there.
        assert len(terminal.event_dates) == 1
        assert terminal.per_event_metadata[0]["break_index"] == 60
        clear_tool_config_cache()

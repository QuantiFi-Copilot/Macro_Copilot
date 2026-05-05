"""tests/test_workflow_substrate.py — workflow substrate test suite.

Phase 2 PR 3.  Covers ``shared/workflow/{types,validate,executor,
result,registry}.py`` end-to-end with synthetic primitives + real
operators.  Tests are fully offline.

Sections:
  1. Schema construction (Workflow / nodes / edges) — happy path
     + construction-time guards (uniqueness, terminal, edge endpoints).
  2. Validation (``validate_workflow``) — cycles, slot existence,
     slot completeness, type compatibility, unknown operators,
     primitive resolvability.
  3. ``topological_order`` — correctness on diamond + chain
     shapes.
  4. Executor — synthetic primitive (no DB) + real operators
     end-to-end.  Lineage chain extension across the DAG.
  5. Executor refusals — operator-side failures wrapped as
     WorkflowExecutionError; non-artifact returns rejected.
  6. WorkflowResult contract — terminal artifact, lineage summary,
     intermediate node-artifact map.
  7. Finance-blindness structural test — substrate must NOT
     transitively import ``rates_agent.*``.
"""

from __future__ import annotations

import importlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Type
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from shared.artifacts import (
    EventSet,
    Lineage,
    PrimitiveStep,
    Series,
    SeriesSet,
    TimeSeriesUnits,
    WindowedPanel,
)
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.workflow import (
    OPERATOR_REGISTRY,
    OperatorNode,
    PrimitiveNode,
    PrimitiveResolver,
    PrimitiveSpec,
    Workflow,
    WorkflowEdge,
    WorkflowExecutionError,
    WorkflowResult,
    WorkflowValidationError,
    execute_workflow,
    topological_order,
    validate_workflow,
)


# ---------------------------------------------------------------------------
# Synthetic primitive — finance-blind, no DB required
# ---------------------------------------------------------------------------


class _SyntheticInput(BaseModel):
    """Minimal *Input class for the synthetic primitive."""

    series_name: str = "synthetic_series"
    n_rows: int = 30
    base_value: float = 100.0
    drift: float = 0.5
    units: str = "bps"  # closed-enum value, validated downstream


class _SyntheticOutput(BaseModel):
    """Minimal *Output schema with a canonical TimeSeries field +
    a current_metrics block (so the bridge's as_of_date extractor
    is satisfied)."""

    class _Metrics(BaseModel):
        as_of_date: str

    current_metrics: "_SyntheticOutput._Metrics"
    time_series: TimeSeries


_SyntheticOutput.model_rebuild()


def _synthetic_primitive_callable(
    *, engine, params: _SyntheticInput, config,
) -> dict:
    """Generates a deterministic synthetic TimeSeries.  Ignores
    ``engine`` (no DB) — this is a finance-blind primitive used
    for substrate tests."""
    bdays = pd.bdate_range(
        date(2026, 4, 1), periods=params.n_rows,
    )
    # Simple linear walk; deterministic for replay tests.
    values = [params.base_value + i * params.drift for i in range(params.n_rows)]
    rows = [
        TimeSeriesRow(
            date=d.strftime("%Y-%m-%d"),
            value=v,
        )
        for d, v in zip(bdays, values)
    ]
    return {
        "current_metrics": {
            "as_of_date": rows[-1].date,
        },
        "time_series": {
            "series_name": params.series_name,
            "units": params.units,
            "description": "Synthetic series for substrate tests.",
            "rows": [r.model_dump() for r in rows],
        },
    }


# Synthetic primitive's bundled config — needs ``ffill_limit_days``
# so the bridge's auto-derived missingness policy works.  Lives in
# tmp at fixture time.
_SYNTHETIC_CONFIG_YAML = """
tool:
  name: synthetic_primitive_tool
  domain: synthetic
  description: Synthetic primitive for substrate tests.
  category: desk_invariant_primitive
conventions:
  ffill_limit_days:
    value: 5
    source: substrate_test_default
    rationale: synthetic config for substrate tests
methodology:
  what_it_does: >-
    Returns a deterministic linear walk; ignores engine.  Used
    only by tests to exercise the workflow substrate without any
    finance-aware dependencies.
"""


@pytest.fixture
def synthetic_config_path(tmp_path) -> Path:
    """Write the synthetic primitive's config.yaml to a tmp file
    so the bridge's tool_config_path / load_tool_config flow has
    a real file to read."""
    p = tmp_path / "synthetic_config.yaml"
    p.write_text(_SYNTHETIC_CONFIG_YAML)
    return p


@pytest.fixture
def synthetic_resolver(synthetic_config_path) -> PrimitiveResolver:
    """A PrimitiveResolver that knows about exactly one synthetic
    tool — keeps the substrate finance-blind in tests."""

    spec = PrimitiveSpec(
        tool_name="synthetic_primitive_tool",
        callable=_synthetic_primitive_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=synthetic_config_path,
    )

    def _resolve(tool_name: str) -> PrimitiveSpec:
        if tool_name != "synthetic_primitive_tool":
            raise KeyError(
                f"synthetic resolver only knows "
                f"'synthetic_primitive_tool'; got {tool_name!r}"
            )
        return spec

    return _resolve


@pytest.fixture(autouse=True)
def _clear_config_cache():
    """Reset the process-wide ToolConfig cache between tests so
    the synthetic config doesn't leak across test runs."""
    from shared.config import clear_tool_config_cache
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


# ===========================================================================
# 1. Schema construction
# ===========================================================================


class TestSchemaConstruction:
    def test_minimal_one_node_workflow(self):
        """One primitive node, no edges, primitive is the
        terminal — the smallest legal workflow."""
        wf = Workflow(
            workflow_id="trivial",
            nodes=[
                PrimitiveNode(
                    node_id="p1",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={},
                ),
            ],
            edges=[],
            terminal_node_id="p1",
        )
        assert wf.workflow_id == "trivial"
        assert len(wf.nodes) == 1
        assert wf.terminal_node_id == "p1"

    def test_two_primitive_one_operator_diamond(self):
        wf = Workflow(
            workflow_id="diamond",
            nodes=[
                PrimitiveNode(
                    node_id="p_left", tool_name="t1",
                    output_field="time_series", params={},
                ),
                PrimitiveNode(
                    node_id="p_right", tool_name="t2",
                    output_field="time_series", params={},
                ),
                OperatorNode(
                    node_id="op", operator_name="align_series",
                    params={},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p_left",
                    target_node_id="op",
                    target_input_slot="series_list",
                ),
                WorkflowEdge(
                    source_node_id="p_right",
                    target_node_id="op",
                    target_input_slot="series_list",
                ),
            ],
            terminal_node_id="op",
        )
        assert len(wf.nodes) == 3
        assert len(wf.edges) == 2

    def test_node_id_uniqueness_enforced_at_construction(self):
        with pytest.raises(Exception, match="duplicate node_id"):
            Workflow(
                workflow_id="bad",
                nodes=[
                    PrimitiveNode(
                        node_id="dup", tool_name="t",
                        output_field="time_series", params={},
                    ),
                    PrimitiveNode(
                        node_id="dup", tool_name="t",
                        output_field="time_series", params={},
                    ),
                ],
                edges=[],
                terminal_node_id="dup",
            )

    def test_terminal_node_must_exist(self):
        with pytest.raises(Exception, match="terminal_node_id"):
            Workflow(
                workflow_id="bad",
                nodes=[
                    PrimitiveNode(
                        node_id="p1", tool_name="t",
                        output_field="time_series", params={},
                    ),
                ],
                edges=[],
                terminal_node_id="ghost",
            )

    def test_edge_source_must_exist(self):
        with pytest.raises(Exception, match="unknown source_node_id"):
            Workflow(
                workflow_id="bad",
                nodes=[
                    PrimitiveNode(
                        node_id="p1", tool_name="t",
                        output_field="time_series", params={},
                    ),
                ],
                edges=[
                    WorkflowEdge(
                        source_node_id="ghost",
                        target_node_id="p1",
                        target_input_slot="series",
                    ),
                ],
                terminal_node_id="p1",
            )

    def test_edge_target_must_exist(self):
        with pytest.raises(Exception, match="unknown target_node_id"):
            Workflow(
                workflow_id="bad",
                nodes=[
                    PrimitiveNode(
                        node_id="p1", tool_name="t",
                        output_field="time_series", params={},
                    ),
                ],
                edges=[
                    WorkflowEdge(
                        source_node_id="p1",
                        target_node_id="ghost",
                        target_input_slot="series",
                    ),
                ],
                terminal_node_id="p1",
            )

    def test_workflow_is_frozen(self):
        wf = Workflow(
            workflow_id="frozen",
            nodes=[
                PrimitiveNode(
                    node_id="p1", tool_name="t",
                    output_field="time_series", params={},
                ),
            ],
            edges=[],
            terminal_node_id="p1",
        )
        with pytest.raises(Exception):
            wf.workflow_id = "different"  # frozen=True

    def test_node_by_id_accessor(self):
        node = PrimitiveNode(
            node_id="p1", tool_name="t",
            output_field="time_series", params={},
        )
        wf = Workflow(
            workflow_id="x", nodes=[node], edges=[],
            terminal_node_id="p1",
        )
        assert wf.node_by_id("p1") is node
        with pytest.raises(KeyError):
            wf.node_by_id("ghost")


# ===========================================================================
# 2. Validation
# ===========================================================================


def _wf_two_primitives_one_align(**overrides) -> Workflow:
    """Helper: build a 2-primitive + 1-align workflow."""
    return Workflow(
        workflow_id=overrides.get("workflow_id", "test"),
        nodes=overrides.get(
            "nodes",
            [
                PrimitiveNode(
                    node_id="a",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_a"},
                ),
                PrimitiveNode(
                    node_id="b",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s_b"},
                ),
                OperatorNode(
                    node_id="align",
                    operator_name="align_series",
                    params={},
                ),
            ],
        ),
        edges=overrides.get(
            "edges",
            [
                WorkflowEdge(
                    source_node_id="a",
                    target_node_id="align",
                    target_input_slot="series_list",
                ),
                WorkflowEdge(
                    source_node_id="b",
                    target_node_id="align",
                    target_input_slot="series_list",
                ),
            ],
        ),
        terminal_node_id=overrides.get("terminal_node_id", "align"),
    )


class TestValidate:
    def test_happy_path_validates(self):
        validate_workflow(_wf_two_primitives_one_align())

    def test_unknown_operator_rejected(self):
        wf = Workflow(
            workflow_id="x",
            nodes=[
                OperatorNode(
                    node_id="op",
                    operator_name="not_a_real_operator",
                    params={},
                ),
            ],
            edges=[],
            terminal_node_id="op",
        )
        with pytest.raises(WorkflowValidationError, match="unknown operator"):
            validate_workflow(wf)

    def test_unknown_target_input_slot_rejected(self):
        wf = Workflow(
            workflow_id="x",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                OperatorNode(
                    node_id="op",
                    operator_name="align_series",
                    params={},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p",
                    target_node_id="op",
                    target_input_slot="not_a_slot",  # typo
                ),
            ],
            terminal_node_id="op",
        )
        with pytest.raises(WorkflowValidationError, match="unknown input slot"):
            validate_workflow(wf)

    def test_edge_into_primitive_rejected(self):
        """PrimitiveNodes do not consume node-output edges in v1."""
        wf = Workflow(
            workflow_id="x",
            nodes=[
                PrimitiveNode(
                    node_id="p1", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                PrimitiveNode(
                    node_id="p2", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p1",
                    target_node_id="p2",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="p2",
        )
        with pytest.raises(
            WorkflowValidationError, match="targets a PrimitiveNode",
        ):
            validate_workflow(wf)

    def test_unbound_required_slot_rejected(self):
        """``align_series.series_list`` is required; no edges = error."""
        wf = Workflow(
            workflow_id="x",
            nodes=[
                OperatorNode(
                    node_id="op",
                    operator_name="align_series",
                    params={},
                ),
            ],
            edges=[],
            terminal_node_id="op",
        )
        with pytest.raises(WorkflowValidationError, match="unbound required input slot"):
            validate_workflow(wf)

    def test_scalar_accepting_slot_may_be_unbound(self):
        """``series_arithmetic.right`` is in ``accepts_scalar_input``
        — leaving it unbound is legal (unary diff/pct_change)."""
        wf = Workflow(
            workflow_id="x",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                OperatorNode(
                    node_id="op",
                    operator_name="series_arithmetic",
                    params={"op": "diff"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p",
                    target_node_id="op",
                    target_input_slot="left",
                ),
            ],
            terminal_node_id="op",
        )
        validate_workflow(wf)  # no exception

    def test_type_mismatch_rejected(self):
        """``threshold_events`` consumes a ``Series``; feeding it a
        ``SeriesSet`` (the output of ``align_series``) is a type
        mismatch — must surface at validate-time."""
        wf = Workflow(
            workflow_id="x",
            nodes=[
                PrimitiveNode(
                    node_id="a", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "a"},
                ),
                PrimitiveNode(
                    node_id="b", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "b"},
                ),
                OperatorNode(
                    node_id="align",
                    operator_name="align_series",
                    params={},
                ),
                OperatorNode(
                    node_id="thresh",
                    operator_name="threshold_events",
                    params={
                        "rule": "abs_above", "threshold": 1.5,
                        "threshold_basis": "raw_value",
                    },
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="a", target_node_id="align",
                    target_input_slot="series_list",
                ),
                WorkflowEdge(
                    source_node_id="b", target_node_id="align",
                    target_input_slot="series_list",
                ),
                # SeriesSet → Series slot (mismatch)
                WorkflowEdge(
                    source_node_id="align",
                    target_node_id="thresh",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="thresh",
        )
        with pytest.raises(
            WorkflowValidationError, match="expects Series",
        ):
            validate_workflow(wf)

    def test_cycle_rejected(self):
        """Build a 2-node cycle by making op_a → op_b → op_a.  The
        cycle detector surfaces the error.  Use BINARY ops here
        so the arity check (Codex P2 follow-up) does not fire
        first — both operators legitimately want a ``right`` edge,
        and the cycle is on that very edge."""
        wf = Workflow(
            workflow_id="cyclic",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                OperatorNode(
                    node_id="op_a",
                    operator_name="series_arithmetic",
                    params={"op": "subtract"},  # binary
                ),
                OperatorNode(
                    node_id="op_b",
                    operator_name="series_arithmetic",
                    params={"op": "subtract"},  # binary
                ),
            ],
            edges=[
                # Bind op_a's left from p (so op_a is reachable)
                WorkflowEdge(
                    source_node_id="p", target_node_id="op_a",
                    target_input_slot="left",
                ),
                # Bind op_b's left from p
                WorkflowEdge(
                    source_node_id="p", target_node_id="op_b",
                    target_input_slot="left",
                ),
                # op_a → op_b → op_a cycle via the right slots
                WorkflowEdge(
                    source_node_id="op_a", target_node_id="op_b",
                    target_input_slot="right",
                ),
                WorkflowEdge(
                    source_node_id="op_b", target_node_id="op_a",
                    target_input_slot="right",
                ),
            ],
            terminal_node_id="op_b",
        )
        with pytest.raises(WorkflowValidationError, match="cycle"):
            validate_workflow(wf)

    def test_primitive_resolver_unknown_tool_rejected(self, synthetic_resolver):
        wf = Workflow(
            workflow_id="x",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="not_a_real_tool",
                    output_field="time_series", params={},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        with pytest.raises(WorkflowValidationError, match="cannot resolve"):
            validate_workflow(wf, primitive_resolver=synthetic_resolver)


# ===========================================================================
# 3. Topological order
# ===========================================================================


class TestTopologicalOrder:
    def test_chain_order(self):
        wf = Workflow(
            workflow_id="chain",
            nodes=[
                PrimitiveNode(
                    node_id="a", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                OperatorNode(
                    node_id="b",
                    operator_name="series_arithmetic",
                    params={"op": "diff"},
                ),
                OperatorNode(
                    node_id="c",
                    operator_name="series_arithmetic",
                    params={"op": "diff"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="a", target_node_id="b",
                    target_input_slot="left",
                ),
                WorkflowEdge(
                    source_node_id="b", target_node_id="c",
                    target_input_slot="left",
                ),
            ],
            terminal_node_id="c",
        )
        order = topological_order(wf)
        assert order.index("a") < order.index("b") < order.index("c")

    def test_diamond_order(self):
        wf = _wf_two_primitives_one_align()
        order = topological_order(wf)
        # Both primitives precede the align operator.
        assert order.index("a") < order.index("align")
        assert order.index("b") < order.index("align")


# ===========================================================================
# 4. Executor — happy path
# ===========================================================================


class TestExecutor:
    def test_single_primitive_execution(self, synthetic_resolver):
        wf = Workflow(
            workflow_id="single",
            nodes=[
                PrimitiveNode(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s1", "n_rows": 20},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        assert isinstance(result, WorkflowResult)
        assert isinstance(result.terminal_artifact, Series)
        assert result.terminal_artifact.series_key == "s1"
        assert result.terminal_artifact.units == TimeSeriesUnits.BPS
        # Lineage chain has exactly one PrimitiveStep.
        assert len(result.terminal_artifact.lineage.steps) == 1
        assert result.terminal_artifact.lineage.steps[0].kind == "primitive"

    def test_align_two_primitives(self, synthetic_resolver):
        wf = _wf_two_primitives_one_align()
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        assert isinstance(result.terminal_artifact, SeriesSet)
        assert "s_a" in result.terminal_artifact.series_by_key
        assert "s_b" in result.terminal_artifact.series_by_key

    def test_chain_primitive_then_diff_then_diff(self, synthetic_resolver):
        wf = Workflow(
            workflow_id="chain",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "s"},
                ),
                OperatorNode(
                    node_id="d1",
                    operator_name="series_arithmetic",
                    params={"op": "diff"},
                ),
                OperatorNode(
                    node_id="d2",
                    operator_name="series_arithmetic",
                    params={"op": "diff"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p", target_node_id="d1",
                    target_input_slot="left",
                ),
                WorkflowEdge(
                    source_node_id="d1", target_node_id="d2",
                    target_input_slot="left",
                ),
            ],
            terminal_node_id="d2",
        )
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        # Lineage chain length: 1 primitive + 2 operators = 3 steps.
        assert len(result.terminal_artifact.lineage.steps) == 3
        kinds = [s.kind for s in result.terminal_artifact.lineage.steps]
        assert kinds == ["primitive", "operator", "operator"]

    def test_intermediate_node_artifacts_preserved(self, synthetic_resolver):
        wf = _wf_two_primitives_one_align()
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        # Three node artifacts present.
        assert set(result.node_artifacts.keys()) == {"a", "b", "align"}
        assert isinstance(result.node_artifacts["a"], Series)
        assert isinstance(result.node_artifacts["b"], Series)
        assert isinstance(result.node_artifacts["align"], SeriesSet)

    def test_workflow_lineage_summary_format(self, synthetic_resolver):
        wf = _wf_two_primitives_one_align()
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        summary = result.workflow_lineage_summary
        assert summary.startswith("workflow test:")
        assert "align" in summary
        assert "→" in summary
        # Contains all node IDs.
        for nid in ("a", "b", "align"):
            assert nid in summary

    def test_replay_determinism(self, synthetic_resolver):
        """Same workflow + same inputs → same lineage hashes."""
        wf = _wf_two_primitives_one_align()
        r1 = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        r2 = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        # Per-node primitive lineage hashes match.
        for nid in ("a", "b"):
            assert (
                r1.node_artifacts[nid].lineage.head_hash
                == r2.node_artifacts[nid].lineage.head_hash
            )


# ===========================================================================
# 5. Executor refusals
# ===========================================================================


class TestExecutorRefusals:
    def test_validation_failure_raises_validation_error(self, synthetic_resolver):
        """Pre-flight validation is part of execute_workflow; a
        bad workflow surfaces as WorkflowValidationError, not as
        a runtime mid-execution crash."""
        wf = Workflow(
            workflow_id="bad",
            nodes=[
                OperatorNode(
                    node_id="op", operator_name="not_a_real_op",
                    params={},
                ),
            ],
            edges=[],
            terminal_node_id="op",
        )
        with pytest.raises(WorkflowValidationError):
            execute_workflow(
                wf, engine=None, primitive_resolver=synthetic_resolver,
            )

    def test_primitive_runtime_error_wrapped(self, synthetic_resolver):
        """If a primitive raises during execution, the executor
        wraps the exception with workflow + node context."""

        def _broken_resolver(tool_name: str) -> PrimitiveSpec:
            class _BadInput(BaseModel):
                pass
            class _BadOutput(BaseModel):
                pass
            return PrimitiveSpec(
                tool_name=tool_name,
                callable=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("primitive went boom")),
                input_class=_BadInput,
                output_class=_BadOutput,
                config_path=Path("/nonexistent/path.yaml"),
            )

        wf = Workflow(
            workflow_id="boom",
            nodes=[
                PrimitiveNode(
                    node_id="p",
                    tool_name="will_fail",
                    output_field="time_series",
                    params={},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        with pytest.raises(WorkflowExecutionError, match="failed during execution"):
            execute_workflow(
                wf, engine=None, primitive_resolver=_broken_resolver,
            )

    def test_series_arithmetic_missing_op_param(self, synthetic_resolver):
        """series_arithmetic requires ``op`` in params; missing it
        is now caught by the new ``arity_validator`` hook at
        validate-time (Codex P2 follow-up — earlier failure surface
        than the prior behaviour, which only caught it at the
        executor's special-case check)."""
        wf = Workflow(
            workflow_id="bad",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                OperatorNode(
                    node_id="op",
                    operator_name="series_arithmetic",
                    params={},  # missing 'op'
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p", target_node_id="op",
                    target_input_slot="left",
                ),
            ],
            terminal_node_id="op",
        )
        with pytest.raises(WorkflowValidationError, match="params.op"):
            execute_workflow(
                wf, engine=None, primitive_resolver=synthetic_resolver,
            )


# ===========================================================================
# 6. WorkflowResult contract
# ===========================================================================


class TestWorkflowResult:
    def test_terminal_artifact_typed(self, synthetic_resolver):
        wf = _wf_two_primitives_one_align()
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        # terminal_artifact is one of the closed-family types.
        assert isinstance(
            result.terminal_artifact,
            (Series, SeriesSet, EventSet, WindowedPanel),
        )

    def test_result_is_frozen(self, synthetic_resolver):
        wf = _wf_two_primitives_one_align()
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        with pytest.raises(Exception):
            result.workflow_id = "different"


# ===========================================================================
# 6b. Codex P2 follow-ups — arity / literal bindings / unit compat
# ===========================================================================


from shared.workflow import LiteralBinding


class TestArityValidator_SeriesArithmetic:
    """Codex P2 follow-up #1: per-operator arity hook.  The prior
    blanket ``accepts_scalar_input`` skip allowed binary ops to
    validate without a ``right`` operand.  The new
    ``arity_validator`` on series_arithmetic's OperatorSpec fixes
    that — binary ops require ``right`` (via edge OR literal),
    unary ops forbid it."""

    def _wf_with_op(self, op: str, *, bind_right: bool, right_via: str = "edge"):
        """Build a workflow with series_arithmetic at op=op.
        ``bind_right`` controls whether the 'right' slot is bound;
        ``right_via`` chooses 'edge' or 'literal'."""
        nodes = [
            PrimitiveNode(
                node_id="p", tool_name="synthetic_primitive_tool",
                output_field="time_series", params={},
            ),
            OperatorNode(
                node_id="op",
                operator_name="series_arithmetic",
                params={"op": op},
            ),
        ]
        edges = [
            WorkflowEdge(
                source_node_id="p", target_node_id="op",
                target_input_slot="left",
            ),
        ]
        literals = []
        if bind_right:
            if right_via == "edge":
                # Add a second primitive feeding right.
                nodes.insert(
                    1,
                    PrimitiveNode(
                        node_id="p_right",
                        tool_name="synthetic_primitive_tool",
                        output_field="time_series",
                        params={"series_name": "right_series"},
                    ),
                )
                edges.append(
                    WorkflowEdge(
                        source_node_id="p_right", target_node_id="op",
                        target_input_slot="right",
                    )
                )
            else:  # literal
                literals.append(
                    LiteralBinding(
                        target_node_id="op",
                        target_input_slot="right",
                        value=100.0,
                    )
                )
        return Workflow(
            workflow_id=f"arity_{op}",
            nodes=nodes,
            edges=edges,
            literal_bindings=literals,
            terminal_node_id="op",
        )

    def test_binary_subtract_without_right_rejected(self):
        wf = self._wf_with_op("subtract", bind_right=False)
        with pytest.raises(WorkflowValidationError, match="binary"):
            validate_workflow(wf)

    def test_binary_add_without_right_rejected(self):
        wf = self._wf_with_op("add", bind_right=False)
        with pytest.raises(WorkflowValidationError, match="binary"):
            validate_workflow(wf)

    def test_binary_multiply_without_right_rejected(self):
        wf = self._wf_with_op("multiply", bind_right=False)
        with pytest.raises(WorkflowValidationError, match="binary"):
            validate_workflow(wf)

    def test_binary_divide_without_right_rejected(self):
        wf = self._wf_with_op("divide", bind_right=False)
        with pytest.raises(WorkflowValidationError, match="binary"):
            validate_workflow(wf)

    def test_unary_diff_with_right_rejected(self):
        wf = self._wf_with_op("diff", bind_right=True, right_via="edge")
        with pytest.raises(WorkflowValidationError, match="unary"):
            validate_workflow(wf)

    def test_unary_pct_change_with_literal_right_rejected(self):
        wf = self._wf_with_op(
            "pct_change", bind_right=True, right_via="literal",
        )
        with pytest.raises(WorkflowValidationError, match="unary"):
            validate_workflow(wf)

    def test_binary_subtract_with_edge_right_accepted(self):
        wf = self._wf_with_op("subtract", bind_right=True, right_via="edge")
        validate_workflow(wf)  # no exception

    def test_binary_subtract_with_literal_right_accepted(self):
        wf = self._wf_with_op("subtract", bind_right=True, right_via="literal")
        validate_workflow(wf)  # no exception

    def test_unary_diff_without_right_accepted(self):
        wf = self._wf_with_op("diff", bind_right=False)
        validate_workflow(wf)  # no exception


class TestLiteralBindings:
    """Codex P2 follow-up #2: ``LiteralBinding`` lets workflows
    express ``Series * 100.0`` where the constant has no upstream
    node.  The substrate validator + executor handle both edges
    and literal bindings for scalar-accepting slots."""

    def test_literal_binding_construction(self):
        binding = LiteralBinding(
            target_node_id="op",
            target_input_slot="right",
            value=100.0,
        )
        assert binding.value == 100.0
        assert binding.target_node_id == "op"

    def test_literal_value_can_be_int_float_str_bool(self):
        for value in (1, 1.5, "test", True):
            binding = LiteralBinding(
                target_node_id="op",
                target_input_slot="right",
                value=value,
            )
            assert binding.value == value

    def test_literal_binding_targeting_unknown_node_rejected(self):
        with pytest.raises(Exception, match="unknown target_node_id"):
            Workflow(
                workflow_id="bad",
                nodes=[
                    PrimitiveNode(
                        node_id="p",
                        tool_name="synthetic_primitive_tool",
                        output_field="time_series", params={},
                    ),
                ],
                edges=[],
                literal_bindings=[
                    LiteralBinding(
                        target_node_id="ghost",
                        target_input_slot="right",
                        value=100.0,
                    ),
                ],
                terminal_node_id="p",
            )

    def test_literal_binding_to_non_scalar_slot_rejected(self):
        """``align_series.series_list`` is List[Series] — not a
        scalar slot.  Literal binding to it must be rejected."""
        wf = Workflow(
            workflow_id="bad",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                OperatorNode(
                    node_id="op",
                    operator_name="align_series",
                    params={},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p", target_node_id="op",
                    target_input_slot="series_list",
                ),
            ],
            literal_bindings=[
                LiteralBinding(
                    target_node_id="op",
                    target_input_slot="series_list",
                    value=100.0,
                ),
            ],
            terminal_node_id="op",
        )
        with pytest.raises(
            WorkflowValidationError,
            match="does not accept scalar literals",
        ):
            validate_workflow(wf)

    def test_literal_binding_to_unknown_slot_rejected(self):
        wf = Workflow(
            workflow_id="bad",
            nodes=[
                OperatorNode(
                    node_id="op",
                    operator_name="series_arithmetic",
                    params={"op": "diff"},
                ),
            ],
            edges=[],
            literal_bindings=[
                LiteralBinding(
                    target_node_id="op",
                    target_input_slot="not_a_real_slot",
                    value=100.0,
                ),
            ],
            terminal_node_id="op",
        )
        with pytest.raises(
            WorkflowValidationError, match="unknown input slot",
        ):
            validate_workflow(wf)

    def test_literal_binding_executes_end_to_end(self, synthetic_resolver):
        """End-to-end: ``Series * 100.0`` via series_arithmetic with
        a literal-bound ``right`` slot."""
        wf = Workflow(
            workflow_id="series_times_100",
            nodes=[
                PrimitiveNode(
                    node_id="p", tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": "s", "n_rows": 10,
                            "base_value": 1.0, "drift": 0.5},
                ),
                OperatorNode(
                    node_id="mul",
                    operator_name="series_arithmetic",
                    params={"op": "multiply"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p", target_node_id="mul",
                    target_input_slot="left",
                ),
            ],
            literal_bindings=[
                LiteralBinding(
                    target_node_id="mul",
                    target_input_slot="right",
                    value=100.0,
                ),
            ],
            terminal_node_id="mul",
        )
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        # Synthetic primitive emits 1.0, 1.5, 2.0, ... (drift=0.5).
        # Multiply by 100 → 100.0, 150.0, 200.0, ...
        terminal = result.terminal_artifact
        assert isinstance(terminal, Series)
        assert terminal.payload.iloc[0] == 100.0
        assert terminal.payload.iloc[1] == 150.0
        # Lineage chain has one primitive + one operator step.
        assert len(terminal.lineage.steps) == 2
        assert terminal.lineage.steps[1].kind == "operator"

    def test_double_binding_edge_AND_literal_rejected_at_runtime(
        self, synthetic_resolver,
    ):
        """A scalar slot bound by BOTH an edge and a literal is a
        workflow-shape error; the executor catches it (the
        validator currently allows the structure but the executor
        refuses the ambiguous resolution)."""
        wf = Workflow(
            workflow_id="bad",
            nodes=[
                PrimitiveNode(
                    node_id="p_left", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "l"},
                ),
                PrimitiveNode(
                    node_id="p_right", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "r"},
                ),
                OperatorNode(
                    node_id="op",
                    operator_name="series_arithmetic",
                    params={"op": "subtract"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p_left", target_node_id="op",
                    target_input_slot="left",
                ),
                WorkflowEdge(
                    source_node_id="p_right", target_node_id="op",
                    target_input_slot="right",
                ),
            ],
            literal_bindings=[
                LiteralBinding(
                    target_node_id="op",
                    target_input_slot="right",
                    value=100.0,  # also bound by edge above
                ),
            ],
            terminal_node_id="op",
        )
        with pytest.raises(
            WorkflowExecutionError,
            match="bound BOTH by an edge and by a LiteralBinding",
        ):
            execute_workflow(
                wf, engine=None, primitive_resolver=synthetic_resolver,
            )


class TestUnitCompatValidator:
    """Codex P2 follow-up #3: best-effort same-unit check at
    validate-time for series_arithmetic's add/subtract/divide
    ops.  Uses ``PrimitiveSpec.output_field_units`` declarations
    when present; skips silently when units are unknown
    (operator runtime check stays as authoritative gate)."""

    def _resolver_with_unit_decls(
        self,
        synthetic_config_path,
        units_per_field: dict,
    ) -> PrimitiveResolver:
        """Resolver that declares units for each output_field.
        Lets us test the unit-compat pass against deterministic
        unit declarations."""
        spec = PrimitiveSpec(
            tool_name="synthetic_primitive_tool",
            callable=_synthetic_primitive_callable,
            input_class=_SyntheticInput,
            output_class=_SyntheticOutput,
            config_path=synthetic_config_path,
            output_field_units=units_per_field,
        )

        def _resolve(tool_name: str) -> PrimitiveSpec:
            return spec

        return _resolve

    def test_subtract_same_units_validates(self, synthetic_config_path):
        resolver = self._resolver_with_unit_decls(
            synthetic_config_path, {"time_series": "bps"},
        )
        wf = Workflow(
            workflow_id="ok",
            nodes=[
                PrimitiveNode(
                    node_id="a", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "a"},
                ),
                PrimitiveNode(
                    node_id="b", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "b"},
                ),
                OperatorNode(
                    node_id="sub",
                    operator_name="series_arithmetic",
                    params={"op": "subtract"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="a", target_node_id="sub",
                    target_input_slot="left",
                ),
                WorkflowEdge(
                    source_node_id="b", target_node_id="sub",
                    target_input_slot="right",
                ),
            ],
            terminal_node_id="sub",
        )
        validate_workflow(wf, primitive_resolver=resolver)  # OK

    def test_subtract_mismatched_units_rejected(self, tmp_path):
        """Two primitives declared with different output units →
        substrate catches the cross-unit subtract at validate-time."""
        cfg_a = tmp_path / "cfg_a.yaml"
        cfg_a.write_text(_SYNTHETIC_CONFIG_YAML)
        cfg_b = tmp_path / "cfg_b.yaml"
        cfg_b.write_text(_SYNTHETIC_CONFIG_YAML)

        spec_a = PrimitiveSpec(
            tool_name="tool_a",
            callable=_synthetic_primitive_callable,
            input_class=_SyntheticInput,
            output_class=_SyntheticOutput,
            config_path=cfg_a,
            output_field_units={"time_series": "bps"},
        )
        spec_b = PrimitiveSpec(
            tool_name="tool_b",
            callable=_synthetic_primitive_callable,
            input_class=_SyntheticInput,
            output_class=_SyntheticOutput,
            config_path=cfg_b,
            output_field_units={"time_series": "percent"},
        )

        def _resolve(tool_name: str) -> PrimitiveSpec:
            return {"tool_a": spec_a, "tool_b": spec_b}[tool_name]

        wf = Workflow(
            workflow_id="cross_unit",
            nodes=[
                PrimitiveNode(
                    node_id="a", tool_name="tool_a",
                    output_field="time_series", params={"series_name": "a"},
                ),
                PrimitiveNode(
                    node_id="b", tool_name="tool_b",
                    output_field="time_series", params={"series_name": "b"},
                ),
                OperatorNode(
                    node_id="sub",
                    operator_name="series_arithmetic",
                    params={"op": "subtract"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="a", target_node_id="sub",
                    target_input_slot="left",
                ),
                WorkflowEdge(
                    source_node_id="b", target_node_id="sub",
                    target_input_slot="right",
                ),
            ],
            terminal_node_id="sub",
        )
        with pytest.raises(
            WorkflowValidationError, match="matching units",
        ):
            validate_workflow(wf, primitive_resolver=_resolve)

    def test_unit_check_skipped_when_units_undeclared(
        self, synthetic_resolver,
    ):
        """When ``output_field_units`` is empty (the default),
        the validator skips the unit check.  Operator runtime
        refusal is the authoritative gate.  This is the
        best-effort discipline."""
        # The synthetic_resolver fixture does NOT declare units —
        # so a cross-unit subtract structure validates here, and
        # the operator's runtime check would catch any actual
        # mismatch at execution time.
        wf = Workflow(
            workflow_id="undeclared",
            nodes=[
                PrimitiveNode(
                    node_id="a", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "a"},
                ),
                PrimitiveNode(
                    node_id="b", tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={"series_name": "b"},
                ),
                OperatorNode(
                    node_id="sub",
                    operator_name="series_arithmetic",
                    params={"op": "subtract"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="a", target_node_id="sub",
                    target_input_slot="left",
                ),
                WorkflowEdge(
                    source_node_id="b", target_node_id="sub",
                    target_input_slot="right",
                ),
            ],
            terminal_node_id="sub",
        )
        # No exception — units undeclared, validator skips silently.
        validate_workflow(wf, primitive_resolver=synthetic_resolver)


# ===========================================================================
# 7. Finance-blindness structural test
# ===========================================================================


class TestFinanceBlindness:
    """The substrate must NOT transitively import from
    ``rates_agent.*``.  This is the load-bearing structural
    contract from ``workflow_architecture.md`` that keeps the
    workflow layer reusable across instrument families (rates,
    FX, credit, equities, any future agent)."""

    def test_substrate_modules_have_no_rates_agent_imports(self):
        """Static-source check: substrate modules must NOT contain
        any actual ``import rates_agent`` or ``from rates_agent``
        statement.  Documentation references in docstrings /
        comments are allowed (they describe the layering); only
        executable imports are forbidden."""
        import ast

        import shared.workflow
        import shared.workflow.types as _types
        import shared.workflow.validate as _validate
        import shared.workflow.executor as _executor
        import shared.workflow.result as _result
        import shared.workflow.registry as _registry

        for module in (
            shared.workflow, _types, _validate, _executor, _result, _registry,
        ):
            source_path = module.__file__
            if source_path is None:
                continue
            tree = ast.parse(Path(source_path).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("rates_agent"), (
                            f"FINANCE-BLINDNESS VIOLATION in "
                            f"{module.__name__} ({source_path}):\n"
                            f"  ``import {alias.name}`` at line {node.lineno}\n"
                            "Substrate code MUST NOT import from "
                            "rates_agent.  Use the PrimitiveResolver "
                            "protocol instead."
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("rates_agent"):
                        raise AssertionError(
                            f"FINANCE-BLINDNESS VIOLATION in "
                            f"{module.__name__} ({source_path}):\n"
                            f"  ``from {node.module} import ...`` at "
                            f"line {node.lineno}\n"
                            "Substrate code MUST NOT import from "
                            "rates_agent.  Use the PrimitiveResolver "
                            "protocol instead."
                        )

    def test_substrate_runtime_imports_no_rates_agent(self):
        """After importing every substrate module, rates_agent
        should NOT have been auto-imported as a side effect.  This
        is the runtime check — even if no static-source scan
        finds a rates_agent reference, a transitive import via
        some unexpected path would still load rates_agent into
        sys.modules.

        Snapshot/restore discipline: deleting shared.workflow.* from
        sys.modules and re-importing creates fresh class objects —
        which then BREAKS subsequent tests' ``isinstance(node,
        PrimitiveNode)`` checks (the executor's PrimitiveNode reference
        becomes stale relative to nodes built by previously-imported
        templates).  We snapshot the substrate modules + dependent
        downstream modules before the reload and restore them on the
        way out so cross-file test ordering stays clean.  Codex P1
        follow-up surfaced when the regime_conditioned_relationship
        suite cross-imported PrimitiveNode and hit a stale-class
        ``unknown kind='primitive'`` execution error.
        """
        import sys
        # Snapshot which rates_agent modules existed before this
        # test (other tests may have legitimately imported them).
        before = {k for k in sys.modules if k.startswith("rates_agent")}

        # Snapshot every module whose freshness this test will
        # disturb, so we can restore them after the assertion.
        # ``shared.workflow.*`` is what we delete; ``rates_agent.*``,
        # ``shared.operators.*``, and ``rates_agent.workflows.*``
        # carry references to the OLD shared.workflow classes that
        # would otherwise leak stale class identities into later
        # tests.  Snapshotting all three groups + restoring on the
        # way out keeps cross-file test ordering deterministic.
        prefixes_to_isolate = (
            "shared.workflow",
            "shared.operators",
            "rates_agent",
        )
        snapshot = {
            k: v for k, v in sys.modules.items()
            if any(k.startswith(p) for p in prefixes_to_isolate)
        }
        try:
            # Force a fresh import of the substrate.
            for mod_name in list(sys.modules):
                if mod_name.startswith("shared.workflow"):
                    del sys.modules[mod_name]
            importlib.import_module("shared.workflow")
            importlib.import_module("shared.workflow.types")
            importlib.import_module("shared.workflow.validate")
            importlib.import_module("shared.workflow.executor")
            importlib.import_module("shared.workflow.result")
            importlib.import_module("shared.workflow.registry")
            after = {k for k in sys.modules if k.startswith("rates_agent")}
            newly_imported = after - before
            assert not newly_imported, (
                f"FINANCE-BLINDNESS VIOLATION: importing shared.workflow "
                f"transitively loaded rates_agent modules: "
                f"{sorted(newly_imported)}.  The substrate must not pull "
                "in any agent-specific code via its import graph."
            )
        finally:
            # Restore the snapshotted modules so subsequent tests see
            # the SAME class identities they had before this test ran.
            # Without this restore, ``isinstance(some_old_node,
            # NewlyImportedPrimitiveNode)`` would fail in any test
            # that runs after this one and consumes a workflow node
            # built before this point.
            for mod_name in list(sys.modules):
                if any(
                    mod_name.startswith(p) for p in prefixes_to_isolate
                ):
                    if mod_name in snapshot:
                        sys.modules[mod_name] = snapshot[mod_name]
                    else:
                        # Module was added during the reload
                        # (e.g. a fresh import_module call) but
                        # wasn't in the original snapshot — drop it
                        # so the next time it's imported, the
                        # original module is the one that loads.
                        del sys.modules[mod_name]
            # Restore any snapshot entries that were deleted from
            # sys.modules during the reload itself.
            for mod_name, mod in snapshot.items():
                sys.modules[mod_name] = mod

    def test_operator_registry_has_only_shared_operators(self):
        """Sanity: every entry in OPERATOR_REGISTRY references a
        callable from ``shared.operators.*`` (NOT from a
        rates_agent path).  Defensive check against a future
        registry edit accidentally pulling a finance-aware
        operator into the closed family."""
        for name, spec in OPERATOR_REGISTRY.items():
            mod = spec.callable.__module__
            assert mod.startswith("shared.operators."), (
                f"Operator registry entry {name!r} references callable "
                f"from {mod!r} — substrate operators must live under "
                "shared.operators (finance-blind by architecture)."
            )

"""shared.workflow.executor — typed DAG executor for workflow templates.

The substrate's runtime: takes a validated ``Workflow`` + a
``PrimitiveResolver`` + an SQLAlchemy ``Engine``, executes the
DAG in topological order, and returns a typed ``WorkflowResult``.

Design contract
---------------
- **Finance-blind.**  Does not import from ``rates_agent`` or any
  agent-specific package.  Primitives are dispatched via the
  caller-supplied ``PrimitiveResolver``.
- **Validation first.**  Calls
  ``validate_workflow`` before doing anything else; a malformed
  workflow surfaces with a clear ``WorkflowValidationError``
  rather than crashing mid-execution.
- **Bridge at every primitive→operator boundary.**  Primitive
  outputs (raw dicts) are lifted to typed ``Series`` artifacts
  via ``shared.artifacts.adapters.tool_output_to_artifact_series``
  before being handed to operators.  This is the load-bearing
  Phase 1B contract — there is no other path between primitives
  and operators in the substrate.
- **Lineage propagated.**  Every operator call adds an
  ``OperatorStep`` to the artifact's ``Lineage`` chain
  automatically (the operator layer does this internally — the
  executor just hands artifacts in and reads the chain back out).
- **Closed-family artifact types.**  The executor checks every
  produced artifact against ``ARTIFACT_TYPE_NAMES`` so a
  primitive or operator that returns a non-artifact value
  surfaces immediately with a clear error.
"""

from __future__ import annotations

from graphlib import TopologicalSorter
from typing import Any, Dict, List

from sqlalchemy.engine import Engine

from shared.artifacts.adapters.from_time_series import (
    tool_output_to_artifact_series,
)
from shared.config import load_tool_config
from shared.workflow.registry import (
    OPERATOR_REGISTRY,
    PrimitiveResolver,
    artifact_type_name,
)
from shared.workflow.result import WorkflowResult
from shared.workflow.types import (
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from shared.workflow.validate import (
    WorkflowValidationError,
    validate_workflow,
)


class WorkflowExecutionError(RuntimeError):
    """Raised when the executor fails mid-DAG.  Carries enough
    context (workflow_id, failing node_id, underlying exception)
    to diagnose without rerunning."""


def execute_workflow(
    workflow: Workflow,
    *,
    engine: Engine,
    primitive_resolver: PrimitiveResolver,
) -> WorkflowResult:
    """Execute a Workflow end-to-end.

    Parameters
    ----------
    workflow :
        The Workflow to execute.  Validated via
        ``validate_workflow`` before any node runs.
    engine :
        Live SQLAlchemy engine for primitive DB access.  Tests
        pass ``None`` and provide a resolver whose primitives
        ignore the engine (they typically mock the fetcher).
    primitive_resolver :
        Caller-supplied lookup from ``tool_name`` → ``PrimitiveSpec``.
        See ``shared.workflow.registry.PrimitiveResolver``.

    Returns
    -------
    WorkflowResult
        Typed terminal artifact + workflow-level lineage summary +
        all intermediate node artifacts.

    Raises
    ------
    WorkflowValidationError
        From the pre-flight validate pass (cycles, slot/type
        compat, unknown operators / primitives, etc.).
    WorkflowExecutionError
        When a primitive or operator call fails mid-DAG.  Wraps
        the underlying exception with workflow + node context.
    """
    # ------------------------------------------------------------------
    # 0. Pre-flight validation.  Pass the resolver so primitive-name
    #    typos surface here rather than mid-execution.
    # ------------------------------------------------------------------
    validate_workflow(workflow, primitive_resolver=primitive_resolver)

    # ------------------------------------------------------------------
    # 1. Topological sort.  Build the sorter, register all nodes
    #    and edges, then walk node-by-node in dependency order.
    # ------------------------------------------------------------------
    sorter: TopologicalSorter = TopologicalSorter()
    for n in workflow.nodes:
        sorter.add(n.node_id)
    for edge in workflow.edges:
        sorter.add(edge.target_node_id, edge.source_node_id)

    # Per-target-node mapping: which incoming edges feed which
    # input slot, in declaration order (load-bearing for
    # list-shaped slots like ``align_series.series_list``).
    edges_by_target: Dict[str, Dict[str, List[WorkflowEdge]]] = {}
    for edge in workflow.edges:
        edges_by_target.setdefault(edge.target_node_id, {}).setdefault(
            edge.target_input_slot, []
        ).append(edge)

    # Per-node artifact cache.  Populated as nodes execute; used
    # to resolve downstream nodes' input bindings.
    node_artifacts: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 2. Execute nodes in topological order.
    # ------------------------------------------------------------------
    execution_order = list(sorter.static_order())
    for node_id in execution_order:
        node = workflow.node_by_id(node_id)
        try:
            if isinstance(node, PrimitiveNode):
                artifact = _execute_primitive_node(
                    node,
                    workflow=workflow,
                    engine=engine,
                    primitive_resolver=primitive_resolver,
                )
            elif isinstance(node, OperatorNode):
                artifact = _execute_operator_node(
                    node,
                    workflow=workflow,
                    edges_by_target=edges_by_target.get(node.node_id, {}),
                    node_artifacts=node_artifacts,
                )
            else:
                # Unreachable (closed-family discriminated union),
                # but defensive in case the union grows without
                # the executor catching up.
                raise WorkflowExecutionError(
                    f"Workflow {workflow.workflow_id!r}: node "
                    f"{node_id!r} has unknown kind={node.kind!r}.  "
                    "Substrate executor needs to be extended for "
                    "this node kind — see shared.workflow.types."
                )
        except WorkflowExecutionError:
            raise
        except Exception as exc:
            raise WorkflowExecutionError(
                f"Workflow {workflow.workflow_id!r}: node "
                f"{node_id!r} failed during execution: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        # Validate the produced artifact's type.  Closed family,
        # loud failure on anything unexpected.  Surfaces here
        # rather than as a downstream operator AttributeError.
        try:
            artifact_type_name(artifact)
        except ValueError as exc:
            raise WorkflowExecutionError(
                f"Workflow {workflow.workflow_id!r}: node "
                f"{node_id!r} produced a non-artifact value: {exc}"
            ) from exc

        node_artifacts[node_id] = artifact

    # ------------------------------------------------------------------
    # 3. Build the terminal result.
    # ------------------------------------------------------------------
    if workflow.terminal_node_id not in node_artifacts:
        # Should not happen — topological_order includes every
        # node.  Defensive guard.
        raise WorkflowExecutionError(
            f"Workflow {workflow.workflow_id!r}: terminal_node_id="
            f"{workflow.terminal_node_id!r} did not produce an "
            "artifact during execution.  This is a substrate bug; "
            "please report."
        )

    terminal_artifact = node_artifacts[workflow.terminal_node_id]

    return WorkflowResult(
        workflow_id=workflow.workflow_id,
        terminal_artifact=terminal_artifact,
        workflow_lineage_summary=_format_workflow_lineage_summary(
            workflow=workflow,
            execution_order=execution_order,
        ),
        node_artifacts=dict(node_artifacts),
    )


# ============================================================================
# NODE-KIND DISPATCH
# ============================================================================


def _execute_primitive_node(
    node: PrimitiveNode,
    *,
    workflow: Workflow,
    engine: Engine,
    primitive_resolver: PrimitiveResolver,
) -> Any:
    """Resolve the primitive, invoke it, and lift the output via
    the bridge into a typed Series artifact."""
    spec = primitive_resolver(node.tool_name)

    # Construct the primitive's *Input from the node's params.
    # Pydantic validation surfaces here with a clear error
    # naming the offending field.
    params = spec.input_class(**node.params)

    # Load the primitive's bundled config (process-cached).
    config = load_tool_config(spec.config_path)

    # Invoke the primitive.  Same call shape every per-tool-folder
    # primitive accepts: (engine, params, config) → dict.
    tool_output = spec.callable(
        engine=engine, params=params, config=config,
    )

    # Lift via the bridge.  Auto-derives the missingness policy
    # from config.yaml + builds the PrimitiveStep with all four
    # identity bits + validates the output dict against the
    # primitive's *Output schema.
    return tool_output_to_artifact_series(
        tool_output,
        output_class=spec.output_class,
        output_field=node.output_field,
        tool_name=node.tool_name,
        tool_config=config,
        params=params,
        tool_config_path=str(spec.config_path),
    )


def _execute_operator_node(
    node: OperatorNode,
    *,
    workflow: Workflow,
    edges_by_target: Dict[str, List[WorkflowEdge]],
    node_artifacts: Dict[str, Any],
) -> Any:
    """Resolve the operator's input slots from upstream artifacts,
    construct its ``*Params`` instance, and invoke it."""
    spec = OPERATOR_REGISTRY[node.operator_name]

    # Resolve input bindings.  For each slot, gather the artifacts
    # produced by upstream edges in declaration order.  List-shaped
    # slots aggregate multiple edges into a list; scalar slots
    # take exactly one edge's artifact.
    call_kwargs: Dict[str, Any] = {}
    for slot_name, slot_type in spec.input_slots.items():
        edges = edges_by_target.get(slot_name, [])
        upstream_artifacts = [
            node_artifacts[edge.source_node_id] for edge in edges
        ]
        if slot_type.startswith("List[") and slot_type.endswith("]"):
            # List-shaped slot — pass the list (possibly empty;
            # operators that require min length will surface the
            # error at their own validator).
            call_kwargs[slot_name] = upstream_artifacts
        elif slot_name in spec.accepts_scalar_input and not edges:
            # Scalar-accepting slot with no upstream edge — leave
            # absent so the operator's signature default applies
            # (e.g. ``series_arithmetic.right=None`` for unary
            # ops).
            continue
        else:
            # Scalar slot — exactly one upstream artifact.  The
            # validator already enforced "≥1 edge bound"; if more
            # than one edge bound the same scalar slot, that's a
            # workflow-shape error we surface here.
            if len(upstream_artifacts) != 1:
                raise WorkflowExecutionError(
                    f"Workflow {workflow.workflow_id!r}: operator "
                    f"node {node.node_id!r} ({node.operator_name!r}) "
                    f"expects exactly one upstream edge for scalar "
                    f"input slot {slot_name!r} (type {slot_type!r}); "
                    f"got {len(upstream_artifacts)}."
                )
            call_kwargs[slot_name] = upstream_artifacts[0]

    # Construct the operator's *Params instance.  An empty
    # node.params dict means "use the operator's defaults" — pass
    # ``params=None`` so the operator's auto-load-from-YAML path
    # fires.
    if node.params:
        if spec.params_class is None:
            raise WorkflowExecutionError(
                f"Workflow {workflow.workflow_id!r}: operator node "
                f"{node.node_id!r} ({node.operator_name!r}) supplied "
                "params, but this operator has no params_class.  "
                "Substrate registry inconsistency — please report."
            )
        try:
            params_instance = spec.params_class(**node.params)
        except Exception as exc:
            raise WorkflowExecutionError(
                f"Workflow {workflow.workflow_id!r}: operator node "
                f"{node.node_id!r} ({node.operator_name!r}) params "
                f"failed Pydantic validation: {exc}"
            ) from exc
        call_kwargs["params"] = params_instance

    # Special case for series_arithmetic: ``op`` is a positional
    # argument (or kwarg with no Params field).  If the caller
    # supplied ``op`` in node.params, it must already be inside
    # the SeriesArithmeticParams instance above; we additionally
    # surface it as a positional kwarg so the operator's required
    # ``op`` arg is satisfied.
    if node.operator_name == "series_arithmetic":
        op = node.params.get("op")
        if op is None:
            raise WorkflowExecutionError(
                f"Workflow {workflow.workflow_id!r}: operator node "
                f"{node.node_id!r} (series_arithmetic) requires "
                "params.op (e.g. 'subtract', 'diff') but none was "
                "supplied."
            )
        call_kwargs["op"] = op

    return spec.callable(**call_kwargs)


# ============================================================================
# WORKFLOW-LEVEL LINEAGE SUMMARY
# ============================================================================


def _format_workflow_lineage_summary(
    *,
    workflow: Workflow,
    execution_order: List[str],
) -> str:
    """Render a one-line summary of the workflow's execution path.

    Format::

        "workflow <workflow_id>: <node_id>[ → <node_id>]*"

    Distinct from the terminal artifact's structured ``Lineage``
    chain (which has one ``LineageStep`` per primitive/operator
    call, with full identity bits).  This summary is the
    workflow-level analog of the bridge's reverse-path lineage
    summary — bounded, readable, suitable for serialization to the
    LLM / frontend.

    Nodes are emitted in topological execution order.  Auxiliary
    chains (right operands of binary operators) are intentionally
    NOT walked — they remain on the structured artifact, same
    discipline as the bridge's reverse path.
    """
    return (
        f"workflow {workflow.workflow_id}: "
        + " → ".join(execution_order)
    )


__all__ = [
    "WorkflowExecutionError",
    "execute_workflow",
]

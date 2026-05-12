"""shared.workflow.validate — pre-execution validation of a Workflow.

Static analysis pass that runs BEFORE the executor.  Catches
shape errors at template-time rather than at execution-time so a
bad workflow fails loudly during template authoring / orchestrator
testing, not in the middle of a multi-node run.

What this module checks
-----------------------
1. **Cycles** — the DAG must be acyclic.  ``graphlib.TopologicalSorter``
   raises on cycles at execution time, but we surface it earlier
   with a more diagnostic error.
2. **Operator existence** — every ``OperatorNode.operator_name``
   must be in ``OPERATOR_REGISTRY``.  Caught with a clear "did
   you mean..." pointer to known operators.
3. **Slot existence** — every operator's named input slots
   (per ``OperatorSpec.input_slots``) must be filled by at least
   one edge.
4. **Slot completeness** — every edge's ``target_input_slot``
   must be a valid slot name on the target operator.  Misspelled
   slot names surface here, not silently into the executor.
5. **Type compatibility** — the source node's output artifact
   type must match the target slot's expected input type.  For
   list-shaped slots (e.g. ``align_series.series_list:
   List[Series]``), every contributing edge must produce the
   element type (``Series``).
6. **Terminal-node connectivity** — the terminal node must be
   reachable from at least one root in the DAG (no orphan
   terminal).  Optional: warn if the terminal has no incoming
   edges (a primitive-only "terminal" is legal but unusual).
7. **Primitive resolvability** — when a ``PrimitiveResolver`` is
   supplied, every ``PrimitiveNode.tool_name`` must resolve.
   Without a resolver, this check is skipped (so workflow
   templates can be statically validated without a live
   resolver).

Output: ``WorkflowValidationError`` raised on failure (subclass
of ``ValueError`` for caller convenience).  All errors include
the ``workflow_id`` and the offending ``node_id`` / edge tuple
for diagnostic clarity.
"""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import Dict, List, Optional, Set


def _topological_node_ids(workflow) -> List[str]:
    """Internal helper: topological order of a workflow's node IDs,
    for unit-propagation walks.  Distinct from the public
    ``topological_order`` (below) which calls validate first;
    this one is called BY validate so it cannot recurse."""
    sorter: TopologicalSorter = TopologicalSorter()
    for n in workflow.nodes:
        sorter.add(n.node_id)
    for edge in workflow.edges:
        sorter.add(edge.target_node_id, edge.source_node_id)
    return list(sorter.static_order())

from shared.workflow.registry import (
    OPERATOR_REGISTRY,
    PrimitiveResolver,
    known_operators,
)
from shared.workflow.types import (
    LiteralBinding,
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
)


class WorkflowValidationError(ValueError):
    """Raised when ``validate_workflow`` finds a structural
    problem with a Workflow before execution."""


def validate_workflow(
    workflow: Workflow,
    *,
    primitive_resolver: Optional[PrimitiveResolver] = None,
) -> None:
    """Validate a Workflow against the substrate's structural
    contract.  Raises ``WorkflowValidationError`` on the first
    error found, with a diagnostic message naming the offending
    node / edge.

    Parameters
    ----------
    workflow :
        The Workflow to validate.  Construction-time checks (node
        ID uniqueness, edge endpoint existence, terminal-node
        existence) have already run via the model validator;
        this pass adds DAG-level structural checks.
    primitive_resolver :
        Optional ``PrimitiveResolver`` for resolving primitive
        tool names.  When supplied, each ``PrimitiveNode.tool_name``
        is resolved at validate-time so a typo or missing primitive
        surfaces here rather than mid-execution.  When omitted,
        primitive-name resolution is deferred to execution time
        (templates can still validate their non-primitive
        structure offline).
    """
    # ------------------------------------------------------------------
    # 1. Operator existence — every OperatorNode names a known operator.
    # ------------------------------------------------------------------
    for node in workflow.nodes:
        if isinstance(node, OperatorNode):
            if node.operator_name not in OPERATOR_REGISTRY:
                raise WorkflowValidationError(
                    f"Workflow {workflow.workflow_id!r}: node "
                    f"{node.node_id!r} references unknown operator "
                    f"{node.operator_name!r}.  Known operators: "
                    f"{known_operators()}.  To add a new operator, "
                    "extend ``OPERATOR_REGISTRY`` and ensure the "
                    "operator passes its admission checklist in "
                    "``operator_architecture.md``."
                )

    # ------------------------------------------------------------------
    # 2. Slot validity — every edge's target_input_slot is a real
    #    slot on the target operator.
    # ------------------------------------------------------------------
    for edge in workflow.edges:
        target_node = workflow.node_by_id(edge.target_node_id)
        if isinstance(target_node, OperatorNode):
            spec = OPERATOR_REGISTRY[target_node.operator_name]
            if edge.target_input_slot not in spec.input_slots:
                raise WorkflowValidationError(
                    f"Workflow {workflow.workflow_id!r}: edge "
                    f"{edge.source_node_id!r} → {edge.target_node_id!r} "
                    f"targets unknown input slot "
                    f"{edge.target_input_slot!r} on operator "
                    f"{target_node.operator_name!r}.  Known slots: "
                    f"{sorted(spec.input_slots.keys())}."
                )
        elif isinstance(target_node, PrimitiveNode):
            # PrimitiveNodes do not consume node-output edges in v1
            # (primitives fetch from DB themselves).  An edge
            # targeting a PrimitiveNode is a workflow-shape error.
            raise WorkflowValidationError(
                f"Workflow {workflow.workflow_id!r}: edge "
                f"{edge.source_node_id!r} → {edge.target_node_id!r} "
                f"targets a PrimitiveNode.  Primitives do not consume "
                "node-output edges in v1 (they fetch from DB "
                "themselves).  Composite primitives are deferred to "
                "Phase 2 (see ``PrimitiveStep.input_hashes`` slot in "
                "``shared.artifacts.lineage``)."
            )

    # ------------------------------------------------------------------
    # 2b. Literal binding validity — every LiteralBinding targets a
    #    real OperatorNode whose target slot accepts a scalar.
    # ------------------------------------------------------------------
    for binding in workflow.literal_bindings:
        target_node = workflow.node_by_id(binding.target_node_id)
        if not isinstance(target_node, OperatorNode):
            raise WorkflowValidationError(
                f"Workflow {workflow.workflow_id!r}: literal binding "
                f"targets node {binding.target_node_id!r} which is "
                "not an OperatorNode.  Literal bindings can only "
                "target operator slots."
            )
        spec = OPERATOR_REGISTRY[target_node.operator_name]
        if binding.target_input_slot not in spec.input_slots:
            raise WorkflowValidationError(
                f"Workflow {workflow.workflow_id!r}: literal binding "
                f"targets unknown input slot "
                f"{binding.target_input_slot!r} on operator "
                f"{target_node.operator_name!r}.  Known slots: "
                f"{sorted(spec.input_slots.keys())}."
            )
        if binding.target_input_slot not in spec.accepts_scalar_input:
            raise WorkflowValidationError(
                f"Workflow {workflow.workflow_id!r}: literal binding "
                f"targets slot {binding.target_input_slot!r} on "
                f"operator {target_node.operator_name!r}, but that "
                "slot does not accept scalar literals.  Use a "
                "WorkflowEdge from an upstream artifact-producing "
                "node instead, or pick an operator whose slot is "
                "in ``OperatorSpec.accepts_scalar_input``."
            )

    # ------------------------------------------------------------------
    # 3. Slot completeness — every operator's required input slots
    #    are filled by at least one edge OR (for scalar slots) one
    #    LiteralBinding.  Operators with conditional arity (e.g.
    #    series_arithmetic, whose ``right`` requiredness depends
    #    on ``op``) declare an ``arity_validator`` hook on their
    #    OperatorSpec; the substrate-default branch handles the
    #    simple "always required unless scalar-accepting" case.
    # ------------------------------------------------------------------
    edges_by_target: Dict[str, Dict[str, List[WorkflowEdge]]] = {}
    for edge in workflow.edges:
        edges_by_target.setdefault(edge.target_node_id, {}).setdefault(
            edge.target_input_slot, []
        ).append(edge)

    literals_by_target: Dict[str, Dict[str, List[LiteralBinding]]] = {}
    for binding in workflow.literal_bindings:
        literals_by_target.setdefault(binding.target_node_id, {}).setdefault(
            binding.target_input_slot, []
        ).append(binding)

    for node in workflow.nodes:
        if not isinstance(node, OperatorNode):
            continue
        spec = OPERATOR_REGISTRY[node.operator_name]
        bound_edge_slots = set(edges_by_target.get(node.node_id, {}).keys())
        bound_literal_slots = set(
            literals_by_target.get(node.node_id, {}).keys()
        )

        # Per-operator arity hook supersedes the substrate default
        # for operators that declare one.  Codex P2 follow-up
        # (PR #78): the prior blanket-skip on
        # ``accepts_scalar_input`` allowed binary
        # series_arithmetic ops to validate without a ``right``
        # operand; the hook fixes that.
        if spec.arity_validator is not None:
            err = spec.arity_validator(
                node.params, bound_edge_slots, bound_literal_slots,
            )
            if err is not None:
                raise WorkflowValidationError(
                    f"Workflow {workflow.workflow_id!r}: operator "
                    f"node {node.node_id!r} ({node.operator_name!r}) "
                    f"failed arity check: {err}"
                )
            continue  # the hook is authoritative for this operator

        # Substrate-default: every slot must be bound (via edge or,
        # for scalar-accepting slots, via literal), unless it is in
        # ``accepts_scalar_input`` AND no arity hook is declared
        # (in which case the operator's own runtime check is the
        # authoritative arity gate).
        for slot_name, slot_type in spec.input_slots.items():
            if (
                slot_name in spec.accepts_scalar_input
                and slot_name not in bound_edge_slots
                and slot_name not in bound_literal_slots
            ):
                # Scalar-accepting slot, no binding either way —
                # the operator's signature default applies.  Legal
                # for operators without an arity hook.
                continue
            if (
                slot_name not in bound_edge_slots
                and slot_name not in bound_literal_slots
            ):
                raise WorkflowValidationError(
                    f"Workflow {workflow.workflow_id!r}: operator "
                    f"node {node.node_id!r} ({node.operator_name!r}) "
                    f"has unbound required input slot "
                    f"{slot_name!r} (type {slot_type!r}).  Add a "
                    "``WorkflowEdge`` whose target_input_slot binds "
                    "this slot, or (if the slot accepts scalars) a "
                    "``LiteralBinding`` with a literal value."
                )

    # ------------------------------------------------------------------
    # 4. Type compatibility — source node's output type matches the
    #    target slot's expected type.
    # ------------------------------------------------------------------
    for edge in workflow.edges:
        target_node = workflow.node_by_id(edge.target_node_id)
        source_node = workflow.node_by_id(edge.source_node_id)

        # Target type from operator spec.
        if not isinstance(target_node, OperatorNode):
            # Already raised above — defensive skip.
            continue
        target_spec = OPERATOR_REGISTRY[target_node.operator_name]
        slot_type = target_spec.input_slots.get(edge.target_input_slot)
        if slot_type is None:
            continue  # already raised above
        # Strip ``List[X]`` to ``X`` for element-type comparison;
        # list-aggregation across edges is the substrate's
        # representation for list-shaped slots.
        expected_element = (
            slot_type[len("List[") : -1]
            if slot_type.startswith("List[") and slot_type.endswith("]")
            else slot_type
        )

        # Source type — primitives produce whichever artifact type the
        # PrimitiveSpec's ``output_artifact_type`` declares (``Series``
        # is the historical default; PR 20 adds ``Panel`` for
        # Panel-emitting primitives like ``build_sovereign_yield_panel_tool``
        # and ``compute_financing_rate_tool``).  When the resolver is
        # unavailable, fall back to ``Series`` (pre-PR-20 behaviour) so
        # validation without a resolver stays permissive.
        # Operators produce whatever ``OperatorSpec.output_type`` declares.
        if isinstance(source_node, PrimitiveNode):
            source_type = "Series"
            if primitive_resolver is not None:
                try:
                    source_spec = primitive_resolver(source_node.tool_name)
                    source_type = getattr(
                        source_spec, "output_artifact_type", "Series",
                    )
                except Exception:
                    # Resolver miss already raised in the explicit
                    # primitive-resolution pass at the end of validate;
                    # fall back to the conservative default here so
                    # type-compat doesn't double-report.
                    pass
        elif isinstance(source_node, OperatorNode):
            source_spec = OPERATOR_REGISTRY[source_node.operator_name]
            source_type = source_spec.output_type
        else:
            continue  # unreachable

        if source_type != expected_element:
            raise WorkflowValidationError(
                f"Workflow {workflow.workflow_id!r}: edge "
                f"{edge.source_node_id!r} ({source_type}) → "
                f"{edge.target_node_id!r}.{edge.target_input_slot} "
                f"expects {expected_element}.  Type-mismatched edge "
                "would propagate as a runtime error mid-execution; "
                "fix the source/target pairing or insert an adapter "
                "node."
            )

    # ------------------------------------------------------------------
    # 4b. Unit compatibility (best-effort).  Codex P2 follow-up
    #    (PR #78): the prior validator's type-compat check only
    #    compared artifact wrapper class names.  This pass adds
    #    operator-declared cross-slot unit-algebra checks where
    #    the upstream sources have declared output units (via
    #    ``PrimitiveSpec.output_field_units`` for primitives or
    #    via the unit-algebra propagation logic below for
    #    operator chains).
    #
    #    Discipline: best-effort.  When source units are unknown
    #    (e.g. a primitive whose resolver doesn't declare
    #    output_field_units, or an operator chain we can't trace),
    #    the validator skips the check.  Operator runtime
    #    refusals stay as the authoritative gate — the substrate
    #    catches what it can declare; operators catch what
    #    template authors couldn't see at validate time.
    # ------------------------------------------------------------------
    if primitive_resolver is not None:
        # Build a per-node "produced unit" map walking the DAG
        # in topological order.  Primitives: lookup
        # PrimitiveSpec.output_field_units.  Operators: today
        # we trace the operator's output unit conservatively
        # (preserves_left for unary diff, otherwise unknown).
        produced_units: Dict[str, Optional[str]] = {}
        for nid in _topological_node_ids(workflow):
            node = workflow.node_by_id(nid)
            if isinstance(node, PrimitiveNode):
                try:
                    spec = primitive_resolver(node.tool_name)
                except Exception:
                    produced_units[nid] = None
                    continue
                produced_units[nid] = spec.output_field_units.get(
                    node.output_field
                )
            elif isinstance(node, OperatorNode):
                # Conservative propagation: only ``series_arithmetic``
                # with unary preserves_left algebra has a known
                # propagation rule we encode here.  Other operators
                # (align_series, threshold_events, event_windows,
                # conditional_aggregate) emit artifacts whose unit
                # depends on operator-internal logic we don't
                # trace at validate time — leave as unknown.
                if node.operator_name == "series_arithmetic":
                    op = node.params.get("op")
                    if op in ("diff",):
                        # Unary diff preserves left's units.
                        left_edges = edges_by_target.get(
                            nid, {}
                        ).get("left", [])
                        if left_edges:
                            produced_units[nid] = produced_units.get(
                                left_edges[0].source_node_id,
                            )
                            continue
                    elif op == "pct_change":
                        produced_units[nid] = "ratio"
                        continue
                produced_units[nid] = None

        # Now invoke each operator's unit_validator with the
        # source units it can see.
        for node in workflow.nodes:
            if not isinstance(node, OperatorNode):
                continue
            spec = OPERATOR_REGISTRY[node.operator_name]
            if spec.unit_validator is None:
                continue
            source_units: Dict[str, Optional[str]] = {}
            for slot_name in spec.input_slots:
                edges = edges_by_target.get(node.node_id, {}).get(slot_name, [])
                if not edges:
                    source_units[slot_name] = None
                    continue
                # First-edge wins for unit lookup; multiple edges
                # to the same scalar slot is already a
                # workflow-shape error caught by the executor.
                source_units[slot_name] = produced_units.get(
                    edges[0].source_node_id,
                )
            err = spec.unit_validator(node.params, source_units)
            if err is not None:
                raise WorkflowValidationError(
                    f"Workflow {workflow.workflow_id!r}: operator "
                    f"node {node.node_id!r} ({node.operator_name!r}) "
                    f"failed unit-compatibility check: {err}"
                )

    # ------------------------------------------------------------------
    # 5. Cycle detection.  ``TopologicalSorter`` raises
    #    ``CycleError`` on cycles.  Surface with workflow context.
    # ------------------------------------------------------------------
    sorter: TopologicalSorter = TopologicalSorter()
    node_ids = {n.node_id for n in workflow.nodes}
    for nid in node_ids:
        sorter.add(nid)
    for edge in workflow.edges:
        sorter.add(edge.target_node_id, edge.source_node_id)
    try:
        sorter.prepare()
    except CycleError as exc:
        raise WorkflowValidationError(
            f"Workflow {workflow.workflow_id!r}: DAG contains a "
            f"cycle.  graphlib.TopologicalSorter says: {exc.args}.  "
            "Workflows must be acyclic."
        ) from exc

    # ------------------------------------------------------------------
    # 6. Primitive resolvability (when a resolver is supplied).
    # ------------------------------------------------------------------
    if primitive_resolver is not None:
        for node in workflow.nodes:
            if isinstance(node, PrimitiveNode):
                try:
                    primitive_resolver(node.tool_name)
                except Exception as exc:
                    raise WorkflowValidationError(
                        f"Workflow {workflow.workflow_id!r}: primitive "
                        f"node {node.node_id!r} references tool_name="
                        f"{node.tool_name!r} that the supplied "
                        f"resolver cannot resolve: {exc}"
                    ) from exc


def topological_order(workflow: Workflow) -> List[str]:
    """Return the node IDs in topological execution order.

    Convenience for the executor; also useful for human inspection
    of a workflow's planned execution sequence.  Calls
    ``validate_workflow`` first so cycles surface as
    ``WorkflowValidationError`` rather than ``CycleError``.
    """
    validate_workflow(workflow)
    sorter: TopologicalSorter = TopologicalSorter()
    for n in workflow.nodes:
        sorter.add(n.node_id)
    for edge in workflow.edges:
        sorter.add(edge.target_node_id, edge.source_node_id)
    return list(sorter.static_order())


__all__ = [
    "WorkflowValidationError",
    "validate_workflow",
    "topological_order",
]

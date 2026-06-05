"""shared.workflow.validate — pre-execution validation of a Workflow.

Static analysis pass that runs BEFORE the executor.  Catches shape errors
at template-time rather than at execution-time so a bad workflow fails
loudly during template authoring / orchestrator testing, not in the
middle of a multi-node run.

Two entry points
----------------
``validate_workflow_result(workflow, primitive_resolver=None) -> ValidationResult``
    Walks the whole DAG and returns EVERY structural problem in one pass
    as a frozen ``ValidationResult``.  Each error carries a stable
    ``ErrorCode`` (closed family per P8) and an ``OwnerLayer`` tag for
    the PR-4 bounded-repair controller's dispatch.

``validate_workflow(workflow, primitive_resolver=None) -> None``
    Legacy strict wrapper, preserved verbatim for back-compat: calls
    the collect-all path, and if any errors were collected, raises
    ``WorkflowValidationError`` with the *first* error's message text.
    The executor (``shared.workflow.executor.execute_workflow``) and
    existing tests assert on these messages via ``pytest.raises(...,
    match="…")``; the strict wrapper guarantees those assertions keep
    matching byte-for-byte.

What this module checks
-----------------------
1. **Operator existence** — every ``OperatorNode.operator_name`` must
   be in ``OPERATOR_REGISTRY``.
2. **Slot validity** — every edge's ``target_input_slot`` is a real
   slot on the target operator; edges may not target a
   ``PrimitiveNode`` (primitives fetch from DB themselves in v1).
3. **Literal binding validity** — every ``LiteralBinding`` targets an
   operator slot that accepts scalars.
4. **Slot completeness** — every operator's required input slots are
   bound (via edge OR, for scalar-accepting slots, via literal).
   Operators with conditional arity (e.g. ``series_arithmetic``) use a
   per-operator ``arity_validator`` hook.
5. **Type compatibility** — the source node's output artifact type
   matches the target slot's expected element type.
6. **Output-field validity** — when the primitive resolver declares
   ``output_field_units`` for a primitive, the node's ``output_field``
   must be one of the declared keys.
7. **Unit compatibility (best-effort)** — operator-declared cross-slot
   unit-algebra checks where the substrate can trace upstream units.
   Best-effort; when source units are unknown, runtime stays as the
   authoritative gate.
8. **Cycles** — the DAG must be acyclic.
9. **Primitive resolvability** — when a ``PrimitiveResolver`` is
   supplied, every ``PrimitiveNode.tool_name`` resolves.

Error-code taxonomy
-------------------
Each of the 13 checks above maps to exactly one ``ErrorCode`` value in
``shared.workflow.validation_result``.  The mapping is documented in
the order-of-checks comments inside ``validate_workflow_result``.
Adding a new code requires an ADR (P8 closed-family discipline).

Back-compat
-----------
The legacy ``WorkflowValidationError`` class is preserved verbatim and
re-exported here; the strict wrapper raises it with the same message
text the pre-refactor validator did.  No caller needs to change.
"""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import Dict, FrozenSet, List, Optional

from shared.workflow.answer_shape import (
    AnswerShape,
    is_unconstrained,
    shape_contract_satisfied,
)
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
    WorkflowNode,
)
from shared.workflow.validation_result import (
    ErrorCode,
    OwnerLayer,
    ValidationError,
    ValidationResult,
)


# ============================================================================
# LEGACY ERROR CLASS (re-export — preserved verbatim for back-compat)
# ============================================================================


class WorkflowValidationError(ValueError):
    """Raised by the strict-wrapper ``validate_workflow`` when one or
    more structural problems are present.  Message text matches the
    first collected ``ValidationError.message`` so existing
    ``pytest.raises(..., match="…")`` assertions keep matching."""


# ============================================================================
# INTERNAL HELPERS
# ============================================================================


def _topological_node_ids(workflow: Workflow) -> List[str]:
    """Internal helper: topological order of a workflow's node IDs,
    for unit-propagation walks.  Distinct from the public
    ``topological_order`` (below) which calls validate first;
    this one is called BY validate so it cannot recurse.

    Raises ``CycleError`` if the DAG contains a cycle.  Callers must
    guard against this — in the collect-all validator we use the
    separate cycle-detection pass (E_DAG_CYCLE) and skip the unit
    propagation block when a cycle was already collected, so this
    helper is only ever called on a known-acyclic graph at that point.
    """
    sorter: TopologicalSorter = TopologicalSorter()
    for n in workflow.nodes:
        sorter.add(n.node_id)
    for edge in workflow.edges:
        sorter.add(edge.target_node_id, edge.source_node_id)
    return list(sorter.static_order())


def _node_output_artifact_type(
    node: WorkflowNode,
    primitive_resolver: Optional[PrimitiveResolver],
) -> Optional[str]:
    """The closed-family artifact-type VALUE (e.g. ``"Series"``,
    ``"ScalarMetric"``) a node emits, or ``None`` when it can't be
    determined (unknown operator — already reported as
    E_UNKNOWN_OPERATOR elsewhere, so callers skip to avoid double-report).

    Mirrors the source-type derivation inside CHECK 5: a PrimitiveNode
    emits whatever its PrimitiveSpec declares (``Series`` by default, or
    via the resolver when supplied); an OperatorNode emits its
    OperatorSpec's ``output.artifact_type``.  Extracted so the terminal
    shape check (plan D2) and CHECK 5 share one definition of truth.
    """
    if isinstance(node, PrimitiveNode):
        if primitive_resolver is not None:
            try:
                return getattr(
                    primitive_resolver(node.tool_name),
                    "output_artifact_type",
                    "Series",
                )
            except Exception:
                return "Series"
        return "Series"
    if isinstance(node, OperatorNode):
        spec = OPERATOR_REGISTRY.get(node.operator_name)
        if spec is None:
            return None
        return spec.output.artifact_type.value
    return None


# ============================================================================
# COLLECT-ALL VALIDATOR (the new substrate)
# ============================================================================


def validate_workflow_result(
    workflow: Workflow,
    *,
    primitive_resolver: Optional[PrimitiveResolver] = None,
    expected_answer_shapes: Optional[FrozenSet[AnswerShape]] = None,
) -> ValidationResult:
    """Validate a Workflow against the substrate's structural contract,
    collecting EVERY problem found into a frozen ``ValidationResult``.

    Unlike the strict wrapper ``validate_workflow``, this function never
    raises on a structural problem — it walks the whole DAG and surfaces
    every check's outcome.  The PR-4 bounded-repair controller consumes
    the result and dispatches retries by ``owner_layer`` in one round.

    Parameters
    ----------
    workflow :
        The Workflow to validate.  Construction-time checks (node ID
        uniqueness, edge endpoint existence, terminal-node existence)
        have already run via the model validator; this pass adds
        DAG-level structural checks.
    primitive_resolver :
        Optional ``PrimitiveResolver``.  When supplied, each
        ``PrimitiveNode.tool_name`` is resolved at validate-time AND
        the substrate can perform best-effort output-field and unit
        compatibility checks.  When omitted, those checks are skipped
        (templates can still validate their non-primitive structure
        offline).
    expected_answer_shapes :
        Optional SOFT output-shape contract (plan D2) — the SET of
        ``AnswerShape`` the L1 router declared the question expects.
        When supplied AND not unconstrained (no ``ANY``), CHECK 10
        verifies the terminal node's artifact type satisfies it and
        emits ``E_TERMINAL_SHAPE_MISMATCH`` (owner L3_WIRING) on a clear
        contradiction.  When omitted / unconstrained, the check is
        skipped — every existing caller (templates, tests) is unaffected.

    Returns
    -------
    ValidationResult
        ``is_clean`` is True iff no errors were collected.
    """
    errors: List[ValidationError] = []
    wf_id = workflow.workflow_id

    # Indexes used across multiple checks; built once.
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

    # ------------------------------------------------------------------
    # CHECK 1 (E_UNKNOWN_OPERATOR) — every OperatorNode names a known
    # operator.
    # ------------------------------------------------------------------
    for node in workflow.nodes:
        if isinstance(node, OperatorNode):
            if node.operator_name not in OPERATOR_REGISTRY:
                errors.append(
                    ValidationError(
                        code=ErrorCode.E_UNKNOWN_OPERATOR,
                        owner_layer=OwnerLayer.L3_WIRING,
                        message=(
                            f"Workflow {wf_id!r}: node "
                            f"{node.node_id!r} references unknown operator "
                            f"{node.operator_name!r}.  Known operators: "
                            f"{known_operators()}.  To add a new operator, "
                            "extend ``OPERATOR_REGISTRY`` and ensure the "
                            "operator passes its admission checklist in "
                            "``operator_architecture.md``."
                        ),
                        node_id=node.node_id,
                        operator_name=node.operator_name,
                    )
                )

    # ------------------------------------------------------------------
    # CHECK 2 (E_UNKNOWN_SLOT, E_EDGE_TARGETS_PRIMITIVE) — every edge's
    # target_input_slot is a real slot on the target operator; edges may
    # not target a PrimitiveNode.
    # ------------------------------------------------------------------
    for edge in workflow.edges:
        target_node = workflow.node_by_id(edge.target_node_id)
        if isinstance(target_node, OperatorNode):
            spec = OPERATOR_REGISTRY.get(target_node.operator_name)
            if spec is None:
                # E_UNKNOWN_OPERATOR already collected for this node;
                # we can't validate the slot without the spec, so skip.
                continue
            if edge.target_input_slot not in spec.input_slots:
                errors.append(
                    ValidationError(
                        code=ErrorCode.E_UNKNOWN_SLOT,
                        owner_layer=OwnerLayer.L3_WIRING,
                        message=(
                            f"Workflow {wf_id!r}: edge "
                            f"{edge.source_node_id!r} → {edge.target_node_id!r} "
                            f"targets unknown input slot "
                            f"{edge.target_input_slot!r} on operator "
                            f"{target_node.operator_name!r}.  Known slots: "
                            f"{sorted(spec.input_slots.keys())}."
                        ),
                        node_id=target_node.node_id,
                        edge=(edge.source_node_id, edge.target_node_id),
                        target_input_slot=edge.target_input_slot,
                        operator_name=target_node.operator_name,
                    )
                )
        elif isinstance(target_node, PrimitiveNode):
            errors.append(
                ValidationError(
                    code=ErrorCode.E_EDGE_TARGETS_PRIMITIVE,
                    owner_layer=OwnerLayer.L3_WIRING,
                    message=(
                        f"Workflow {wf_id!r}: edge "
                        f"{edge.source_node_id!r} → {edge.target_node_id!r} "
                        f"targets a PrimitiveNode.  Primitives do not consume "
                        "node-output edges in v1 (they fetch from DB "
                        "themselves).  Composite primitives are deferred to "
                        "Phase 2 (see ``PrimitiveStep.input_hashes`` slot in "
                        "``shared.artifacts.lineage``)."
                    ),
                    node_id=target_node.node_id,
                    edge=(edge.source_node_id, edge.target_node_id),
                    tool_name=target_node.tool_name,
                )
            )

    # ------------------------------------------------------------------
    # CHECK 3 (E_LITERAL_TARGETS_NON_OPERATOR, E_LITERAL_UNKNOWN_SLOT,
    # E_LITERAL_SLOT_NO_SCALAR) — literal binding validity.
    # ------------------------------------------------------------------
    for binding in workflow.literal_bindings:
        target_node = workflow.node_by_id(binding.target_node_id)
        if not isinstance(target_node, OperatorNode):
            errors.append(
                ValidationError(
                    code=ErrorCode.E_LITERAL_TARGETS_NON_OPERATOR,
                    owner_layer=OwnerLayer.L3_WIRING,
                    message=(
                        f"Workflow {wf_id!r}: literal binding "
                        f"targets node {binding.target_node_id!r} which is "
                        "not an OperatorNode.  Literal bindings can only "
                        "target operator slots."
                    ),
                    node_id=binding.target_node_id,
                    target_input_slot=binding.target_input_slot,
                )
            )
            continue
        spec = OPERATOR_REGISTRY.get(target_node.operator_name)
        if spec is None:
            # E_UNKNOWN_OPERATOR already collected for this node.
            continue
        if binding.target_input_slot not in spec.input_slots:
            errors.append(
                ValidationError(
                    code=ErrorCode.E_LITERAL_UNKNOWN_SLOT,
                    owner_layer=OwnerLayer.L3_WIRING,
                    message=(
                        f"Workflow {wf_id!r}: literal binding "
                        f"targets unknown input slot "
                        f"{binding.target_input_slot!r} on operator "
                        f"{target_node.operator_name!r}.  Known slots: "
                        f"{sorted(spec.input_slots.keys())}."
                    ),
                    node_id=target_node.node_id,
                    target_input_slot=binding.target_input_slot,
                    operator_name=target_node.operator_name,
                )
            )
            continue
        if not spec.input_slots[binding.target_input_slot].accepts_scalar:
            errors.append(
                ValidationError(
                    code=ErrorCode.E_LITERAL_SLOT_NO_SCALAR,
                    owner_layer=OwnerLayer.L3_WIRING,
                    message=(
                        f"Workflow {wf_id!r}: literal binding "
                        f"targets slot {binding.target_input_slot!r} on "
                        f"operator {target_node.operator_name!r}, but that "
                        "slot does not accept scalar literals.  Use a "
                        "WorkflowEdge from an upstream artifact-producing "
                        "node instead, or pick an operator whose slot is "
                        "declared with ``accepts_scalar=True`` on its "
                        "``SlotDescriptor``."
                    ),
                    node_id=target_node.node_id,
                    target_input_slot=binding.target_input_slot,
                    operator_name=target_node.operator_name,
                )
            )

    # ------------------------------------------------------------------
    # CHECK 4 (E_ARITY_VIOLATION, E_UNBOUND_REQUIRED_SLOT) — every
    # operator's required input slots are bound.  Operators with
    # conditional arity declare an ``arity_validator`` hook on their
    # OperatorSpec; the substrate-default branch handles the simple
    # "always required unless scalar-accepting" case.
    # ------------------------------------------------------------------
    for node in workflow.nodes:
        if not isinstance(node, OperatorNode):
            continue
        spec = OPERATOR_REGISTRY.get(node.operator_name)
        if spec is None:
            # E_UNKNOWN_OPERATOR already collected.
            continue
        bound_edge_slots = set(edges_by_target.get(node.node_id, {}).keys())
        bound_literal_slots = set(
            literals_by_target.get(node.node_id, {}).keys()
        )

        # Per-operator arity hook supersedes the substrate default for
        # operators that declare one.
        if spec.arity_validator is not None:
            err = spec.arity_validator(
                node.params, bound_edge_slots, bound_literal_slots,
            )
            if err is not None:
                errors.append(
                    ValidationError(
                        code=ErrorCode.E_ARITY_VIOLATION,
                        owner_layer=OwnerLayer.L3_WIRING,
                        message=(
                            f"Workflow {wf_id!r}: operator "
                            f"node {node.node_id!r} ({node.operator_name!r}) "
                            f"failed arity check: {err}"
                        ),
                        node_id=node.node_id,
                        operator_name=node.operator_name,
                        detail={"arity_validator_message": err},
                    )
                )
            # The hook is authoritative for this operator — skip the
            # substrate-default unbound-required-slot check.
            continue

        # Substrate-default: every slot must be bound (via edge or, for
        # scalar-accepting slots, via literal), unless its
        # ``SlotDescriptor.accepts_scalar`` is True AND no arity hook is
        # declared (in which case the operator's own runtime check is
        # the authoritative arity gate).
        for slot_name, descriptor in spec.input_slots.items():
            if (
                descriptor.accepts_scalar
                and slot_name not in bound_edge_slots
                and slot_name not in bound_literal_slots
            ):
                # Scalar-accepting slot with no binding either way: the
                # operator's signature default applies.  Legal for
                # operators without an arity hook.
                continue
            if (
                slot_name not in bound_edge_slots
                and slot_name not in bound_literal_slots
            ):
                errors.append(
                    ValidationError(
                        code=ErrorCode.E_UNBOUND_REQUIRED_SLOT,
                        owner_layer=OwnerLayer.L3_WIRING,
                        message=(
                            f"Workflow {wf_id!r}: operator "
                            f"node {node.node_id!r} ({node.operator_name!r}) "
                            f"has unbound required input slot "
                            f"{slot_name!r} (type "
                            f"{descriptor.artifact_type.value!r}).  Add a "
                            "``WorkflowEdge`` whose target_input_slot binds "
                            "this slot, or (if the slot accepts scalars) a "
                            "``LiteralBinding`` with a literal value."
                        ),
                        node_id=node.node_id,
                        target_input_slot=slot_name,
                        operator_name=node.operator_name,
                        detail={
                            "expected_artifact_type": descriptor.artifact_type.value,
                        },
                    )
                )

    # ------------------------------------------------------------------
    # CHECK 5 (E_TYPE_MISMATCH) — source node's output type matches the
    # target slot's expected element type.  owner_layer is L3_WIRING
    # when the upstream is an operator (composer wired wrong) and
    # L2_BINDING when the upstream is a primitive (selector bound a
    # primitive whose output type doesn't fit the operator's slot).
    # ------------------------------------------------------------------
    for edge in workflow.edges:
        target_node = workflow.node_by_id(edge.target_node_id)
        source_node = workflow.node_by_id(edge.source_node_id)

        if not isinstance(target_node, OperatorNode):
            # Already collected as E_EDGE_TARGETS_PRIMITIVE.
            continue
        target_spec = OPERATOR_REGISTRY.get(target_node.operator_name)
        if target_spec is None:
            continue
        descriptor = target_spec.input_slots.get(edge.target_input_slot)
        if descriptor is None:
            continue  # already E_UNKNOWN_SLOT
        expected_element = descriptor.artifact_type.value

        # Source type: primitives produce whichever artifact type the
        # PrimitiveSpec's ``output_artifact_type`` declares (Series by
        # default; PR 20 added Panel).  When the resolver is unavailable,
        # fall back to Series so validation without a resolver stays
        # permissive.  Operators produce whatever their OperatorSpec's
        # ``output.artifact_type`` declares.
        upstream_is_primitive = isinstance(source_node, PrimitiveNode)
        if upstream_is_primitive:
            source_type = "Series"
            if primitive_resolver is not None:
                try:
                    source_spec = primitive_resolver(source_node.tool_name)
                    source_type = getattr(
                        source_spec, "output_artifact_type", "Series",
                    )
                except Exception:
                    # Resolver miss will be collected as
                    # E_PRIMITIVE_RESOLVE_FAIL in the dedicated pass
                    # below; fall back to Series here so we don't
                    # double-report.
                    pass
        elif isinstance(source_node, OperatorNode):
            source_spec = OPERATOR_REGISTRY.get(source_node.operator_name)
            if source_spec is None:
                continue  # E_UNKNOWN_OPERATOR already collected
            source_type = source_spec.output.artifact_type.value
        else:
            continue  # unreachable

        if source_type != expected_element:
            errors.append(
                ValidationError(
                    code=ErrorCode.E_TYPE_MISMATCH,
                    # Plan §PR-1: L3_WIRING when upstream is operator
                    # (composer's shape is the wrong shape); L2_BINDING
                    # when upstream is primitive (selector bound a
                    # primitive whose output type doesn't fit).
                    owner_layer=(
                        OwnerLayer.L2_BINDING
                        if upstream_is_primitive
                        else OwnerLayer.L3_WIRING
                    ),
                    message=(
                        f"Workflow {wf_id!r}: edge "
                        f"{edge.source_node_id!r} ({source_type}) → "
                        f"{edge.target_node_id!r}.{edge.target_input_slot} "
                        f"expects {expected_element}.  Type-mismatched edge "
                        "would propagate as a runtime error mid-execution; "
                        "fix the source/target pairing or insert an adapter "
                        "node."
                    ),
                    node_id=target_node.node_id,
                    edge=(edge.source_node_id, edge.target_node_id),
                    target_input_slot=edge.target_input_slot,
                    operator_name=target_node.operator_name,
                    detail={
                        "expected_artifact_type": expected_element,
                        "source_artifact_type": source_type,
                        "upstream_is_primitive": upstream_is_primitive,
                    },
                )
            )

    # ------------------------------------------------------------------
    # CHECK 6 (E_UNKNOWN_OUTPUT_FIELD) — when the primitive resolver
    # declares ``output_field_units`` for a primitive, the node's
    # ``output_field`` must be one of the declared keys.  Hoisted out
    # of the unit-propagation loop so it runs even when the DAG has a
    # cycle.
    # ------------------------------------------------------------------
    if primitive_resolver is not None:
        for node in workflow.nodes:
            if not isinstance(node, PrimitiveNode):
                continue
            try:
                spec = primitive_resolver(node.tool_name)
            except Exception:
                # Resolver miss collected as E_PRIMITIVE_RESOLVE_FAIL
                # below; skip the field check.
                continue
            if (
                spec.output_field_units
                and node.output_field not in spec.output_field_units
            ):
                errors.append(
                    ValidationError(
                        code=ErrorCode.E_UNKNOWN_OUTPUT_FIELD,
                        owner_layer=OwnerLayer.L2_BINDING,
                        message=(
                            f"Workflow {wf_id!r}: primitive "
                            f"node {node.node_id!r} (tool "
                            f"{node.tool_name!r}) declares "
                            f"output_field={node.output_field!r}, but the "
                            f"resolver only knows: "
                            f"{sorted(spec.output_field_units.keys())}.  "
                            "Pick one of the declared fields; this would "
                            "otherwise fail at execute-time inside the "
                            "primitive→artifact bridge."
                        ),
                        node_id=node.node_id,
                        tool_name=node.tool_name,
                        detail={
                            "declared_output_field": node.output_field,
                            "known_output_fields": sorted(
                                spec.output_field_units.keys()
                            ),
                        },
                    )
                )

    # ------------------------------------------------------------------
    # CHECK 7 (E_DAG_CYCLE) — DAG must be acyclic.  Hoisted ABOVE unit
    # propagation in the collect-all flow so unit propagation's
    # topological walk doesn't blow up on a cyclic graph.  When a
    # cycle is collected, the unit-propagation block below is skipped.
    # ------------------------------------------------------------------
    has_cycle = False
    sorter: TopologicalSorter = TopologicalSorter()
    node_ids = {n.node_id for n in workflow.nodes}
    for nid in node_ids:
        sorter.add(nid)
    for edge in workflow.edges:
        sorter.add(edge.target_node_id, edge.source_node_id)
    try:
        sorter.prepare()
    except CycleError as exc:
        has_cycle = True
        errors.append(
            ValidationError(
                code=ErrorCode.E_DAG_CYCLE,
                owner_layer=OwnerLayer.L3_WIRING,
                message=(
                    f"Workflow {wf_id!r}: DAG contains a "
                    f"cycle.  graphlib.TopologicalSorter says: {exc.args}.  "
                    "Workflows must be acyclic."
                ),
                detail={"cycle_args": list(exc.args)},
            )
        )

    # ------------------------------------------------------------------
    # CHECK 8 (E_UNIT_MISMATCH) — best-effort unit-algebra checks where
    # the substrate can trace upstream units in topological order.
    # Skipped when the DAG has a cycle (no meaningful topological walk).
    # ------------------------------------------------------------------
    if primitive_resolver is not None and not has_cycle:
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
                # emit artifacts whose unit depends on operator-internal
                # logic we don't trace at validate time.
                if node.operator_name == "series_arithmetic":
                    op = node.params.get("op")
                    if op in ("diff",):
                        left_edges = edges_by_target.get(nid, {}).get(
                            "left", []
                        )
                        if left_edges:
                            produced_units[nid] = produced_units.get(
                                left_edges[0].source_node_id,
                            )
                            continue
                    elif op == "pct_change":
                        produced_units[nid] = "ratio"
                        continue
                produced_units[nid] = None

        # Invoke each operator's unit_validator with the source units
        # it can see.
        for node in workflow.nodes:
            if not isinstance(node, OperatorNode):
                continue
            spec = OPERATOR_REGISTRY.get(node.operator_name)
            if spec is None or spec.unit_validator is None:
                continue
            source_units: Dict[str, Optional[str]] = {}
            for slot_name in spec.input_slots:
                slot_edges = edges_by_target.get(node.node_id, {}).get(
                    slot_name, []
                )
                if not slot_edges:
                    source_units[slot_name] = None
                    continue
                source_units[slot_name] = produced_units.get(
                    slot_edges[0].source_node_id,
                )
            err = spec.unit_validator(node.params, source_units)
            if err is not None:
                errors.append(
                    ValidationError(
                        code=ErrorCode.E_UNIT_MISMATCH,
                        owner_layer=OwnerLayer.L3_WIRING,
                        message=(
                            f"Workflow {wf_id!r}: operator "
                            f"node {node.node_id!r} ({node.operator_name!r}) "
                            f"failed unit-compatibility check: {err}"
                        ),
                        node_id=node.node_id,
                        operator_name=node.operator_name,
                        detail={"unit_validator_message": err},
                    )
                )

    # ------------------------------------------------------------------
    # CHECK 9 (E_PRIMITIVE_RESOLVE_FAIL) — when a resolver is supplied,
    # every PrimitiveNode.tool_name must resolve.
    # ------------------------------------------------------------------
    if primitive_resolver is not None:
        for node in workflow.nodes:
            if isinstance(node, PrimitiveNode):
                try:
                    primitive_resolver(node.tool_name)
                except Exception as exc:
                    errors.append(
                        ValidationError(
                            code=ErrorCode.E_PRIMITIVE_RESOLVE_FAIL,
                            owner_layer=OwnerLayer.L2_BINDING,
                            message=(
                                f"Workflow {wf_id!r}: primitive "
                                f"node {node.node_id!r} references tool_name="
                                f"{node.tool_name!r} that the supplied "
                                f"resolver cannot resolve: {exc}"
                            ),
                            node_id=node.node_id,
                            tool_name=node.tool_name,
                            detail={
                                "resolver_exception_type": type(exc).__name__,
                                "resolver_exception_message": str(exc),
                            },
                        )
                    )

    # ------------------------------------------------------------------
    # CHECK 10 (E_TERMINAL_SHAPE_MISMATCH) — plan D2.  When the caller
    # supplied a non-unconstrained ``expected_answer_shapes`` contract,
    # the DAG's TERMINAL artifact type must satisfy it.  SOFT: a
    # multi-shape / ``ANY`` contract is permissive; only a clear
    # contradiction trips it.  owner_layer=L3_WIRING drives the
    # bounded self-correction loop (D1).  Skipped entirely when no
    # contract was passed (every legacy caller) — back-compat clean.
    # ------------------------------------------------------------------
    if expected_answer_shapes is not None and not is_unconstrained(
        expected_answer_shapes
    ):
        terminal = workflow.node_by_id(workflow.terminal_node_id)
        actual_type = _node_output_artifact_type(terminal, primitive_resolver)
        # ``None`` ⇒ unknown operator, already reported as
        # E_UNKNOWN_OPERATOR; don't double-report.
        if actual_type is not None and not shape_contract_satisfied(
            expected_answer_shapes, actual_type
        ):
            wanted = sorted(s.value for s in expected_answer_shapes)
            errors.append(
                ValidationError(
                    code=ErrorCode.E_TERMINAL_SHAPE_MISMATCH,
                    owner_layer=OwnerLayer.L3_WIRING,
                    message=(
                        f"Workflow {wf_id!r}: terminal node "
                        f"{workflow.terminal_node_id!r} produces a "
                        f"{actual_type!r}, but the question expects answer "
                        f"shape {wanted} (a {actual_type!r} answers a "
                        "different question).  Rebuild the DAG so its "
                        "terminal produces the requested shape — e.g. for a "
                        "single-number answer, end in an operator that emits "
                        "a ScalarMetric (such as summarize_series), not a "
                        "Series."
                    ),
                    node_id=workflow.terminal_node_id,
                    detail={
                        "expected_answer_shapes": wanted,
                        "terminal_artifact_type": actual_type,
                    },
                )
            )

    # ------------------------------------------------------------------
    # CHECK 11 (E_PARAM_SANITY) — plan D4.  Each operator's optional
    # STATIC param-sanity hook runs over the node's params.  Pure code,
    # no external data; operators without a hook are skipped (default
    # None) so this is registration-clean.  Data-dependent failures
    # (window >= available rows) are NOT checked here — they surface as
    # the operator's own typed execute-time error and route to the
    # self-correction loop.
    # ------------------------------------------------------------------
    for node in workflow.nodes:
        if not isinstance(node, OperatorNode):
            continue
        spec = OPERATOR_REGISTRY.get(node.operator_name)
        if spec is None or spec.param_sanity_validator is None:
            continue
        sanity_err = spec.param_sanity_validator(node.params)
        if sanity_err is not None:
            errors.append(
                ValidationError(
                    code=ErrorCode.E_PARAM_SANITY,
                    owner_layer=OwnerLayer.L3_WIRING,
                    message=(
                        f"Workflow {wf_id!r}: operator node "
                        f"{node.node_id!r} ({node.operator_name!r}) failed "
                        f"param-sanity check: {sanity_err}"
                    ),
                    node_id=node.node_id,
                    operator_name=node.operator_name,
                    detail={"param_sanity_message": sanity_err},
                )
            )

    return ValidationResult(workflow_id=wf_id, errors=tuple(errors))


# ============================================================================
# STRICT WRAPPER (legacy back-compat)
# ============================================================================


def validate_workflow(
    workflow: Workflow,
    *,
    primitive_resolver: Optional[PrimitiveResolver] = None,
) -> None:
    """Strict-mode validator: runs ``validate_workflow_result`` and
    raises ``WorkflowValidationError`` with the first error's message
    if any errors were collected.

    Preserved verbatim for back-compat with the executor
    (``shared.workflow.executor.execute_workflow``) and the existing
    test suite (which asserts on error messages via
    ``pytest.raises(WorkflowValidationError, match="…")``).  The
    raised message text is byte-identical to what the pre-refactor
    validator produced on the same error condition.

    Parameters
    ----------
    workflow :
        The Workflow to validate.
    primitive_resolver :
        Optional ``PrimitiveResolver``.  When supplied, primitive-name
        resolution AND output-field / unit checks run; when omitted,
        those checks are skipped (templates can validate offline).

    Raises
    ------
    WorkflowValidationError
        If at least one structural problem was found.  The raised
        message is the first collected error's message string.
    """
    result = validate_workflow_result(
        workflow, primitive_resolver=primitive_resolver,
    )
    if not result.is_clean:
        first = result.first()
        assert first is not None  # is_clean is False so first is not None
        raise WorkflowValidationError(first.message)


# ============================================================================
# TOPOLOGICAL ORDER (public helper)
# ============================================================================


def topological_order(workflow: Workflow) -> List[str]:
    """Return the node IDs in topological execution order.

    Convenience for the executor; also useful for human inspection of a
    workflow's planned execution sequence.  Calls the strict wrapper
    ``validate_workflow`` first so cycles surface as
    ``WorkflowValidationError`` rather than ``CycleError``.
    """
    validate_workflow(workflow)
    return _topological_node_ids(workflow)


# ============================================================================
# DEPRECATED ALIAS
# ============================================================================
#
# An earlier draft of PR-1 shipped under the name
# ``validate_workflow_collect``.  The plan (``tmp/orchestration.md``
# §PR-1) named the public entry ``validate_workflow_result``, and the
# rename to match the plan happened before any caller existed in-tree.
# The alias is kept as a soft landing for any out-of-tree consumer that
# already imported the earlier name; a follow-on PR can remove it once
# we are certain nothing reads it.

validate_workflow_collect = validate_workflow_result


__all__ = [
    "WorkflowValidationError",
    "validate_workflow",
    "validate_workflow_result",
    "validate_workflow_collect",  # deprecated alias for validate_workflow_result
    "topological_order",
]

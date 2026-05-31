"""shared.workflow.assembler — PR-4 of the open-DAG PoC.

The substrate's bounded-repair controller.  Takes a ``ShapeSpec`` from
L3 (with leaf-holes), a list of ``BoundLeaf`` from L2 (one per hole),
and a ``PrimitiveResolver`` from the agent layer, and produces a
runnable ``Workflow`` — OR refuses with a structured trace.

The contract
============

  Assembler.assemble(shape, leaves) -> AssemblyResult

Where ``AssemblyResult`` is either:
  - ``status=CLEAN`` with a substituted, validated ``Workflow``, or
  - ``status=REFUSED`` with the final ``ValidationResult`` and a
    structured repair trace.

Three-pass pipeline
===================

1. **Substitute**: replace every ``LeafHole`` with a ``PrimitiveNode``
   whose ``tool_name`` is the BoundLeaf's ``resolver_tool_key`` (the
   one the global resolver dispatches on — derived via
   ``shared.workflow.resolver_keys.domain_to_resolver_key``).

2. **Validate**: two layers in one pass:
   - **Contract-level (PR-4 own)**: per-leaf LeafRequest vs BoundLeaf.
     Hard for closed-substrate fields (artifact type, units,
     frequency) → ``E_TYPE_MISMATCH`` / ``E_UNIT_MISMATCH`` /
     ``E_FREQUENCY_MISMATCH`` at severity=ERROR with
     owner_layer=L2_BINDING.  Soft for free-form fields
     (semantic_role / requested_output_meaning) → normalised-string
     compare; mismatch surfaces as ``E_ROLE_DISCRIMINANT_MISMATCH``
     at severity=WARNING.
   - **Structural (PR-1's ``validate_workflow_result``)**: the
     existing 13-code substrate check (cycles, slot existence, type
     compat, output_field validity, unit_validator hooks, etc.).

3. **One bounded repair round**: when validation finds hard errors,
   partition by ``owner_layer`` and dispatch:
   - ``L2_BINDING`` errors → call the supplied ``LeafRebinder`` once
     per affected leaf with the leaf's request, current BoundLeaf,
     and the errors targeting it.  Get an updated BoundLeaf (or a
     refusal).
   - ``L3_WIRING`` errors → call the supplied ``ShapePatchProvider``
     with the assembled Workflow and the errors.  Get a sequence of
     ``InsertAdapterNode`` / ``RewireEdge`` patches.  Apply them.
   - ``ASSEMBLER`` errors → handled internally (typically a missing
     or duplicate leaf — refused early before this step).

   Re-substitute (if any leaves rebound), re-apply patches, re-validate.
   If still has hard errors → ``REFUSED``.

Repair-mutation discipline (additive only)
==========================================

The plan (``tmp/orchestration.md`` §PR-4) bans destructive mutations:
no primitive swap, no DAG re-shape, no multi-round retry.  This module
enforces it by construction — the patch types declared here
(``InsertAdapterNode``, ``RewireEdge``) are the ONLY mutations the
patch provider can return.  Any other instinct (replace an operator,
remove a node, change the terminal) means the Composer's shape was
wrong → refuse and let Boundary B (PR-8) escalate to one precise
clarification.

The Assembler never invents bindings or fills missing data.  It only
orchestrates Composer / Selector retries on LLM-authored content; if a
callback is not supplied and the corresponding owner_layer has hard
errors, the assembler refuses.

Finance-blindness (P11 / P9)
============================

This module sits in ``shared/workflow/``.  It does NOT import from
``rates_agent/`` or from ``orchestrator/`` — agent-side callbacks
(Composer's patch provider, Selectors' rebinder) and the
``PrimitiveResolver`` are passed in by the caller via the typed
Protocols declared here.  Tests use synthetic callbacks; PR-6 and
PR-7 will wire the real LLM-backed implementations.
"""

from __future__ import annotations

from enum import Enum
from typing import (
    Annotated, Any, Dict, List, Literal, Optional, Protocol, Sequence, Tuple,
    Union,
)

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.registry import ArtifactTypeName
from shared.schemas.time_series import TimeSeriesUnits
from orchestrator.open_dag.contracts import (
    BoundLeaf, LeafHole, LeafRequest, ShapeSpec,
)
from shared.workflow.registry import PrimitiveResolver
from shared.workflow.types import (
    LiteralBinding,
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from shared.workflow.validate import validate_workflow_result
from shared.workflow.validation_result import (
    ErrorCode,
    OwnerLayer,
    Severity,
    ValidationError,
    ValidationResult,
)


# ============================================================================
# REPAIR + ASSEMBLY OUTCOME ENUMS
# ============================================================================


class AssemblyStatus(str, Enum):
    """Outcome of ``Assembler.assemble``.  Two terminal values — there
    is no PARTIAL state; either the assembled Workflow is ready to
    execute (CLEAN) or it is refused (REFUSED).  The intermediate
    ``WARNING`` severity from ``ValidationResult`` does NOT block
    assembly — warnings flow to Boundary B (PR-8)."""

    CLEAN = "CLEAN"
    REFUSED = "REFUSED"


class RepairKind(str, Enum):
    """Closed family of repair operations applied during ``assemble``.
    The Assembler trace records exactly which of these ran, in order,
    for full auditability of the bounded-repair round."""

    # Initial sweep (always present in trace when assemble runs).
    SUBSTITUTE_LEAF = "SUBSTITUTE_LEAF"
    # Repair-round operations:
    REBIND_LEAF = "REBIND_LEAF"
    INSERT_ADAPTER_NODE = "INSERT_ADAPTER_NODE"
    REWIRE_EDGE = "REWIRE_EDGE"


# ============================================================================
# REPAIR TRACE
# ============================================================================


class RepairStep(BaseModel):
    """One entry in the assembler's repair trace.

    Frozen.  Every mutation the Assembler applies (substitution, leaf
    rebind, adapter insert, edge rewire) appends one RepairStep so the
    full sequence is auditable.  Per PR-4's acceptance criterion 2 ('The
    trace lists every mutation applied in repair (auditable).')."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RepairKind
    rationale: str = Field(..., min_length=1)
    leaf_id: Optional[str] = None
    node_id: Optional[str] = None
    edge: Optional[Tuple[str, str, str]] = Field(
        default=None,
        description="(source_node_id, target_node_id, target_input_slot)",
    )
    detail: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# SHAPE PATCHES (the additive-only mutation vocabulary)
# ============================================================================


class InsertAdapterNode(BaseModel):
    """Insert an operator node on an existing edge, splitting it into
    two edges that route the data through the adapter.

    Concretely:
      original edge:   source -> target on slot S
      after insertion: source -> adapter on slot adapter_input_slot
                       adapter -> target on slot S

    Adapter operators in V1 are ``convert_units`` (unit mismatch) and
    ``align_series`` (frequency / index mismatch); the patch payload
    carries the adapter's params + optional literal bindings so the
    Assembler can construct a complete OperatorNode + LiteralBinding
    set inline.

    Frozen; round-trips through JSON for the trace.

    The ``adapter_operator_name`` is restricted to a closed whitelist
    (currently ``convert_units`` and ``align_series``) so the Composer
    cannot smuggle an arbitrary operator under the "adapter" label —
    that would be DAG-reshaping by another name, which the plan
    (``tmp/orchestration.md`` §PR-4) explicitly bans.  Adding a new
    adapter operator is an ADR-recorded extension.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["insert_adapter_node"] = "insert_adapter_node"
    on_edge_source: str = Field(..., min_length=1)
    on_edge_target: str = Field(..., min_length=1)
    on_edge_slot: str = Field(..., min_length=1)
    adapter_node_id: str = Field(..., min_length=1)
    adapter_operator_name: Literal["convert_units", "align_series"] = Field(
        ...,
        description=(
            "Closed whitelist of allowed adapter operators per "
            "tmp/orchestration.md §PR-4.  Adding another adapter "
            "requires an ADR — arbitrary operators would let the "
            "Composer reshape the DAG under the 'adapter' label."
        ),
    )
    adapter_input_slot: str = Field(
        ...,
        min_length=1,
        description=(
            "Which input slot on the adapter receives the original "
            "source's output.  E.g. for convert_units the slot is "
            "'series'; for align_series fan-in it's 'series_list'."
        ),
    )
    adapter_params: Dict[str, Any] = Field(default_factory=dict)
    adapter_literal_bindings: List[LiteralBinding] = Field(
        default_factory=list,
        description=(
            "Optional scalar literal bindings on the adapter's slots "
            "(e.g. target_units='bps' for a convert_units adapter is "
            "passed via params; a scalar 'right' operand for "
            "series_arithmetic.multiply would go here)."
        ),
    )


class RewireEdge(BaseModel):
    """Change the ``target_input_slot`` of an existing edge.

    Bounded by P3 / PR-4 discipline: the source AND target nodes do
    NOT change; only the slot does.  This covers the Composer's
    'I picked the wrong slot on the right operator' fix without
    re-shaping the DAG.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["rewire_edge"] = "rewire_edge"
    source_node_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    current_target_input_slot: str = Field(..., min_length=1)
    new_target_input_slot: str = Field(..., min_length=1)


# Discriminated union over the closed mutation family — the Composer's
# patch provider MAY ONLY return values of these types.
ShapePatch = Annotated[
    Union[InsertAdapterNode, RewireEdge],
    Field(discriminator="kind"),
]


# ============================================================================
# CALLBACK PROTOCOLS (PR-6 / PR-7 will implement)
# ============================================================================


class ShapePatchProvider(Protocol):
    """Callback signature for the Composer-side repair (PR-7).

    Invoked by the Assembler with the post-substitution Workflow and
    the sequence of L3_WIRING hard errors that need fixing.  Must
    return a sequence of additive patches; returning an empty sequence
    is treated as 'no patch possible' and refuses the assembly.

    Implementations MUST NOT mutate the inputs.  They MAY return
    InsertAdapterNode / RewireEdge instances only — any other patch
    type is rejected at the type system level by ``ShapePatch``.
    """

    def __call__(
        self,
        workflow: Workflow,
        errors: Sequence[ValidationError],
    ) -> Sequence[ShapePatch]: ...


class LeafRebinder(Protocol):
    """Callback signature for the Selector-side repair (PR-6).

    Invoked once per leaf that has hard L2_BINDING errors after
    substitution.  Receives the original ``LeafRequest`` from the
    LeafHole, the current ``BoundLeaf`` the Selector returned, and
    the validation errors that targeted this leaf.  Must return a
    fresh ``BoundLeaf`` — either a corrected binding (refusal=None)
    or a refusal (refusal=str).

    Implementations route by ``leaf_request.domain_hint`` to the
    correct per-domain Selector.
    """

    def __call__(
        self,
        leaf_request: LeafRequest,
        current_bound: BoundLeaf,
        errors: Sequence[ValidationError],
    ) -> BoundLeaf: ...


# ============================================================================
# ASSEMBLY RESULT
# ============================================================================


class AssemblyResult(BaseModel):
    """The structured outcome of ``Assembler.assemble``.

    Frozen.  ``status`` is the terminal verdict; ``workflow`` is the
    runnable Workflow when CLEAN (None when REFUSED);
    ``validation_result`` is the final pass's full ValidationResult
    (carries warnings + any residual errors); ``repair_trace`` lists
    every mutation the Assembler applied in declaration order; and
    ``refusal_reasons`` is a tuple of human-readable strings — one per
    distinct cause when REFUSED (per-leaf refusal text, repair
    exhaustion note, missing callback, etc.) — that Boundary B (PR-8)
    can use when composing a clarification or refusal message.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AssemblyStatus
    workflow: Optional[Workflow] = None
    validation_result: ValidationResult
    repair_trace: Tuple[RepairStep, ...] = ()
    refusal_reasons: Tuple[str, ...] = ()

    @model_validator(mode="after")
    def _validate_consistency(self) -> "AssemblyResult":
        if self.status == AssemblyStatus.CLEAN:
            if self.workflow is None:
                raise ValueError(
                    "AssemblyResult(status=CLEAN) must carry a non-None "
                    "Workflow.  CLEAN means 'ready to execute'."
                )
            if self.validation_result.hard_errors:
                raise ValueError(
                    "AssemblyResult(status=CLEAN) cannot carry hard "
                    "errors — that would be a contradictory verdict.  "
                    "Warnings are allowed and flow to Boundary B."
                )
        else:  # REFUSED
            if self.workflow is not None:
                raise ValueError(
                    "AssemblyResult(status=REFUSED) must carry "
                    "workflow=None — refusal means the caller MUST "
                    "NOT execute.  Inspect validation_result.errors "
                    "and repair_trace for diagnostics."
                )
        return self


# ============================================================================
# THE ASSEMBLER
# ============================================================================


class Assembler:
    """The PR-4 bounded-repair controller.

    Constructed once per session (or once per request) with:
      - a ``PrimitiveResolver`` (the agent layer's resolver, e.g.
        ``rates_agent.workflows.rates_primitive_resolver``).
      - an optional ``ShapePatchProvider`` callback (PR-7's Composer).
      - an optional ``LeafRebinder`` callback (PR-6's Selectors).

    Then called as ``assemble(shape, leaves)`` per request.  Returns
    an ``AssemblyResult`` — never raises (refusal is structured).

    Determinism: ``assemble`` is pure with respect to its inputs +
    the callbacks' return values.  The Assembler itself does not call
    any LLM; the callbacks do.  The trace records every mutation in
    application order.
    """

    def __init__(
        self,
        primitive_resolver: PrimitiveResolver,
        shape_patch_provider: Optional[ShapePatchProvider] = None,
        leaf_rebinder: Optional[LeafRebinder] = None,
    ) -> None:
        self._resolver = primitive_resolver
        self._patch_provider = shape_patch_provider
        self._rebinder = leaf_rebinder

    # ------------------------------------------------------------------
    # PUBLIC
    # ------------------------------------------------------------------

    def assemble(
        self,
        shape: ShapeSpec,
        leaves: Sequence[BoundLeaf],
    ) -> AssemblyResult:
        """Assemble a ShapeSpec + BoundLeaf list into a CLEAN or
        REFUSED outcome.  See the module docstring for the full
        protocol."""
        trace: List[RepairStep] = []

        # ----- Pre-flight: index + filter leaves -----
        leaves_by_id, preflight = self._preflight_leaves(shape, leaves)
        if preflight is not None:
            return preflight

        # ----- First substitution + validation -----
        workflow_v1, sub_trace_v1 = self._substitute(shape, leaves_by_id)
        trace.extend(sub_trace_v1)

        result_v1 = self._full_validate(
            shape, leaves_by_id, workflow_v1,
        )

        if result_v1.is_clean:
            return AssemblyResult(
                status=AssemblyStatus.CLEAN,
                workflow=workflow_v1,
                validation_result=result_v1,
                repair_trace=tuple(trace),
            )

        # ----- Bounded one-round repair -----
        leaves_by_id_v2, patches, repair_trace_round, refusal_reasons = (
            self._gather_repairs(
                shape=shape,
                leaves_by_id=leaves_by_id,
                workflow=workflow_v1,
                result=result_v1,
            )
        )
        trace.extend(repair_trace_round)

        if refusal_reasons:
            return AssemblyResult(
                status=AssemblyStatus.REFUSED,
                workflow=None,
                validation_result=result_v1,
                repair_trace=tuple(trace),
                refusal_reasons=tuple(refusal_reasons),
            )

        # ----- Re-substitute, re-apply patches, re-validate -----
        workflow_v2, sub_trace_v2 = self._substitute(shape, leaves_by_id_v2)
        trace.extend(sub_trace_v2)

        for patch in patches:
            try:
                workflow_v2, patch_step = self._apply_patch(
                    workflow_v2, patch,
                )
            except ValueError as exc:
                # An inconsistent patch (e.g. referencing a non-existent
                # edge) is the Composer's bug, not a substrate crash.
                # Refusal is the contract per
                # tmp/orchestration.md §PR-4 ('Assembler.assemble(...)
                # returns AssemblyResult(status=CLEAN|REFUSED)').  No
                # exception escapes assemble().
                return AssemblyResult(
                    status=AssemblyStatus.REFUSED,
                    workflow=None,
                    validation_result=result_v1,
                    repair_trace=tuple(trace),
                    refusal_reasons=(
                        f"Composer patch failed to apply: {exc}",
                    ),
                )
            trace.append(patch_step)

        result_v2 = self._full_validate(
            shape, leaves_by_id_v2, workflow_v2,
        )

        if result_v2.is_clean:
            return AssemblyResult(
                status=AssemblyStatus.CLEAN,
                workflow=workflow_v2,
                validation_result=result_v2,
                repair_trace=tuple(trace),
            )

        return AssemblyResult(
            status=AssemblyStatus.REFUSED,
            workflow=None,
            validation_result=result_v2,
            repair_trace=tuple(trace),
            refusal_reasons=(
                "bounded one-round repair exhausted; residual hard errors "
                "present after re-validation.",
            ),
        )

    # ------------------------------------------------------------------
    # INTERNAL — PRE-FLIGHT
    # ------------------------------------------------------------------

    def _preflight_leaves(
        self,
        shape: ShapeSpec,
        leaves: Sequence[BoundLeaf],
    ) -> Tuple[Dict[str, BoundLeaf], Optional[AssemblyResult]]:
        """Index leaves by leaf_id.  Refuse early on three failure modes:
          1. Duplicate leaf_ids (caller bug).
          2. Selector-side refusals (one or more BoundLeafs carry
             refusal=non-None).
          3. Holes with no matching BoundLeaf (caller bug — Selectors
             didn't deliver everything L3 asked for).
        """
        # Duplicate leaf_id check.
        leaf_ids_seen: Dict[str, BoundLeaf] = {}
        for leaf in leaves:
            if leaf.leaf_id in leaf_ids_seen:
                err = ValidationError(
                    code=ErrorCode.E_PRIMITIVE_RESOLVE_FAIL,
                    owner_layer=OwnerLayer.ASSEMBLER,
                    severity=Severity.ERROR,
                    message=(
                        f"Assembler: duplicate BoundLeaf for leaf_id="
                        f"{leaf.leaf_id!r}.  Caller MUST supply at most "
                        "one BoundLeaf per LeafHole."
                    ),
                    leaf_id=leaf.leaf_id,
                )
                return {}, AssemblyResult(
                    status=AssemblyStatus.REFUSED,
                    validation_result=ValidationResult(
                        workflow_id=shape.workflow_id, errors=(err,),
                    ),
                    refusal_reasons=("duplicate BoundLeaf leaf_id",),
                )
            leaf_ids_seen[leaf.leaf_id] = leaf

        # Selector refusals — early termination.
        refusals = [l for l in leaves if l.is_refusal]
        if refusals:
            errs = tuple(
                ValidationError(
                    code=ErrorCode.E_PRIMITIVE_RESOLVE_FAIL,
                    owner_layer=OwnerLayer.L2_BINDING,
                    severity=Severity.ERROR,
                    message=(
                        f"Assembler: Selector refused leaf "
                        f"{l.leaf_id!r}: {l.refusal}"
                    ),
                    leaf_id=l.leaf_id,
                )
                for l in refusals
            )
            return {}, AssemblyResult(
                status=AssemblyStatus.REFUSED,
                validation_result=ValidationResult(
                    workflow_id=shape.workflow_id, errors=errs,
                ),
                refusal_reasons=tuple(l.refusal or "" for l in refusals),
            )

        # Coverage check — every LeafHole has a BoundLeaf.
        hole_ids = set(shape.leaf_hole_ids())
        missing = hole_ids - set(leaf_ids_seen.keys())
        extra = set(leaf_ids_seen.keys()) - hole_ids
        if missing or extra:
            errs_list = []
            for mid in sorted(missing):
                errs_list.append(ValidationError(
                    code=ErrorCode.E_PRIMITIVE_RESOLVE_FAIL,
                    owner_layer=OwnerLayer.ASSEMBLER,
                    severity=Severity.ERROR,
                    message=(
                        f"Assembler: LeafHole {mid!r} has no matching "
                        "BoundLeaf.  Every hole must be bound (or "
                        "refused) before assembly."
                    ),
                    leaf_id=mid,
                ))
            for xid in sorted(extra):
                errs_list.append(ValidationError(
                    code=ErrorCode.E_PRIMITIVE_RESOLVE_FAIL,
                    owner_layer=OwnerLayer.ASSEMBLER,
                    severity=Severity.ERROR,
                    message=(
                        f"Assembler: BoundLeaf leaf_id={xid!r} does not "
                        "match any LeafHole in the shape."
                    ),
                    leaf_id=xid,
                ))
            return {}, AssemblyResult(
                status=AssemblyStatus.REFUSED,
                validation_result=ValidationResult(
                    workflow_id=shape.workflow_id,
                    errors=tuple(errs_list),
                ),
                refusal_reasons=(
                    "leaf coverage mismatch (missing or extra BoundLeafs)",
                ),
            )

        return leaf_ids_seen, None

    # ------------------------------------------------------------------
    # INTERNAL — SUBSTITUTION
    # ------------------------------------------------------------------

    def _substitute(
        self,
        shape: ShapeSpec,
        leaves_by_id: Dict[str, BoundLeaf],
    ) -> Tuple[Workflow, List[RepairStep]]:
        """Replace every LeafHole with a PrimitiveNode whose
        ``tool_name`` is the matching BoundLeaf's ``resolver_tool_key``.
        OperatorNodes carry over unchanged.  Edges and literal bindings
        carry over unchanged (node IDs are stable across the
        substitution).

        Trace: appends one SUBSTITUTE_LEAF step per hole.
        """
        substituted_nodes: List[WorkflowNode] = []
        trace: List[RepairStep] = []
        for node in shape.nodes:
            if isinstance(node, LeafHole):
                bound = leaves_by_id[node.node_id]
                # Refusals were filtered in preflight, so bound is a
                # real binding here.
                prim = PrimitiveNode(
                    node_id=node.node_id,
                    tool_name=bound.resolver_tool_key,
                    output_field=bound.output_field,
                    params=dict(bound.params),
                )
                substituted_nodes.append(prim)
                trace.append(RepairStep(
                    kind=RepairKind.SUBSTITUTE_LEAF,
                    rationale=(
                        f"Substituted LeafHole {node.node_id!r} with "
                        f"PrimitiveNode resolving to "
                        f"{bound.resolver_tool_key!r} (mcp tool: "
                        f"{bound.mcp_tool_name!r}, domain: "
                        f"{bound.domain!r})."
                    ),
                    leaf_id=node.node_id,
                    node_id=node.node_id,
                    detail={
                        "resolver_tool_key": bound.resolver_tool_key,
                        "mcp_tool_name": bound.mcp_tool_name,
                        "domain": bound.domain,
                        "output_field": bound.output_field,
                    },
                ))
            else:
                # OperatorNode carries over verbatim (frozen — same instance OK).
                substituted_nodes.append(node)

        return Workflow(
            workflow_id=shape.workflow_id,
            nodes=substituted_nodes,
            edges=list(shape.edges),
            literal_bindings=list(shape.literal_bindings),
            terminal_node_id=shape.terminal_node_id,
        ), trace

    # ------------------------------------------------------------------
    # INTERNAL — CONTRACT CHECK (PR-4 Boundary A inner)
    # ------------------------------------------------------------------

    def _contract_check(
        self,
        shape: ShapeSpec,
        leaves_by_id: Dict[str, BoundLeaf],
    ) -> List[ValidationError]:
        """Per-leaf LeafRequest vs BoundLeaf consistency check.

        HARD (severity=ERROR, owner_layer=L2_BINDING):
          - E_TYPE_MISMATCH: required_artifact_type ≠
            declared_output_artifact_type.
          - E_UNIT_MISMATCH: expected_units set AND ≠ declared_units.
          - E_FREQUENCY_MISMATCH: expected_frequency set AND ≠
            declared_frequency (case-insensitive, whitespace-trimmed).

        SOFT (severity=WARNING, owner_layer=L2_BINDING):
          - E_ROLE_DISCRIMINANT_MISMATCH: semantic_role or
            requested_output_meaning differ under normalised
            comparison.
        """
        out: List[ValidationError] = []
        for hole in shape.leaf_holes():
            bound = leaves_by_id.get(hole.node_id)
            if bound is None or bound.is_refusal:
                continue  # filtered in preflight
            req = hole.leaf_request

            # 1. Artifact-type — HARD.
            if req.required_artifact_type != bound.declared_output_artifact_type:
                out.append(ValidationError(
                    code=ErrorCode.E_TYPE_MISMATCH,
                    owner_layer=OwnerLayer.L2_BINDING,
                    severity=Severity.ERROR,
                    message=(
                        f"Assembler contract check: leaf "
                        f"{hole.node_id!r} LeafRequest expected "
                        f"artifact type "
                        f"{req.required_artifact_type.value!r}, but "
                        f"the bound primitive "
                        f"{bound.mcp_tool_name!r} declares "
                        f"{bound.declared_output_artifact_type.value!r}."
                    ),
                    leaf_id=hole.node_id,
                    node_id=hole.node_id,
                    tool_name=bound.mcp_tool_name,
                    detail={
                        "field": "artifact_type",
                        "expected": req.required_artifact_type.value,
                        "declared": bound.declared_output_artifact_type.value,
                    },
                ))

            # 2. Units — HARD (only when LeafRequest pinned a unit).
            if (
                req.expected_units is not None
                and bound.declared_units != req.expected_units
            ):
                out.append(ValidationError(
                    code=ErrorCode.E_UNIT_MISMATCH,
                    owner_layer=OwnerLayer.L2_BINDING,
                    severity=Severity.ERROR,
                    message=(
                        f"Assembler contract check: leaf "
                        f"{hole.node_id!r} LeafRequest expected units "
                        f"{req.expected_units.value!r}, but the bound "
                        f"primitive {bound.mcp_tool_name!r} declares "
                        f"{bound.declared_units.value if bound.declared_units else None!r}."
                    ),
                    leaf_id=hole.node_id,
                    node_id=hole.node_id,
                    tool_name=bound.mcp_tool_name,
                    detail={
                        "field": "units",
                        "expected": req.expected_units.value,
                        "declared": (
                            bound.declared_units.value
                            if bound.declared_units else None
                        ),
                    },
                ))

            # 3. Frequency — HARD (only when LeafRequest pinned a freq).
            # Frequency is a closed Frequency enum (PR-A3 corrective);
            # comparison is direct enum equality, no normalisation needed.
            if (
                req.expected_frequency is not None
                and bound.declared_frequency != req.expected_frequency
            ):
                out.append(ValidationError(
                    code=ErrorCode.E_FREQUENCY_MISMATCH,
                    owner_layer=OwnerLayer.L2_BINDING,
                    severity=Severity.ERROR,
                    message=(
                        f"Assembler contract check: leaf "
                        f"{hole.node_id!r} LeafRequest expected "
                        f"frequency {req.expected_frequency.value!r}, "
                        f"but the bound primitive "
                        f"{bound.mcp_tool_name!r} declares "
                        f"{bound.declared_frequency.value if bound.declared_frequency else None!r}."
                    ),
                    leaf_id=hole.node_id,
                    node_id=hole.node_id,
                    tool_name=bound.mcp_tool_name,
                    detail={
                        "field": "frequency",
                        "expected": req.expected_frequency.value,
                        "declared": (
                            bound.declared_frequency.value
                            if bound.declared_frequency else None
                        ),
                    },
                ))

            # 4. Free-form fields — SOFT warnings.
            if _normalise(req.semantic_role) != _normalise(
                bound.declared_semantic_role,
            ):
                out.append(ValidationError(
                    code=ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH,
                    owner_layer=OwnerLayer.L2_BINDING,
                    severity=Severity.WARNING,
                    message=(
                        f"Assembler contract check: leaf "
                        f"{hole.node_id!r} semantic_role mismatch "
                        f"(LeafRequest: "
                        f"{req.semantic_role!r}; BoundLeaf: "
                        f"{bound.declared_semantic_role!r}).  SOFT "
                        "warning — Boundary B receives this as "
                        "supplementary evidence."
                    ),
                    leaf_id=hole.node_id,
                    node_id=hole.node_id,
                    tool_name=bound.mcp_tool_name,
                    detail={
                        "field": "semantic_role",
                        "requested": req.semantic_role,
                        "declared": bound.declared_semantic_role,
                    },
                ))
            if _normalise(req.requested_output_meaning) != _normalise(
                bound.declared_output_meaning,
            ):
                out.append(ValidationError(
                    code=ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH,
                    owner_layer=OwnerLayer.L2_BINDING,
                    severity=Severity.WARNING,
                    message=(
                        f"Assembler contract check: leaf "
                        f"{hole.node_id!r} requested_output_meaning "
                        "mismatch.  SOFT warning — Boundary B "
                        "receives this as supplementary evidence."
                    ),
                    leaf_id=hole.node_id,
                    node_id=hole.node_id,
                    tool_name=bound.mcp_tool_name,
                    detail={
                        "field": "requested_output_meaning",
                        "requested": req.requested_output_meaning,
                        "declared": bound.declared_output_meaning,
                    },
                ))

        return out

    def _full_validate(
        self,
        shape: ShapeSpec,
        leaves_by_id: Dict[str, BoundLeaf],
        workflow: Workflow,
    ) -> ValidationResult:
        """Run both validation layers and combine into one
        ValidationResult.  PR-4's contract check first, then PR-1's
        structural validator."""
        contract_errors = self._contract_check(shape, leaves_by_id)
        structural = validate_workflow_result(
            workflow, primitive_resolver=self._resolver,
        )
        combined = tuple(contract_errors) + tuple(structural.errors)
        return ValidationResult(
            workflow_id=workflow.workflow_id, errors=combined,
        )

    # ------------------------------------------------------------------
    # INTERNAL — REPAIR DISPATCH
    # ------------------------------------------------------------------

    def _gather_repairs(
        self,
        shape: ShapeSpec,
        leaves_by_id: Dict[str, BoundLeaf],
        workflow: Workflow,
        result: ValidationResult,
    ) -> Tuple[
        Dict[str, BoundLeaf],
        List[ShapePatch],
        List[RepairStep],
        List[str],
    ]:
        """One repair round.  Dispatch errors by owner_layer:

          - L2_BINDING errors on a specific leaf → rebinder.
          - L3_WIRING errors → patch provider.
          - ASSEMBLER errors → no LLM round-trip; if present here it
            means the preflight missed something; refuse.

        Returns the updated leaves_by_id (with rebound leaves replacing
        the originals), the patch sequence to apply post-substitution,
        the per-mutation trace, and a list of refusal reasons (empty
        when repair can proceed)."""
        new_leaves_by_id = dict(leaves_by_id)
        patches: List[ShapePatch] = []
        repair_trace: List[RepairStep] = []
        refusals: List[str] = []

        hard = result.hard_errors

        # ASSEMBLER errors → cannot self-heal at this stage.
        assembler_errors = [
            e for e in hard if e.owner_layer == OwnerLayer.ASSEMBLER
        ]
        if assembler_errors:
            refusals.append(
                "Assembler-owned errors surfaced post-substitution and "
                "cannot self-heal within the bounded repair round."
            )
            return new_leaves_by_id, patches, repair_trace, refusals

        # L2_BINDING errors → rebinder, dispatched per leaf.
        l2_errors = [
            e for e in hard if e.owner_layer == OwnerLayer.L2_BINDING
        ]
        if l2_errors:
            if self._rebinder is None:
                refusals.append(
                    "L2_BINDING hard errors present but no LeafRebinder "
                    "callback was supplied to the Assembler."
                )
                return new_leaves_by_id, patches, repair_trace, refusals

            # Group by leaf_id.  Errors with no leaf_id are routed to
            # ANY leaf they implicate — fall back: if the error has
            # ``tool_name`` matching a bound leaf, use that leaf;
            # otherwise drop into a generic 'unattributed' bucket
            # that fails the repair.
            errors_by_leaf: Dict[str, List[ValidationError]] = {}
            unattributed: List[ValidationError] = []
            for e in l2_errors:
                if e.leaf_id and e.leaf_id in new_leaves_by_id:
                    errors_by_leaf.setdefault(e.leaf_id, []).append(e)
                elif e.tool_name:
                    matched = False
                    for lid, leaf in new_leaves_by_id.items():
                        if (
                            leaf.mcp_tool_name == e.tool_name
                            or leaf.resolver_tool_key == e.tool_name
                        ):
                            errors_by_leaf.setdefault(lid, []).append(e)
                            matched = True
                            break
                    if not matched:
                        unattributed.append(e)
                else:
                    unattributed.append(e)

            if unattributed:
                refusals.append(
                    f"L2_BINDING errors without a routable leaf_id or "
                    f"tool_name: "
                    f"{[e.code.value for e in unattributed]}."
                )
                return new_leaves_by_id, patches, repair_trace, refusals

            for leaf_id, errs in errors_by_leaf.items():
                hole = self._hole_for(shape, leaf_id)
                if hole is None:
                    # Shouldn't happen — preflight ensured coverage.
                    refusals.append(
                        f"L2_BINDING repair: leaf {leaf_id!r} not in "
                        "shape."
                    )
                    return new_leaves_by_id, patches, repair_trace, refusals
                current = new_leaves_by_id[leaf_id]
                try:
                    rebound = self._rebinder(
                        leaf_request=hole.leaf_request,
                        current_bound=current,
                        errors=tuple(errs),
                    )
                except Exception as exc:
                    refusals.append(
                        f"LeafRebinder raised on leaf {leaf_id!r}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    return new_leaves_by_id, patches, repair_trace, refusals

                if rebound.leaf_id != leaf_id:
                    refusals.append(
                        f"LeafRebinder returned BoundLeaf with leaf_id="
                        f"{rebound.leaf_id!r} for request on "
                        f"{leaf_id!r}; rebinder must echo the leaf_id."
                    )
                    return new_leaves_by_id, patches, repair_trace, refusals

                if rebound.is_refusal:
                    refusals.append(
                        f"LeafRebinder refused leaf {leaf_id!r}: "
                        f"{rebound.refusal}"
                    )
                    return new_leaves_by_id, patches, repair_trace, refusals

                # No-primitive-swap discipline.  Per plan
                # tmp/orchestration.md §PR-4: the rebinder may change
                # params (and output_field) but MUST NOT pick a
                # different primitive for the same hole — that's
                # "re-bind, then re-shape, then re-bind" → oscillation.
                # Enforce by asserting identity on the three
                # primitive-identity fields.
                if (
                    rebound.domain != current.domain
                    or rebound.mcp_tool_name != current.mcp_tool_name
                    or rebound.resolver_tool_key != current.resolver_tool_key
                ):
                    refusals.append(
                        f"LeafRebinder attempted a primitive swap on "
                        f"leaf {leaf_id!r}: "
                        f"(domain, mcp_tool_name, resolver_tool_key) "
                        f"went from ({current.domain!r}, "
                        f"{current.mcp_tool_name!r}, "
                        f"{current.resolver_tool_key!r}) to "
                        f"({rebound.domain!r}, "
                        f"{rebound.mcp_tool_name!r}, "
                        f"{rebound.resolver_tool_key!r}).  Repair may "
                        "change params/output_field/declared_* but "
                        "NOT the primitive itself; re-picking is a "
                        "shape concern that requires a new Composer "
                        "round, not a Selector retry."
                    )
                    return new_leaves_by_id, patches, repair_trace, refusals

                new_leaves_by_id[leaf_id] = rebound
                repair_trace.append(RepairStep(
                    kind=RepairKind.REBIND_LEAF,
                    rationale=(
                        f"Rebound leaf {leaf_id!r} after L2_BINDING "
                        f"errors {[e.code.value for e in errs]}.  "
                        "Primitive identity preserved (no-swap "
                        "discipline); params / declared_* updated by "
                        "the Selector."
                    ),
                    leaf_id=leaf_id,
                    node_id=leaf_id,
                    detail={
                        "error_codes": [e.code.value for e in errs],
                        "resolver_tool_key": current.resolver_tool_key,
                        "previous_params": dict(current.params),
                        "new_params": dict(rebound.params),
                        "previous_output_field": current.output_field,
                        "new_output_field": rebound.output_field,
                    },
                ))

        # L3_WIRING errors → patch provider.
        l3_errors = [
            e for e in hard if e.owner_layer == OwnerLayer.L3_WIRING
        ]
        if l3_errors:
            if self._patch_provider is None:
                refusals.append(
                    "L3_WIRING hard errors present but no "
                    "ShapePatchProvider callback was supplied to the "
                    "Assembler."
                )
                return new_leaves_by_id, patches, repair_trace, refusals

            try:
                provider_patches = self._patch_provider(
                    workflow=workflow,
                    errors=tuple(l3_errors),
                )
            except Exception as exc:
                refusals.append(
                    f"ShapePatchProvider raised: "
                    f"{type(exc).__name__}: {exc}"
                )
                return new_leaves_by_id, patches, repair_trace, refusals

            patches.extend(provider_patches)
            if not provider_patches:
                refusals.append(
                    "ShapePatchProvider returned an empty patch list "
                    "for L3_WIRING errors; no repair possible."
                )
                return new_leaves_by_id, patches, repair_trace, refusals

        return new_leaves_by_id, patches, repair_trace, refusals

    @staticmethod
    def _hole_for(
        shape: ShapeSpec, leaf_id: str,
    ) -> Optional[LeafHole]:
        for hole in shape.leaf_holes():
            if hole.node_id == leaf_id:
                return hole
        return None

    # ------------------------------------------------------------------
    # INTERNAL — PATCH APPLICATION
    # ------------------------------------------------------------------

    def _apply_patch(
        self,
        workflow: Workflow,
        patch: ShapePatch,
    ) -> Tuple[Workflow, RepairStep]:
        """Apply one additive patch and return the new Workflow + a
        trace entry.  Workflow is frozen Pydantic, so we build a fresh
        instance from updated lists."""
        if isinstance(patch, InsertAdapterNode):
            return self._apply_insert_adapter(workflow, patch)
        if isinstance(patch, RewireEdge):
            return self._apply_rewire_edge(workflow, patch)
        # Closed-family discriminator forbids anything else; defensive.
        raise TypeError(
            f"Assembler._apply_patch: unknown patch type {type(patch).__name__}"
        )

    def _apply_insert_adapter(
        self,
        workflow: Workflow,
        patch: InsertAdapterNode,
    ) -> Tuple[Workflow, RepairStep]:
        # Identify the original edge.
        new_edges: List[WorkflowEdge] = []
        matched = False
        for edge in workflow.edges:
            is_match = (
                edge.source_node_id == patch.on_edge_source
                and edge.target_node_id == patch.on_edge_target
                and edge.target_input_slot == patch.on_edge_slot
            )
            if is_match and not matched:
                matched = True
                # Replace with two new edges via the adapter.
                new_edges.append(WorkflowEdge(
                    source_node_id=patch.on_edge_source,
                    target_node_id=patch.adapter_node_id,
                    target_input_slot=patch.adapter_input_slot,
                ))
                new_edges.append(WorkflowEdge(
                    source_node_id=patch.adapter_node_id,
                    target_node_id=patch.on_edge_target,
                    target_input_slot=patch.on_edge_slot,
                ))
            else:
                new_edges.append(edge)

        if not matched:
            raise ValueError(
                f"InsertAdapterNode references edge "
                f"({patch.on_edge_source!r} -> "
                f"{patch.on_edge_target!r} on slot "
                f"{patch.on_edge_slot!r}) that does not exist in the "
                "workflow.  The Composer's patch is inconsistent with "
                "the assembled shape."
            )

        # Append the adapter node.
        adapter_node = OperatorNode(
            node_id=patch.adapter_node_id,
            operator_name=patch.adapter_operator_name,
            params=dict(patch.adapter_params),
        )
        new_nodes = list(workflow.nodes) + [adapter_node]

        # Append any new literal bindings the patch declares.
        new_literals = list(workflow.literal_bindings) + list(
            patch.adapter_literal_bindings,
        )

        new_workflow = Workflow(
            workflow_id=workflow.workflow_id,
            nodes=new_nodes,
            edges=new_edges,
            literal_bindings=new_literals,
            terminal_node_id=workflow.terminal_node_id,
        )
        step = RepairStep(
            kind=RepairKind.INSERT_ADAPTER_NODE,
            rationale=(
                f"Inserted adapter {patch.adapter_operator_name!r} "
                f"(node_id={patch.adapter_node_id!r}) on edge "
                f"{patch.on_edge_source!r} -> "
                f"{patch.on_edge_target!r} (slot "
                f"{patch.on_edge_slot!r})."
            ),
            node_id=patch.adapter_node_id,
            edge=(
                patch.on_edge_source,
                patch.on_edge_target,
                patch.on_edge_slot,
            ),
            detail={
                "adapter_operator_name": patch.adapter_operator_name,
                "adapter_input_slot": patch.adapter_input_slot,
                "adapter_params": dict(patch.adapter_params),
            },
        )
        return new_workflow, step

    def _apply_rewire_edge(
        self,
        workflow: Workflow,
        patch: RewireEdge,
    ) -> Tuple[Workflow, RepairStep]:
        new_edges: List[WorkflowEdge] = []
        matched = False
        for edge in workflow.edges:
            is_match = (
                edge.source_node_id == patch.source_node_id
                and edge.target_node_id == patch.target_node_id
                and edge.target_input_slot == patch.current_target_input_slot
            )
            if is_match and not matched:
                matched = True
                new_edges.append(WorkflowEdge(
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    target_input_slot=patch.new_target_input_slot,
                ))
            else:
                new_edges.append(edge)

        if not matched:
            raise ValueError(
                f"RewireEdge references edge ({patch.source_node_id!r} "
                f"-> {patch.target_node_id!r} on slot "
                f"{patch.current_target_input_slot!r}) that does not "
                "exist in the workflow."
            )

        new_workflow = Workflow(
            workflow_id=workflow.workflow_id,
            nodes=list(workflow.nodes),
            edges=new_edges,
            literal_bindings=list(workflow.literal_bindings),
            terminal_node_id=workflow.terminal_node_id,
        )
        step = RepairStep(
            kind=RepairKind.REWIRE_EDGE,
            rationale=(
                f"Rewired edge {patch.source_node_id!r} -> "
                f"{patch.target_node_id!r} from slot "
                f"{patch.current_target_input_slot!r} to "
                f"{patch.new_target_input_slot!r}."
            ),
            edge=(
                patch.source_node_id,
                patch.target_node_id,
                patch.current_target_input_slot,
            ),
            detail={
                "new_target_input_slot": patch.new_target_input_slot,
            },
        )
        return new_workflow, step


# ============================================================================
# HELPERS
# ============================================================================


def _normalise(s: str) -> str:
    """Normalise a free-form English string for SOFT comparison:
    lowercase, strip, collapse internal whitespace.  Used by the
    contract check on semantic_role / requested_output_meaning."""
    return " ".join(s.lower().split())


__all__ = [
    "AssemblyStatus",
    "RepairKind",
    "RepairStep",
    "InsertAdapterNode",
    "RewireEdge",
    "ShapePatch",
    "ShapePatchProvider",
    "LeafRebinder",
    "AssemblyResult",
    "Assembler",
]

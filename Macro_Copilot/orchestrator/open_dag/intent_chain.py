"""orchestrator.open_dag.intent_chain — PR-9 of the open-DAG PoC.

The **intent chain**: a typed record of what the open-DAG pipeline
*understood and built*, captured at each LLM-driven boundary.

Per ``tmp/orchestration.md`` §PR-9:

  > Extend lineage to record the full intent chain alongside the
  > compute chain.  L6 answer template: intent echo → number →
  > per-leaf provenance footer.

The substrate's ``Lineage`` (in ``shared/artifacts/lineage.py``) is
the **compute chain** — content-addressed steps the executor recorded
as the DAG ran.  The intent chain is its complement: a record of what
the LLM layers *believed* they were producing.  The two together give
L6 both the "what was computed" and the "what was intended"
half-truth — and R9's discipline: **intent echo protects correctness,
the lineage hash protects reproducibility**.

Four sub-records (one per LLM-driven boundary)
==============================================

  1. ``RouterIntentRecord`` — L1's structured output.  IntentTag
     + decomposition (list of EconomicQuantity) + the supervisor's
     adjustments tape (so under-scoped routing surfaces in the
     intent echo).
  2. ``SelectorIntentRecord`` (one per LeafHole) — per-leaf the L2
     Selector's declared semantic_role + output_meaning +
     fit_confidence; the BoundLeaf already carries these as the
     subset of its declaration that's NOT a primitive identity, so
     the intent record reuses them (primitive-blind).
  3. ``ComposerIntentRecord`` — L3's shape summary: terminal
     operator family, operator name list, refusal-if-any.  No
     LLM-authored prose surface — what the Composer *did* is
     captured structurally.
  4. ``GateIntentRecord`` — L4.5 Boundary B's verdict: status +
     reason + clarification_question + soft_warnings.

Construction
============

Two paths:
  - ``IntentChain.from_inputs(...)`` — typed constructor that takes
    the four substrate inputs (RouteDecision, BoundLeaf list,
    ShapeSpec OR Workflow, GateVerdict) and builds the four
    sub-records.  Used by PR-10's orchestrator.
  - Direct ``IntentChain(...)`` — Pydantic ctor.  Used by tests +
    by callers who already have the sub-records.

Refusal at any boundary
=======================

A refused Composer or REFUSE/CLARIFY GateVerdict still yields a valid
IntentChain.  The L6 layer reads it and renders the gate's
clarification or refusal message INSTEAD of an answer (per §PR-9:
"No PR-9 answer is generated for a refused/clarified gate verdict —
the gate's clarification message goes to the user instead.").

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/``.  No imports from
``rates_agent/``.  Sub-records carry closed-substrate values
(IntentTag enum, GateStatus literal, ArtifactTypeName enum) + the
LLM-authored free-form English (semantic_role, output_meaning,
gate verdict reason) — never a primitive's tool_name.  The
``SelectorIntentRecord.bound_tool_name`` field IS captured because
the L6 answer's provenance footer explicitly cites primitives by
name per §PR-9 — that's the documented exception, scoped to the
footer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.contracts import (
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag.contracts import BoundLeaf, ShapeSpec
from orchestrator.open_dag.coverage_gate import GateVerdict
from shared.workflow.types import OperatorNode, PrimitiveNode, Workflow


# ============================================================================
# ROUTER INTENT (L1)
# ============================================================================


class RouterIntentRecord(BaseModel):
    """L1's structured output, captured for the intent echo.

    Carries the closed-family intent_tag + the L1 decomposition +
    the supervisor's normaliser adjustments.  The adjustments tape
    is critical: when L1 dropped a domain, the supervisor's adjustment
    explains "decomposition implies X but routing domains are [...] —
    Possible under-scoped routing", and that note flows through to
    the L6 echo so the user sees what was understood.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: RouteAction
    intent_tag: Optional[IntentTag] = Field(
        default=None,
        description=(
            "Populated for non-clarify actions; None when L1 routed "
            "CLARIFY (intent is unknown until the user disambiguates)."
        ),
    )
    decomposition: Tuple[EconomicQuantity, ...] = Field(
        default_factory=tuple,
        description=(
            "L1's decomposition as a frozen tuple (Pydantic List would "
            "be mutable).  Order preserved from RouteDecision."
        ),
    )
    adjustments: Tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Supervisor's post-normaliser notes — typically "
            "under-scoped-routing flags.  Surfaced in the intent echo."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "Supervisor's one-sentence rationale for the routing "
            "choice (echoed from RouteDecision.rationale).  May be "
            "empty when the supervisor declined to provide one."
        ),
    )


# ============================================================================
# SELECTOR INTENT (L2, one per leaf)
# ============================================================================


class SelectorIntentRecord(BaseModel):
    """L2's per-leaf intent record.

    The L6 provenance footer cites primitives by name (the documented
    exception to the L3-primitive-blindness discipline — the user
    needs to see what tool produced what number).  So this record
    DOES carry ``bound_tool_name``.  Everything else is the
    selector's free-form English + closed-substrate declarations.

    For a refused leaf (BoundLeaf.refusal is non-None), the intent
    record sets ``refusal`` and leaves the bound fields empty —
    mirrors BoundLeaf's two-mode contract.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    leaf_id: str = Field(..., min_length=1)
    domain: str = Field(..., min_length=1)

    # Bound-mode fields (meaningful when refusal is None).
    bound_tool_name: str = Field(
        default="",
        description=(
            "The MCP tool the selector bound.  Surfaced ONLY in the "
            "L6 provenance footer (R9: traders need to see what tool "
            "produced what number).  Empty allowed when refusal is set."
        ),
    )
    declared_semantic_role: str = Field(
        default="",
        description=(
            "Selector's free-form tag for the bound primitive's role; "
            "feeds the intent echo's 'Pulled' bullet."
        ),
    )
    declared_output_meaning: str = Field(
        default="",
        description=(
            "Selector's one-sentence English description of the bound "
            "primitive's output; the intent echo's PM-readable hook."
        ),
    )
    bound_params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The primitive's input params — surfaced in the L6 footer "
            "for full audit.  Captured verbatim from BoundLeaf.params."
        ),
    )
    fit_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Selector's self-assessment in [0, 1].  Low confidence on "
            "a successful bind surfaces in the intent echo as a "
            "softer claim."
        ),
    )
    refusal: Optional[str] = Field(
        default=None,
        description=(
            "Populated when the selector declined to bind any tool. "
            "When non-None, the bound_* fields are empty by contract."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "PR-9A Codex F2: the selector's lingo-resolution rationale "
            "in one English sentence.  Per the plan §PR-9 the intent "
            "chain MUST record selector rationales alongside the "
            "structural fields.  In V1 this is DERIVED deterministically "
            "from declared_semantic_role + declared_output_meaning + "
            "fit_confidence (or the refusal string in refusal mode) — "
            "see ``_derive_selector_rationale``.  A future PR-6 "
            "extension could add a free-form LLM-authored "
            "``rationale`` field to ``SelectorLLMOutput`` and populate "
            "this directly from the LLM; the IntentChain slot is here "
            "to consume it when it arrives.  Tests assert this is "
            "non-empty for both bound and refused selectors."
        ),
    )

    @property
    def is_refusal(self) -> bool:
        return self.refusal is not None


# ============================================================================
# COMPOSER INTENT (L3)
# ============================================================================


class ComposerIntentRecord(BaseModel):
    """L3's shape summary — captured structurally, not as LLM prose.

    Two modes:
      - ``refusal is None``: the Composer emitted a ShapeSpec.  The
        record carries the terminal operator name + the list of
        operator names in TOPOLOGICAL EXECUTION ORDER (deterministic
        Kahn walk, ties broken by node_id — byte-stable, and the L6
        "Wired:" line reads in the order the DAG executes).
      - ``refusal is set``: the Composer declined.  ``operator_names``
        is empty + ``terminal_operator_name`` is empty.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = Field(
        default="",
        description=(
            "ShapeSpec.workflow_id when bound; empty on refusal."
        ),
    )
    terminal_operator_name: str = Field(
        default="",
        description=(
            "The operator at the shape's terminal node (e.g. "
            "'correlation', 'rolling_zscore').  Empty when the "
            "shape's terminal is a leaf (degenerate 1-leaf lookup) "
            "or when the Composer refused."
        ),
    )
    terminal_artifact_type: str = Field(
        default="",
        description=(
            "Closed-family artifact type the terminal emits (e.g. "
            "'ScalarMetric', 'Series').  Empty on refusal."
        ),
    )
    operator_names: Tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Every operator name in the shape, in topological "
            "execution order (deterministic; ties broken by node_id). "
            "Empty on refusal."
        ),
    )
    refusal: Optional[str] = Field(
        default=None,
        description=(
            "Populated when the Composer declined (no operator chain "
            "fits the prompt).  When non-None, the structural fields "
            "are empty by contract."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "PR-9A Codex F2: the composer's wiring rationale in one "
            "English sentence.  Per the plan §PR-9 the intent chain "
            "MUST record the composer's wiring rationale alongside "
            "the structural fields.  In V1 this is DERIVED "
            "deterministically from operator_names + "
            "terminal_operator_name + terminal_artifact_type (or the "
            "refusal string in refusal mode) — see "
            "``_derive_composer_rationale``.  A future PR-7 extension "
            "could add a free-form LLM-authored ``rationale`` field "
            "to ``ComposerLLMOutput`` and populate this directly; the "
            "IntentChain slot is here to consume it when it arrives. "
            "Tests assert this is non-empty for both bound and "
            "refused composers."
        ),
    )

    @property
    def is_refusal(self) -> bool:
        return self.refusal is not None


# ============================================================================
# GATE INTENT (L4.5)
# ============================================================================


class GateIntentRecord(BaseModel):
    """L4.5's verdict, captured for the intent chain.

    Carries every field of ``GateVerdict`` so the L6 answer renderer
    (or the gate's clarification path) has the full audit trail
    without re-fetching the AssemblyResult.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: str = Field(
        ...,
        min_length=1,
        description="One of 'PASS', 'REFUSE', 'CLARIFY'.",
    )
    reason: str = Field(..., min_length=1)
    clarification_question: Optional[str] = Field(default=None)
    soft_warnings: Tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _validate_clarification(self) -> "GateIntentRecord":
        if self.status == "CLARIFY":
            if (
                self.clarification_question is None
                or not self.clarification_question.strip()
            ):
                raise ValueError(
                    "GateIntentRecord(status=CLARIFY) requires a "
                    "non-empty clarification_question, mirroring the "
                    "GateVerdict invariant."
                )
        else:
            if self.clarification_question is not None:
                raise ValueError(
                    f"GateIntentRecord(status={self.status}) MUST NOT "
                    "carry clarification_question."
                )
        return self

    @property
    def is_pass(self) -> bool:
        return self.status == "PASS"


# ============================================================================
# INTENT CHAIN
# ============================================================================


class IntentChain(BaseModel):
    """The four-record intent chain captured across the LLM-driven
    boundaries.

    Frozen.  Round-trips through Pydantic + JSON for storage in
    lineage / replay scenarios.

    Invariants (model_validator-enforced):
      - All four sub-records are populated.
      - When ``gate.status != 'PASS'``, the chain is still well-formed
        but the L6 renderer routes to the gate's clarification /
        refusal message instead of producing an answer.

    Usage
    -----
    Construct via ``IntentChain.from_inputs(...)`` (typed factory) or
    directly via the Pydantic ctor.  Both paths are exercised in
    PR-9's tests.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_prompt: str = Field(
        ...,
        min_length=1,
        description=(
            "The original user prompt — pinned at the head of the "
            "chain so the L6 renderer can reproduce 'what was asked' "
            "verbatim without re-fetching."
        ),
    )
    router: RouterIntentRecord
    selectors: Tuple[SelectorIntentRecord, ...] = Field(default_factory=tuple)
    composer: ComposerIntentRecord
    gate: GateIntentRecord

    @classmethod
    def from_inputs(
        cls,
        *,
        user_prompt: str,
        route_decision: RouteDecision,
        bound_leaves: Tuple[BoundLeaf, ...] | List[BoundLeaf],
        shape_or_workflow: Union[ShapeSpec, Workflow, None],
        gate_verdict: GateVerdict,
        composer_refusal: Optional[str] = None,
        composer_llm_rationale: str = "",
    ) -> "IntentChain":
        """Build an IntentChain from the substrate inputs.

        Parameters
        ----------
        user_prompt :
            The original user message, captured verbatim.
        route_decision :
            L1's output.  Its ``decomposition`` + ``adjustments`` +
            ``intent_tag`` + ``action`` populate ``router``.
        bound_leaves :
            The L2 BoundLeaf list.  Each leaf yields one
            SelectorIntentRecord (refusal-mode propagates).
        shape_or_workflow :
            EITHER the L3 ShapeSpec (when not yet substituted) OR the
            post-substitution Workflow (when the Assembler has run).
            ``None`` indicates the Composer refused.  In all three
            cases the record's ``composer`` field is built
            consistently.
        gate_verdict :
            The L4.5 GateVerdict.  Its fields map 1:1 onto
            ``GateIntentRecord``.
        composer_refusal :
            Populated when ``shape_or_workflow is None`` —
            the ComposerRefusal.reason string.  Pydantic-validated
            non-empty when supplied.
        """
        # --- ROUTER ---
        router = RouterIntentRecord(
            action=route_decision.action,
            intent_tag=route_decision.intent_tag,
            decomposition=tuple(route_decision.decomposition),
            adjustments=tuple(route_decision.adjustments),
            rationale=route_decision.rationale,
        )

        # --- SELECTORS ---
        # PR-9A Codex F2: each record's ``rationale`` is derived
        # deterministically from the BoundLeaf surface so the plan's
        # "selector lingo-resolution rationales" slot is populated.
        selectors: List[SelectorIntentRecord] = []
        for leaf in bound_leaves:
            if leaf.is_refusal:
                selectors.append(SelectorIntentRecord(
                    leaf_id=leaf.leaf_id,
                    domain=leaf.domain,
                    fit_confidence=leaf.fit_confidence,
                    refusal=leaf.refusal,
                    rationale=_derive_selector_rationale(leaf),
                ))
            else:
                selectors.append(SelectorIntentRecord(
                    leaf_id=leaf.leaf_id,
                    domain=leaf.domain,
                    bound_tool_name=leaf.mcp_tool_name,
                    declared_semantic_role=leaf.declared_semantic_role,
                    declared_output_meaning=leaf.declared_output_meaning,
                    bound_params=dict(leaf.params),
                    fit_confidence=leaf.fit_confidence,
                    rationale=_derive_selector_rationale(leaf),
                ))

        # --- COMPOSER ---
        # PR-9A Codex F2: ``rationale`` derived from the wiring
        # structure so the plan's "composer wiring rationale" slot
        # is populated.
        if shape_or_workflow is None:
            # Composer refused.
            refusal_reason = composer_refusal or "Composer refused (no reason supplied)"
            composer = ComposerIntentRecord(
                refusal=refusal_reason,
                rationale=f"REFUSED: {refusal_reason}",
            )
        else:
            composer = _composer_record_from(
                shape_or_workflow,
                llm_authored_rationale=composer_llm_rationale,
            )

        # --- GATE ---
        gate = GateIntentRecord(
            status=gate_verdict.status,
            reason=gate_verdict.reason,
            clarification_question=gate_verdict.clarification_question,
            soft_warnings=tuple(gate_verdict.soft_warnings),
        )

        return cls(
            user_prompt=user_prompt,
            router=router,
            selectors=tuple(selectors),
            composer=composer,
            gate=gate,
        )

    @property
    def is_answerable(self) -> bool:
        """True iff the gate verdict is PASS AND no upstream layer
        refused.  When False, the L6 layer routes to the gate's
        clarification or refusal message (per §PR-9).
        """
        if not self.gate.is_pass:
            return False
        if self.composer.is_refusal:
            return False
        if any(s.is_refusal for s in self.selectors):
            return False
        return True


# ============================================================================
# HELPERS
# ============================================================================


def _derive_selector_rationale(leaf: BoundLeaf) -> str:
    """Selector lingo-resolution rationale string.

    PR-10B Codex F14: if the BoundLeaf carries an LLM-authored
    ``rationale`` field (populated when SelectorLLMOutput.rationale
    was non-empty), use it VERBATIM — preserving the actual
    LLM-authored rationale per plan §PR-9.  Otherwise fall back to
    the deterministic derivation introduced in PR-9A F2 (from
    declared_semantic_role + declared_output_meaning +
    fit_confidence).

    Format (fallback):
      - Bound: ``"<role> from <domain> (fit_confidence=<f>): <meaning>"``
      - Refusal: ``"REFUSED on <domain>: <reason>"``
    """
    llm_authored = getattr(leaf, "rationale", "") or ""
    if llm_authored.strip():
        return llm_authored.strip()
    if leaf.is_refusal:
        return (
            f"REFUSED on {leaf.domain}: {leaf.refusal}"
        )
    return (
        f"{leaf.declared_semantic_role} from {leaf.domain} "
        f"(fit_confidence={leaf.fit_confidence:.2f}): "
        f"{leaf.declared_output_meaning}"
    )


def _derive_composer_rationale(
    *,
    workflow_id: str,
    operator_names: Tuple[str, ...],
    terminal_operator_name: str,
    terminal_artifact_type: str,
) -> str:
    """Derive the V1 composer wiring rationale string.

    Per PR-9A Codex F2 + plan §PR-9 ("composer wiring rationale"):
    the ComposerIntentRecord MUST carry a rationale field.  PR-7's
    ComposerLLMOutput doesn't currently emit a free-form rationale
    (only the structural ShapeSpec), so V1 derives the rationale
    deterministically from the operator topology.  When a future
    PR-7 extension adds a free-form ``rationale`` to
    ComposerLLMOutput, this helper can be retired.

    Format:
      ``"Wired <workflow_id>: <op1> -> ... -> <opN>; terminal
        <terminal_op> -> <terminal_artifact>"``
    """
    if not operator_names:
        # Degenerate 1-leaf shape — terminal IS the leaf.
        return (
            f"Wired {workflow_id}: degenerate 1-leaf shape "
            "(no operator chain; terminal is the bound primitive "
            "directly)."
        )
    chain = " -> ".join(operator_names)
    terminal_desc = (
        f"{terminal_operator_name} -> {terminal_artifact_type}"
        if terminal_operator_name else "(leaf terminal)"
    )
    return f"Wired {workflow_id}: {chain}; terminal {terminal_desc}."


def _topological_operator_names(
    shape_or_workflow: Union[ShapeSpec, Workflow],
) -> List[str]:
    """Operator names in deterministic topological execution order.

    Kahn's algorithm over ``nodes`` + ``edges``, with the ready set
    kept as a min-heap on ``node_id`` so ties (parallel branches)
    break lexicographically — identical graphs always produce the
    identical tuple (byte-stable record), but the sequence now reads
    in the order the executor runs the DAG (leaves → terminal), not
    alphabetical node-id order.  Nodes left over after the walk (a
    cycle would be rejected upstream by the workflow validator; this
    is defensive) are appended in node_id order so the record never
    silently drops an operator.
    """
    import heapq

    nodes = shape_or_workflow.nodes
    edges = getattr(shape_or_workflow, "edges", None) or []
    indegree: Dict[str, int] = {n.node_id: 0 for n in nodes}
    downstream: Dict[str, List[str]] = {n.node_id: [] for n in nodes}
    for e in edges:
        if e.source_node_id in downstream and e.target_node_id in indegree:
            downstream[e.source_node_id].append(e.target_node_id)
            indegree[e.target_node_id] += 1

    ready = sorted(nid for nid, d in indegree.items() if d == 0)
    heapq.heapify(ready)
    order: List[str] = []
    while ready:
        nid = heapq.heappop(ready)
        order.append(nid)
        for target in downstream[nid]:
            indegree[target] -= 1
            if indegree[target] == 0:
                heapq.heappush(ready, target)
    if len(order) < len(nodes):  # defensive — see docstring
        seen = set(order)
        order.extend(nid for nid in sorted(indegree) if nid not in seen)

    node_map = {n.node_id: n for n in nodes}
    return [
        node_map[nid].operator_name
        for nid in order
        if isinstance(node_map[nid], OperatorNode)
    ]


def _composer_record_from(
    shape_or_workflow: Union[ShapeSpec, Workflow],
    *,
    llm_authored_rationale: str = "",
) -> ComposerIntentRecord:
    """Build a ComposerIntentRecord from either a ShapeSpec (pre-
    assembly) or a Workflow (post-assembly).  Both carry the same
    operator-topology + terminal info for the intent record's purposes.

    For ShapeSpec the terminal is resolved via ``node_by_id``; for
    Workflow the same.  The terminal's artifact type is read from the
    OPERATOR_REGISTRY (single source of truth) when the terminal is
    an OperatorNode; for the degenerate 1-leaf shape (terminal IS a
    leaf), the record carries empty terminal_operator_name +
    terminal_artifact_type — the L6 echo reads the leaf record to
    fill that in.
    """
    from shared.workflow.registry import OPERATOR_REGISTRY

    # Walk nodes — collect operator names, find terminal.
    terminal_operator_name = ""
    terminal_artifact_type = ""

    nodes = shape_or_workflow.nodes
    terminal = shape_or_workflow.node_by_id(
        shape_or_workflow.terminal_node_id,
    )

    # Operators in TOPOLOGICAL EXECUTION ORDER (deterministic Kahn,
    # ties broken by node_id) — the L6 "Wired:" line must read in the
    # order the DAG executes, not alphabetical node-id order (the
    # FRONTEND_SCORECARD T3 nit).  Still byte-stable: identical graphs
    # produce identical tuples.
    operator_names_sorted = _topological_operator_names(shape_or_workflow)

    if isinstance(terminal, OperatorNode):
        terminal_operator_name = terminal.operator_name
        spec = OPERATOR_REGISTRY.get(terminal.operator_name)
        if spec is not None:
            terminal_artifact_type = spec.output.artifact_type.value
    elif isinstance(terminal, PrimitiveNode):
        # Degenerate 1-leaf lookup — the terminal IS a leaf.  The
        # record leaves terminal_operator_name empty; the L6 echo
        # surfaces the leaf's semantic_role instead.
        terminal_operator_name = ""
        terminal_artifact_type = ""

    operator_names_tuple = tuple(operator_names_sorted)
    # PR-10B Codex F14: prefer LLM-authored rationale when supplied;
    # else fall back to deterministic derivation.
    rationale = (
        llm_authored_rationale.strip()
        if llm_authored_rationale and llm_authored_rationale.strip()
        else _derive_composer_rationale(
            workflow_id=shape_or_workflow.workflow_id,
            operator_names=operator_names_tuple,
            terminal_operator_name=terminal_operator_name,
            terminal_artifact_type=terminal_artifact_type,
        )
    )
    return ComposerIntentRecord(
        workflow_id=shape_or_workflow.workflow_id,
        terminal_operator_name=terminal_operator_name,
        terminal_artifact_type=terminal_artifact_type,
        operator_names=operator_names_tuple,
        rationale=rationale,
    )


__all__ = [
    "RouterIntentRecord",
    "SelectorIntentRecord",
    "ComposerIntentRecord",
    "GateIntentRecord",
    "IntentChain",
]

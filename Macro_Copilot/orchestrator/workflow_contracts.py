"""orchestrator/workflow_contracts.py — Pydantic contracts for the
workflow-template routing layer.

Mirrors the discipline of ``orchestrator/contracts.py``
(``RouteDecision`` for domain routing) but for the new layer that
maps a user prompt to a specific workflow TEMPLATE + bound
slot_values.

Architecture context
--------------------
The existing supervisor routes user queries to a DOMAIN
(sovereign_bonds vs ois) and the domain agent runs a ReAct tool
loop over the domain's primitive MCP tools.  That handles "where's
SOFR 2Y" style questions.

Workflow templates are a different question shape — DAG-shaped
analyses (event_study, regime_conditioned_relationship, ...) whose
slots are filled from the user's words deterministically.  The
WorkflowRouter (``orchestrator.workflow_router``) is the LLM step
that picks ONE template + slot_values; this module declares the
typed envelope for that decision.

Why a separate decision schema (not just reusing RouteDecision)
---------------------------------------------------------------
- The output shape is different: domain routing returns a list of
  domains; template routing returns a single template_id + a
  free-shape dict of slot_values.
- The closed enum is different: RouteAction = single_domain /
  multi_domain / clarify; WorkflowRouteAction = route /
  out_of_scope / clarify (no multi for V1; multi-template fan-out
  is deferred).
- Keeping the schemas separate lets the existing Supervisor stay
  untouched.  A future PR can integrate the WorkflowRouter into
  the main session pipeline without disturbing the existing
  contracts.

Field discipline
----------------
Same content-vs-shape split as ``RouteDecision``:
  - LLM produces ``action``, ``template_id``, ``slot_values``,
    ``rationale``, ``clarification_question``.
  - ``adjustments`` is a post-hoc, code-generated list of
    normalisation notes (unknown template_id → CLARIFY, slot type
    coercion, etc.).  The router clears anything the LLM tried to
    write into this field.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# WORKFLOW ROUTE ACTION
# ===========================================================================


class WorkflowRouteAction(str, Enum):
    """The closed-family decision the LLM emits.

    V1 actions:

      - ``ROUTE``         exactly one template chosen + every required
                          slot bound from the prompt.
      - ``OUT_OF_SCOPE``  no template in the catalogue answers this
                          prompt.  Distinct from ``CLARIFY`` — there's
                          no follow-up that would route to a workflow.
      - ``CLARIFY``       a template is plausible but at least one
                          required slot cannot be inferred from the
                          prompt; the router emits a focused follow-up
                          question.

    V1 deliberately omits a ``MULTI_TEMPLATE`` action — running two
    templates in one turn (e.g. "show me the event-study AND the
    regime-conditional regression") is deferred.  When that lands,
    extend this enum + ``WorkflowRouteDecision``.
    """

    ROUTE = "route"
    OUT_OF_SCOPE = "out_of_scope"
    CLARIFY = "clarify"


# ===========================================================================
# WORKFLOW ROUTE DECISION (LLM → code)
# ===========================================================================


class WorkflowRouteDecision(BaseModel):
    """The LLM's typed routing decision for a user prompt.

    Produced via ``with_structured_output(WorkflowRouteDecision)`` so
    the LLM cannot smuggle prose into the routing surface — same
    discipline as ``RouteDecision`` for domain routing.

    Fields populated by the LLM
    ---------------------------
    action :
        Which branch the router took.
    template_id :
        The chosen template's id from the catalogue.  Required when
        ``action == ROUTE``; should be ``None`` otherwise.
    slot_values :
        The bound slot dict.  Required when ``action == ROUTE``;
        should be empty otherwise.  Slot types must match the
        chosen template's ``slot_schema`` declarations (the LLM is
        instructed to respect declared types — the router post-
        validates and adjusts on mismatch).
    rationale :
        One short sentence (≤25 words) naming the cue from the user's
        prompt that drove the routing choice.  Logged for
        observability, not shown to the user.
    clarification_question :
        Populated ONLY when ``action == CLARIFY``.  Phrased as a
        focused follow-up — what slot is missing or ambiguous and
        what value would unblock the routing.

    Field populated by the code (post-hoc normalisation)
    ---------------------------------------------------
    adjustments :
        A list of human-readable notes generated by the router AFTER
        the LLM call when the raw output had to be repaired (unknown
        template_id, slot type coerced, missing required slot →
        CLARIFY, etc.).  The router clears anything the LLM tried to
        write into this field so only real normalisation notes from
        ``_normalise_workflow_route`` survive.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: WorkflowRouteAction = Field(
        ...,
        description=(
            "The routing action.  ``route`` when exactly one template "
            "fits the prompt and every required slot can be bound.  "
            "``out_of_scope`` when no template in the catalogue "
            "answers the prompt.  ``clarify`` when a template is "
            "plausible but a required slot cannot be inferred from "
            "the prompt."
        ),
    )
    template_id: Optional[str] = Field(
        default=None,
        description=(
            "The chosen template's id.  Populated when action="
            "``route``; ``None`` otherwise.  MUST be one of the "
            "registered template_ids returned by ``list_workflows``."
        ),
    )
    slot_values: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Slot bindings keyed by slot name, populated when action="
            "``route``.  Each value's type must match the chosen "
            "template's ``slot_schema`` declaration.  The router "
            "post-validates: a type mismatch or a missing required "
            "slot promotes the decision to ``clarify`` with an "
            "adjustments note."
        ),
    )
    rationale: str = Field(
        ...,
        description=(
            "One short sentence (≤25 words) naming the cue from the "
            "user's prompt that drove the routing choice.  Logged for "
            "observability, not shown to the user."
        ),
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description=(
            "Populated ONLY when action=``clarify``.  A concise "
            "follow-up question phrased the way a senior PM would "
            "phrase it (short, direct, names the missing slot)."
        ),
    )
    adjustments: List[str] = Field(
        default_factory=list,
        description=(
            "Post-hoc normalisation notes appended by the code — NOT "
            "produced by the LLM.  Populated when the raw decision "
            "had to be repaired (unknown template_id, slot type "
            "coerced, etc.).  Surfaced through the router so eval "
            "harnesses + debug panels can detect drift in the LLM's "
            "structured output."
        ),
    )


# ===========================================================================
# WORKFLOW EXECUTION RESULT
# ===========================================================================


class WorkflowExecutionResult(BaseModel):
    """End-to-end outcome of ``WorkflowRouter.execute`` — routing
    decision + execution envelope.

    The ``execution`` field carries the same JSON-friendly envelope
    ``rates_agent.workflows._runner.run_template`` returns:

        {"ok": true, "template_id": "...",
         "terminal_artifact": {...},
         "workflow_lineage_summary": "..."}

    or

        {"ok": false, "template_id": "...",
         "error": "<diagnostic>"}

    The ``execution`` field is populated when ``route.action ==
    ROUTE`` (i.e. the router proceeded to execute the bound
    workflow); ``None`` for CLARIFY / OUT_OF_SCOPE actions.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    route: WorkflowRouteDecision
    execution: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Workflow execution envelope from "
            "``rates_agent.workflows._runner.run_template``.  None "
            "when routing did not produce a ROUTE action (CLARIFY / "
            "OUT_OF_SCOPE)."
        ),
    )


__all__ = [
    "WorkflowRouteAction",
    "WorkflowRouteDecision",
    "WorkflowExecutionResult",
]

"""
orchestrator/contracts.py — Data contracts for supervisor ↔ child communication
================================================================================

Pydantic models that define the typed interfaces between the three layers of
the rates copilot:

    User ↔ Supervisor ↔ Domain Agent (sovereign_bonds / ois)

Design notes
------------
- The supervisor's route decision is produced via structured output
  (``with_structured_output``) so routing is deterministic JSON, not free-form
  prose.  This is the "thin supervisor" discipline: it picks domains, it does
  not translate the query.

- Each child returns a ``ChildResponse`` — prose answer + structured facts +
  tool trace.  The supervisor's synthesis step consumes the structured facts
  directly, so cross-domain answers never round-trip numbers through free
  text (which is where hallucination creeps in).

- ``Domain`` values match the sub-package directory names
  (``rates_agent/sovereign_bonds/``, ``rates_agent/ois/``) so routing keys,
  config keys, and code paths stay 1:1.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, List, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# DOMAIN ENUM
# ============================================================================

class Domain(str, Enum):
    """The set of domain specialists the supervisor can route to.

    New domains are added here first, then wired into ``DOMAIN_MCP_SERVERS``
    in ``config.py`` and given a system prompt in ``prompts.py``.
    """

    SOVEREIGN_BONDS = "sovereign_bonds"
    OIS = "ois"


# ============================================================================
# ROUTE DECISION (supervisor → code)
# ============================================================================

class RouteAction(str, Enum):
    SINGLE_DOMAIN = "single_domain"
    MULTI_DOMAIN = "multi_domain"
    CLARIFY = "clarify"


class RouteDecision(BaseModel):
    """The supervisor's routing decision for a user query.

    The supervisor NEVER answers the query — it only decides who should.
    """

    action: RouteAction = Field(
        ...,
        description=(
            "The routing action.  'single_domain' when exactly one specialist "
            "can answer the query.  'multi_domain' when two or more specialists "
            "are needed (e.g. a cross-curve comparison).  'clarify' only when "
            "the query is genuinely ambiguous and you cannot pick a domain from "
            "context."
        ),
    )
    domains: List[Domain] = Field(
        default_factory=list,
        description=(
            "The domains to invoke.  Exactly one entry for single_domain, two "
            "or more for multi_domain, empty list for clarify."
        ),
    )
    rationale: str = Field(
        ...,
        description=(
            "One short sentence (max 15 words) naming the signal in the user's "
            "query that drove your routing choice.  Logged for observability, "
            "not shown to the user."
        ),
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description=(
            "Populated ONLY when action='clarify'.  A concise question phrased "
            "as a senior PM would phrase it."
        ),
    )
    adjustments: List[str] = Field(
        default_factory=list,
        description=(
            "Post-hoc normalisation notes appended by the code — NOT produced "
            "by the supervisor LLM.  Populated when the raw LLM output had to "
            "be repaired (duplicate domains, action/length mismatch, etc.). "
            "Surfaced in the ``route_decision`` streaming event so an eval "
            "harness or debug panel can detect drift in the supervisor's JSON "
            "without scraping logs."
        ),
    )


# ============================================================================
# CHILD RESPONSE (domain agent → supervisor)
# ============================================================================

class ChildStatus(str, Enum):
    OK = "ok"
    NEEDS_CLARIFICATION = "needs_clarification"
    OUT_OF_SCOPE = "out_of_scope"
    ERROR = "error"


class FactRow(BaseModel):
    """One structured fact extracted from a child's tool output.

    Facts are what the supervisor synthesizes across children in the
    multi-domain path.  Keeping them structured (not prose) prevents the
    supervisor from re-interpreting or rounding numbers.
    """

    tool: str = Field(..., description="Tool that produced this fact.")
    curve_family: Optional[str] = Field(default=None)
    metric: str = Field(..., description="Metric name, e.g. 'current_spread_bps'.")
    value: Any = Field(..., description="Scalar value.  Usually float, str, or int.")
    units: Optional[str] = Field(default=None, description="e.g. 'bps', '%'.")
    as_of: Optional[str] = Field(default=None, description="YYYY-MM-DD of the observation.")


class ChildToolCallTrace(BaseModel):
    """One entry in a child's tool-call timeline."""

    tool: str
    params: dict = Field(default_factory=dict)
    duration_ms: Optional[int] = None
    error: Optional[str] = None


class ChildResponse(BaseModel):
    """What a domain child returns to the supervisor."""

    status: ChildStatus
    domain: Domain
    answer_markdown: str = Field(
        default="",
        description=(
            "PM-facing prose answer for this domain only.  In the single-domain "
            "path this is returned to the user directly; in the multi-domain "
            "path the supervisor uses ``facts`` for synthesis and may or may "
            "not reference this prose."
        ),
    )
    facts: List[FactRow] = Field(default_factory=list)
    workspace_context: Optional[dict] = None
    tool_trace: List[ChildToolCallTrace] = Field(default_factory=list)
    follow_up_question: Optional[str] = Field(
        default=None,
        description="Populated when status='needs_clarification'.",
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Populated when status='error'.",
    )



# ============================================================================
# PROPOSED OVERRIDES (R5.5)
# ============================================================================
#
# When the user types a workspace-scoped intent that maps to a parameter
# change ("change the z-score window to 126d", "use ACT/365 instead"),
# the supervisor (or a cheaper pre-classifier) can emit structured
# ``proposed_overrides`` on the ``done`` event.  The frontend renders
# them as click-to-queue chips that dispatch into the shared workspace
# overrides queue — wired in PR #141.

class ProposedOverride(BaseModel):
    """One parameter-override suggestion the chat emits to the
    frontend's `proposed_overrides` channel.

    Mirrors ``ServerProposedOverride`` on the wire (``src/types/
    copilot.ts``) so the frontend can dispatch the chip click directly
    into ``WorkspaceOverridesProvider`` without translation.

    ``path`` follows the override-state convention:
      - ``[slot]`` for scalar slots
      - ``[slot, field]`` for dict-merge entries
    """

    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = Field(
        default=None,
        description=(
            "Stable id within the message so chips can be approved / "
            "rejected individually.  Optional; the frontend falls back "
            "to a hash of (path, value)."
        ),
    )
    path: Union[Tuple[str], Tuple[str, str]] = Field(
        ...,
        description=(
            "Override-key path.  One-element tuple for top-level scalar "
            "slots, two-element tuple for nested dict-merge entries "
            "(e.g. ('signal_params', 'window_days'))."
        ),
    )
    value: Any = Field(
        ...,
        description="New value the user implied.  Scalar (string/number/bool).",
    )
    value_label: Optional[str] = Field(
        default=None,
        description=(
            "Optional human-readable label for the chip (e.g. 'ACT/365') "
            "when the raw value would print awkwardly.  Defaults to "
            "str(value) on the wire."
        ),
    )
    node_id: Optional[str] = Field(
        default=None,
        description=(
            "The stage's node_id this override targets.  Optional — the "
            "override map is path-keyed and doesn't need it — but carrying "
            "it through lets the rail render 'for stage X' context."
        ),
    )
    previous_value: Any = Field(
        default=None,
        description=(
            "The substrate's previous value at this path, when known.  "
            "Lets the chip show 'was → is' before the user applies it."
        ),
    )
    rationale: Optional[str] = Field(
        default=None,
        description=(
            "Short note explaining why this override is being suggested. "
            "Rendered as the chip's tooltip."
        ),
    )

    def to_wire(self) -> dict:
        """Serialise to the snake_case wire shape the frontend expects."""
        return {
            "id": self.id,
            "path": list(self.path),
            "value": self.value,
            "value_label": self.value_label,
            "node_id": self.node_id,
            "previous_value": self.previous_value,
            "rationale": self.rationale,
        }


class ProposedOverridesList(BaseModel):
    """Top-level structured-output model for an LLM that classifies
    override intent.  Used by ``orchestrator/override_classifier.py``
    when the heuristic fast-path doesn't find a match and the system
    falls through to the structured LLM call (env-gated; off by
    default in v1 to avoid per-turn cost)."""

    model_config = ConfigDict(extra="forbid")

    overrides: List[ProposedOverride] = Field(default_factory=list)

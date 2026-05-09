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
from typing import Any, List, Optional

from pydantic import BaseModel, Field


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
    INFLATION_INDEXED_BONDS = "inflation_indexed_bonds"
    INFLATION_SWAPS = "inflation_swaps"


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

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
# DOMAIN ENUM — REGISTRATION-ONLY via orchestrator.domain_registry
# ============================================================================
#
# PR-10F Codex audit gap #2: the Domain enum's MEMBERS are now BUILT at
# import time from ``orchestrator.domain_registry.DOMAIN_SPECS``, which
# auto-discovers every sub-package of ``rates_agent/`` that declares the
# required ``__domain_id__`` / ``__domain_label__`` / etc. constants.
#
# Adding the Nth domain requires ZERO source edits to this file (or to
# orchestrator/config.py, orchestrator/open_dag/resolver_keys.py).  Just:
#   1. Drop a folder ``rates_agent/<new_domain>/``
#   2. Declare the discovery constants on its ``__init__.py``
#   3. Add a ``mcp_server.py`` exposing the @mcp.tool() functions
#
# The Domain TYPE is still a ``str, Enum`` subclass — LangChain's
# ``with_structured_output(RouteDecision)`` reads it at Supervisor
# construction time, by which point discovery has already completed.
# Pydantic Field(Domain) introspects the same closed set.  So the cache
# stability invariant (the previous round's documented concern) is
# preserved: within one session the schema is identical to what the
# hardcoded enum produced.
#
# The per-domain SUPERVISOR routing card content (AVAILABLE DOMAINS,
# DOMAIN SIGNALS) and the per-domain child SYSTEM_PROMPT strings still
# ship as content in orchestrator/prompts.py; a follow-up could move them
# onto each domain's __init__.py as __domain_card__ / __domain_signals__
# / __domain_child_prompt__ constants templated into the prompt.  That is
# content migration, not a code-surface gap.
from orchestrator.domain_registry import DOMAIN_SPECS as _DOMAIN_SPECS

Domain = Enum(  # type: ignore[misc]
    "Domain",
    {spec.domain_id.upper(): spec.domain_id for spec in _DOMAIN_SPECS.values()},
    type=str,
)
Domain.__doc__ = (
    "The set of domain specialists the supervisor can route to.  "
    "Members are discovered at import time from "
    "``rates_agent/<domain>/__init__.py`` via "
    "``orchestrator.domain_registry``.  Adding the Nth domain requires "
    "ONLY dropping a folder under ``rates_agent/`` with the required "
    "discovery constants — no edit to this enum body, to "
    "``orchestrator/config.py``, or to "
    "``orchestrator/open_dag/resolver_keys.py``."
)


# ============================================================================
# ROUTE DECISION (supervisor → code)
# ============================================================================

class RouteAction(str, Enum):
    SINGLE_DOMAIN = "single_domain"
    MULTI_DOMAIN = "multi_domain"
    CLARIFY = "clarify"


# ============================================================================
# INTENT TAG (PR-5: L1 router decomposition)
# ============================================================================


class IntentTag(str, Enum):
    """Closed family of intent tags the L1 router emits per query.

    Per ``tmp/orchestration.md`` §PR-5: the L1 router must label the
    user's intent with one of these nine tags so the L3 Composer (PR-7)
    can choose the right operator family and the Boundary B coverage
    gate (PR-8) can sanity-check the assembled DAG against the
    documented intent.  Extension is an ADR change.
    """

    LOOKUP = "lookup"
    RELATIONSHIP = "relationship"
    REGRESSION = "regression"
    COINTEGRATION = "cointegration"
    TRANSFORM = "transform"
    EVENT_REGIME = "event_regime"
    SCAN = "scan"
    PANEL = "panel"
    BASIS = "basis"


# ============================================================================
# ECONOMIC QUANTITY (PR-5: L1 router decomposition)
# ============================================================================


class EconomicQuantity(BaseModel):
    """One named economic quantity the L1 router identified in the query.

    Frozen Pydantic.  Carries a slug (``name``), a one-sentence English
    description, and the routing ``domain_hint`` that owns the
    quantity.  The downstream L3 Composer (PR-7) consumes these to
    decide which L2 Selectors to dispatch leaf-requests to, and
    Boundary B (PR-8) consumes them as supplementary evidence when
    sanity-checking the assembled DAG against the original prompt.

    Per the plan's acceptance criterion: single-domain queries STILL
    produce decomposition (with one entry).  Decomposition is the
    coverage oracle for the downstream verification step.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        ...,
        min_length=1,
        description=(
            "Short slug identifier for the quantity (e.g. 'us_2s10s', "
            "'us_5y_breakeven').  Used in lineage + debugging surfaces; "
            "the LLM should keep this stable across runs for the same "
            "kind of quantity."
        ),
    )
    nl_description: str = Field(
        ...,
        min_length=1,
        description=(
            "One-sentence English meaning a senior PM would write to "
            "describe the quantity (e.g. 'UST curve spread, 2Y minus "
            "10Y').  Boundary B reads this when echoing the DAG back "
            "to the prompt."
        ),
    )
    domain_hint: Domain = Field(
        ...,
        description=(
            "Which domain owns this quantity.  PREFER a domain that "
            "is already in the parent RouteDecision.domains list.  "
            "However, when the prompt names a quantity from a domain "
            "the router did NOT include in `domains`, INCLUDE the "
            "entry anyway with the correct domain_hint — the "
            "normaliser PRESERVES out-of-routing entries and attaches "
            "a structured adjustment note so the downstream Boundary "
            "B (L4.5 Coverage Gate) sees the under-scoping evidence "
            "and can refuse / clarify.  Silently dropping mismatched "
            "entries hides the very failure mode Boundary B exists "
            "to catch."
        ),
    )


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
    # ---- PR-5 additions (backward-compatible defaults) ----
    intent_tag: Optional[IntentTag] = Field(
        default=None,
        description=(
            "One of the nine IntentTag closed-family values describing "
            "the user's intent (lookup / relationship / regression / "
            "cointegration / transform / event_regime / scan / panel / "
            "basis).  Populated for non-clarify actions; null for "
            "clarify (the intent is unknown until the user disambiguates)."
        ),
    )
    decomposition: List[EconomicQuantity] = Field(
        default_factory=list,
        description=(
            "Named economic quantities identified in the user's query, "
            "each tagged with its owning domain.  Exactly one entry "
            "for single_domain, two or more for multi_domain, empty "
            "list for clarify.  Even single-domain queries produce "
            "decomposition so the downstream coverage gate has an "
            "oracle.  Composite-noun queries (e.g. '5y5y real yield') "
            "decompose into their constituent legs (nominal + breakeven)."
        ),
    )
    # ---- Orchestration-upgrade plan D2/D5 (backward-compatible default) ----
    expected_answer_shape: List[str] = Field(
        default_factory=list,
        description=(
            "The SOFT output-shape contract (closed vocab "
            "AnswerShape: scalar / series / series_set / event_set / "
            "panel / any) — the SET of artifact shapes the question's "
            "answer may take.  ONE number ('the average / current value "
            "of X', 'how correlated', 'is X cointegrated') → ['scalar']. "
            "A value-per-date answer ('show me X over time', 'rolling "
            "z-score', a chart) → ['series'].  Genuinely open-ended → "
            "['any'].  Ambiguous → list BOTH (e.g. ['scalar','series']). "
            "Advisory: the deterministic verifier checks the DAG's "
            "terminal artifact type against this set and only flags a "
            "CLEAR contradiction.  Empty list == unconstrained (treated "
            "as 'any').  Populated for non-clarify actions."
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

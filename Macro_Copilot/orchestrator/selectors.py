"""orchestrator.selectors — PR-6 of the open-DAG PoC.

The pure logic backing ``DomainAgentSession.fill_leaf``.  The split is
deliberate: the heavy plumbing (MCP subprocess, LangChain model
construction, LangGraph state) stays inside ``orchestrator.domain_agent``
where the existing ``run`` (ReAct) mode lives; the deterministic pieces
the Selector relies on — tool-catalogue rendering, the user-prompt
builder, the post-LLM transformation from the structured-output payload
into a ``BoundLeaf`` — live here and are unit-tested without needing a
live MCP server or a live LLM.

What the Selector does
======================

1. Receives a ``LeafRequest`` from the Composer (L3) via the Assembler
   (L4), tagged with the domain this Selector owns.
2. Looks at its OWN domain's MCP tool catalogue (P11 isolation enforced
   at the MCP-subprocess level — the Selector can't see other domains'
   primitives, period).
3. Reasons about which tool best satisfies the LeafRequest, OR refuses
   if no tool fits (the "refuse rather than bind nearest" discipline
   per the plan).
4. Emits a ``BoundLeaf`` carrying the chosen primitive's identity in
   the resolver-safe form the Assembler expects.

What the Selector does NOT do
=============================

- It NEVER executes a primitive.  The static
  ``orchestrator.open_dag.primitive_declarations.declare_primitive_output``
  helper is the only metadata channel; no DB engine is touched.
- It NEVER invents a ``resolver_tool_key`` — the code derives it
  deterministically from ``(domain, mcp_tool_name)`` via
  ``orchestrator.open_dag.resolver_keys.domain_to_resolver_key``.
- It NEVER binds more than one primitive per call.  Multi-binding /
  composition is L3's job.

Two-layer split
===============

This module exposes pure functions:

  - ``render_tool_catalogue`` — turns the live MCP tool list + the
    agent-side resolver into a ``ToolCatalogueEntry`` list.
  - ``render_user_prompt`` — turns the LeafRequest + catalogue into the
    string the LLM sees as its user message.
  - ``llm_output_to_bound_leaf`` — turns the structured-output payload
    + the resolver-side catalogue into a ``BoundLeaf``.  Refusal
    handled here.

The LLM-call layer (the actual model invocation) lives in
``DomainAgentSession.fill_leaf`` so the existing MCP / LangChain
plumbing isn't duplicated.

Finance-blindness
=================

This module sits in ``orchestrator/`` (the agent layer).  It DOES
import from ``orchestrator.open_dag`` (the typed open-DAG contracts)
and from ``shared.workflow`` (substrate registry types).  It does NOT
import from ``rates_agent/`` — the agent-side primitive resolver is
passed in by the caller, and every primitive identity travels through
the substrate's typed channels.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.contracts import Domain
from orchestrator.open_dag import (
    BoundLeaf,
    Frequency,
    LeafRequest,
    PrimitiveDeclaration,
    declare_primitive_output,
    domain_to_resolver_key,
)
from shared.schemas.time_series import TimeSeriesUnits
from shared.workflow.registry import PrimitiveResolver


# ============================================================================
# CATALOGUE ENTRY — one row per MCP tool in this domain
# ============================================================================


class ToolCatalogueEntry(BaseModel):
    """One row of the per-Selector tool catalogue.

    Built once per ``DomainAgentSession.open()`` from the MCP-visible
    tool list + the agent-side ``PrimitiveResolver``.  Carries
    everything the Selector LLM needs to make an informed choice
    (rich description + structured metadata) AND everything the
    post-LLM code needs to build a resolver-safe ``BoundLeaf``
    (resolver key + declared output type + declared per-field units).

    Frozen.  Round-trips cleanly through JSON for logging.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Visible identity (what the LLM sees + selects by name)
    mcp_tool_name: str = Field(..., min_length=1)
    description: str = Field(
        ...,
        min_length=1,
        description=(
            "The primitive's rich MCP docstring.  These are the priors "
            "the Selector LLM reasons over."
        ),
    )

    # Resolver-safe identity (post-LLM code uses this for BoundLeaf)
    resolver_tool_key: str = Field(..., min_length=1)

    # Static output metadata (from PrimitiveDeclaration)
    output_artifact_type: str = Field(..., min_length=1)
    output_field_units: Dict[str, str] = Field(default_factory=dict)
    available_output_fields: Tuple[str, ...] = Field(default_factory=tuple)


# ============================================================================
# SELECTOR LLM STRUCTURED OUTPUT
# ============================================================================


class SelectorLLMOutput(BaseModel):
    """The Selector LLM's structured output before the code wrap.

    The LLM picks the tool (or refuses) and supplies the free-form
    fields (``declared_semantic_role`` / ``declared_output_meaning``)
    plus a ``fit_confidence``.  The post-LLM code derives the
    closed-substrate fields (``resolver_tool_key`` from
    ``domain_to_resolver_key``; ``declared_output_artifact_type`` and
    ``declared_units`` from the matching catalogue entry).

    Why the split: closed-substrate fields are GROUND TRUTH from the
    primitive's registration, not LLM-derived.  Letting the LLM
    populate them is a path to subtle drift (the LLM declares "bps"
    but the primitive actually emits "percent").  The code derives;
    the LLM cannot fabricate.

    ``refusal=None`` means the LLM bound the leaf.  ``refusal`` set
    means the Selector explicitly declined — and per the plan's
    "refuse rather than bind nearest" discipline, the wrapper does
    NOT second-guess the refusal.
    """

    model_config = ConfigDict(extra="forbid")

    # Binding choice (meaningful when refusal is None).
    chosen_mcp_tool_name: str = Field(
        default="",
        description=(
            "The MCP tool name from the catalogue this Selector saw.  "
            "Leave empty when refusing.  Must match a "
            "ToolCatalogueEntry.mcp_tool_name exactly."
        ),
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The primitive's input params.  Substrate does NOT "
            "validate; the primitive's own *Input schema catches "
            "errors at execute time.  Leave empty when refusing."
        ),
    )
    chosen_output_field: str = Field(
        default="",
        description=(
            "Which time_series* field of the primitive's output to "
            "lift via the bridge.  Must be in the catalogue entry's "
            "available_output_fields when non-empty.  Leave empty "
            "when refusing."
        ),
    )

    # Optional declarations (LLM-authored; closed-substrate types).
    declared_frequency: Optional[Frequency] = Field(
        default=None,
        description=(
            "Closed-family Frequency value or null.  PR-4 Boundary A "
            "hard-checks against LeafRequest.expected_frequency."
        ),
    )

    # Free-form declarations (LLM-authored; SOFT warnings on mismatch).
    declared_semantic_role: str = Field(
        default="",
        description=(
            "Selector's own tag for the bound primitive's role.  PR-4 "
            "Boundary A normalised-string compares against the "
            "LeafRequest.semantic_role; mismatch surfaces as a SOFT "
            "warning.  Leave empty when refusing."
        ),
    )
    declared_output_meaning: str = Field(
        default="",
        description=(
            "One-sentence English describing what this bound primitive "
            "produces.  Leave empty when refusing."
        ),
    )

    fit_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Selector's self-assessment of how well the bound "
            "primitive fits the LeafRequest.  Low confidence + a "
            "binding gives Boundary B (PR-8) a soft signal to lean "
            "toward CLARIFY.  Pass 0.0 when refusing."
        ),
    )
    refusal: Optional[str] = Field(
        default=None,
        description=(
            "Non-empty string when the Selector declined to bind any "
            "primitive (no tool fits the LeafRequest).  Null when the "
            "Selector bound a primitive."
        ),
    )


# ============================================================================
# CATALOGUE RENDERING — MCP tools + resolver → ToolCatalogueEntry list
# ============================================================================


def render_tool_catalogue(
    domain: Domain,
    mcp_tools: Sequence[Any],
    resolver: PrimitiveResolver,
) -> List[ToolCatalogueEntry]:
    """Render the per-Selector tool catalogue.

    Parameters
    ----------
    domain :
        The Selector's own domain.  Used to derive resolver-safe
        primitive keys via ``domain_to_resolver_key``.
    mcp_tools :
        Sequence of MCP tool descriptors.  Each must expose ``.name``
        and ``.description`` attributes — the LangChain
        ``MultiServerMCPClient.get_tools()`` return type satisfies this
        in the live path; tests pass plain duck-typed objects.
    resolver :
        The agent-side ``PrimitiveResolver`` (e.g.
        ``rates_agent.workflows.rates_primitive_resolver``).  Used
        only for static metadata lookup via
        ``declare_primitive_output`` — no callable invoked.

    Returns
    -------
    list[ToolCatalogueEntry]
        One entry per MCP tool, in input order.  Entries whose
        resolver lookup fails (the MCP server registered a tool but
        no PrimitiveSpec exists) are dropped — a defensive measure
        for the rates resolver's transition state where new MCP
        tools may temporarily lag registry entries.
    """
    out: List[ToolCatalogueEntry] = []
    domain_value = domain.value
    for tool in mcp_tools:
        mcp_name = getattr(tool, "name", None)
        description = getattr(tool, "description", None)
        if not mcp_name or not description:
            continue
        resolver_key = domain_to_resolver_key(domain_value, mcp_name)
        try:
            decl: PrimitiveDeclaration = declare_primitive_output(
                resolver, resolver_key,
            )
        except KeyError:
            # MCP exposes the tool but the resolver doesn't know it.
            # Skip — the Selector can't bind something it has no
            # static declaration for.
            continue
        out.append(ToolCatalogueEntry(
            mcp_tool_name=mcp_name,
            description=description,
            resolver_tool_key=resolver_key,
            output_artifact_type=decl.output_artifact_type,
            output_field_units=dict(decl.output_field_units),
            available_output_fields=decl.available_output_fields,
        ))
    return out


# ============================================================================
# USER-PROMPT RENDERING
# ============================================================================


def render_user_prompt(
    domain: Domain,
    request: LeafRequest,
    catalogue: Sequence[ToolCatalogueEntry],
) -> str:
    """Render the user message the Selector LLM sees.

    The system prompt
    (``orchestrator.prompts.SELECTOR_FILL_LEAF_SYSTEM_PROMPT``)
    carries the static instructions + refusal discipline.  The user
    message — built here — carries the per-call payload: the
    LeafRequest in full + the domain's tool catalogue rendered as a
    sequence of per-tool blocks.

    Keeping this deterministic (sorted keys / stable ordering / no
    randomness) means the resulting prompt is cache-friendly for the
    Anthropic prompt cache and reproducible across runs.
    """
    lines: List[str] = []
    lines.append(f"DOMAIN: {domain.value}")
    lines.append("")
    lines.append("LEAF REQUEST:")
    lines.append(
        f"  required_artifact_type: {request.required_artifact_type.value}"
    )
    if request.expected_units is not None:
        lines.append(f"  expected_units:         {request.expected_units.value}")
    else:
        lines.append("  expected_units:         (none — any unit acceptable)")
    if request.expected_frequency is not None:
        lines.append(
            f"  expected_frequency:     {request.expected_frequency.value}"
        )
    else:
        lines.append("  expected_frequency:     (none — any frequency acceptable)")
    lines.append(f"  semantic_role:          {request.semantic_role}")
    lines.append(
        f"  requested_output_meaning: {request.requested_output_meaning}"
    )
    lines.append(f"  nl_intent:              {request.nl_intent}")
    lines.append("")
    lines.append(f"AVAILABLE TOOLS (this domain, {len(catalogue)} total):")
    for entry in catalogue:
        lines.append("")
        lines.append(f"### {entry.mcp_tool_name}")
        lines.append(
            f"OUTPUT: artifact_type={entry.output_artifact_type}; "
            f"available_output_fields={list(entry.available_output_fields)}"
        )
        if entry.output_field_units:
            lines.append(
                f"OUTPUT UNITS by field: "
                f"{dict(sorted(entry.output_field_units.items()))}"
            )
        lines.append("DESCRIPTION:")
        # The docstring is indented for visual clarity; preserves the
        # primitive author's formatting.
        for line in entry.description.splitlines():
            lines.append(f"  {line}")
    lines.append("")
    lines.append(
        "Pick ONE tool (chosen_mcp_tool_name + params + "
        "chosen_output_field), OR set refusal=<reason> if no tool "
        "fits.  Do NOT bind the nearest wrong tool."
    )
    return "\n".join(lines)


# ============================================================================
# OUTPUT → BoundLeaf
# ============================================================================


class SelectorBindingError(ValueError):
    """Raised by ``llm_output_to_bound_leaf`` when the LLM output is
    structurally impossible to convert into a ``BoundLeaf`` (e.g. the
    chosen tool is not in the catalogue).  Caller converts to a
    refusal BoundLeaf for downstream consumption."""


def llm_output_to_bound_leaf(
    *,
    leaf_id: str,
    domain: Domain,
    output: SelectorLLMOutput,
    catalogue: Sequence[ToolCatalogueEntry],
) -> BoundLeaf:
    """Transform the Selector LLM's structured output into a
    ``BoundLeaf``.

    Two paths:
      (a) Refusal — ``output.refusal`` is non-empty.  Build a refusal
          BoundLeaf (sentinel structural fields are allowed by the
          BoundLeaf contract).
      (b) Binding — locate the catalogue entry for
          ``output.chosen_mcp_tool_name``, derive the closed-substrate
          fields (resolver_tool_key, declared_output_artifact_type,
          declared_units) from it, and construct the final BoundLeaf.

    If the LLM picks a tool not in the catalogue, the path-(b) lookup
    fails and we convert to a refusal BoundLeaf with a structured
    reason.  This preserves the "no exception escapes fill_leaf"
    contract by handling the failure cleanly.
    """
    leaf_id = leaf_id.strip()
    if not leaf_id:
        raise SelectorBindingError(
            "leaf_id must be non-empty — the Assembler maps the "
            "returned BoundLeaf back to its LeafHole by leaf_id."
        )

    if output.refusal is not None and output.refusal.strip():
        return BoundLeaf(
            leaf_id=leaf_id,
            domain=domain.value,
            fit_confidence=output.fit_confidence,
            refusal=output.refusal.strip(),
        )

    chosen = output.chosen_mcp_tool_name.strip()
    if not chosen:
        # Selector didn't pick a tool AND didn't refuse — that's a
        # selector contract violation; treat as a structured refusal.
        return BoundLeaf(
            leaf_id=leaf_id,
            domain=domain.value,
            fit_confidence=output.fit_confidence,
            refusal=(
                "Selector LLM produced neither a tool choice nor a "
                "refusal — treating as refusal per the bounded "
                "Selector contract."
            ),
        )

    # Lookup catalogue entry.
    entry: Optional[ToolCatalogueEntry] = None
    for e in catalogue:
        if e.mcp_tool_name == chosen:
            entry = e
            break
    if entry is None:
        return BoundLeaf(
            leaf_id=leaf_id,
            domain=domain.value,
            fit_confidence=output.fit_confidence,
            refusal=(
                f"Selector LLM chose mcp_tool_name={chosen!r} which "
                f"is not in this domain's tool catalogue "
                f"({sorted(e.mcp_tool_name for e in catalogue)}).  "
                "Refusing rather than guessing."
            ),
        )

    output_field = output.chosen_output_field.strip()
    if not output_field:
        return BoundLeaf(
            leaf_id=leaf_id,
            domain=domain.value,
            mcp_tool_name=chosen,
            resolver_tool_key=entry.resolver_tool_key,
            fit_confidence=output.fit_confidence,
            refusal=(
                "Selector LLM bound a tool but left chosen_output_field "
                "empty — refusing per the bounded Selector contract."
            ),
        )

    # Derive closed-substrate fields from the catalogue (ground truth).
    declared_units_raw = entry.output_field_units.get(output_field)
    declared_units: Optional[TimeSeriesUnits] = None
    if declared_units_raw is not None:
        try:
            declared_units = TimeSeriesUnits(declared_units_raw)
        except ValueError:
            # Primitive's output_field_units carries a unit string that
            # isn't a TimeSeriesUnits member.  Don't fail — let the
            # downstream substrate validator surface it; declared_units
            # stays None.
            declared_units = None

    return BoundLeaf(
        leaf_id=leaf_id,
        domain=domain.value,
        mcp_tool_name=chosen,
        resolver_tool_key=entry.resolver_tool_key,
        params=dict(output.params),
        output_field=output_field,
        declared_output_artifact_type=entry.output_artifact_type,
        declared_units=declared_units,
        declared_frequency=output.declared_frequency,
        declared_semantic_role=(
            output.declared_semantic_role.strip()
            or request_role_fallback(output)
        ),
        declared_output_meaning=(
            output.declared_output_meaning.strip()
            or request_meaning_fallback(output)
        ),
        fit_confidence=output.fit_confidence,
        refusal=None,
    )


def request_role_fallback(output: SelectorLLMOutput) -> str:
    """Used when the LLM bound a tool but omitted declared_semantic_role.
    The BoundLeaf contract requires a non-empty value in binding mode,
    so we substitute a deterministic sentinel for the post-hoc
    Boundary A check to flag as a soft warning."""
    return (
        f"(Selector did not declare a semantic_role for tool "
        f"{output.chosen_mcp_tool_name!r}.)"
    )


def request_meaning_fallback(output: SelectorLLMOutput) -> str:
    return (
        f"(Selector did not declare an output_meaning for tool "
        f"{output.chosen_mcp_tool_name!r}.)"
    )


# ============================================================================
# REQUEST-DOMAIN MISMATCH GUARD
# ============================================================================


def assert_request_for_this_domain(
    request: LeafRequest, domain: Domain,
) -> None:
    """Guard the boundary: a Selector should only receive
    LeafRequests whose ``domain_hint`` matches its own domain.  Raises
    ``SelectorBindingError`` on mismatch — the Assembler is supposed
    to dispatch by domain_hint and a mismatch is an upstream bug, not
    something the Selector should silently re-route."""
    if request.domain_hint != domain.value:
        raise SelectorBindingError(
            f"Selector for domain {domain.value!r} received a "
            f"LeafRequest with domain_hint={request.domain_hint!r}.  "
            "The Assembler must dispatch each LeafRequest to the "
            "Selector matching its domain_hint."
        )


__all__ = [
    "ToolCatalogueEntry",
    "SelectorLLMOutput",
    "SelectorBindingError",
    "render_tool_catalogue",
    "render_user_prompt",
    "llm_output_to_bound_leaf",
    "assert_request_for_this_domain",
    "request_role_fallback",
    "request_meaning_fallback",
]

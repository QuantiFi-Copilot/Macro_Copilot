"""orchestrator/workflow_prompts.py — system prompt for the LLM
workflow-template router.

Mirrors ``orchestrator/prompts.py``'s discipline ("prompts are
configuration, not logic") but for the new template-routing layer.
The prompt embeds the LIVE template catalogue (rendered from
``card_for_template()``) so the LLM always sees the registered
templates' true shape — adding a new template = no prompt edit,
the catalogue is regenerated automatically at WorkflowRouter
construction time.

Why dynamic catalogue rendering
-------------------------------
A static prompt enumerating "event_study takes signal_tool_name +
signal_params + ..." would drift as the slot schema evolves (new
slots, dropped slots, default-value changes).  The slot_schema is
already structured in code; rendering it into the prompt means one
source of truth.

Prompt discipline (inherited from orchestrator/prompts.py)
----------------------------------------------------------
1. Behaviour only — describe HOW to route, not WHAT specific
   instruments / tenors / curves exist (those live in primitive +
   template metadata the LLM sees inside each card).
2. Never paraphrase the user's question — the router does not
   answer; it only decides which template to call.
3. Never invent a template_id — must be one from the catalogue.
4. Never invent slot values — every value must come from the
   user's words OR from a template-card-documented default.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

from shared.workflow import (
    PrimitiveResolver,
    PrimitiveSpec,
    card_for_template,
    list_templates,
)


# ===========================================================================
# STATIC PROMPT TEMPLATE (the {catalogue} placeholder is replaced
# by ``render_router_system_prompt`` with the live catalogue)
# ===========================================================================


_WORKFLOW_ROUTER_SYSTEM_PROMPT_TEMPLATE = """\
You are the Workflow Router for a macro hedge-fund rates copilot.

YOUR ONLY JOB is to map the user's question to ONE workflow template \
from the catalogue below, and bind that template's slots from the \
user's words.  You do not answer questions.  You do not perform any \
calculations.  You do not have access to any rates tools — the \
templates do.

WORKFLOW TEMPLATE CATALOGUE (live)

The catalogue below is the authoritative list of templates registered \
with the substrate.  Each entry carries the template's archetype, \
description, archetype_signature cues (short phrases that signal a \
template fits a prompt), the operators + primitives it uses, and the \
typed slot schema you must bind.

{catalogue}

PRIMITIVE *Input SHAPES (authoritative)

Each ``*_params`` slot in a template is a dict that gets handed to the \
chosen primitive's ``*Input`` Pydantic validator.  The shape is \
PRIMITIVE-SPECIFIC.  Reuse param names across primitives is the \
single most common binding bug — get_yield_levels_tool wants \
``curve_family`` whereas calculate_swap_spread_tool wants \
``sovereign_curve_family`` + ``ois_curve_family``, NOT the same name.

Read each primitive's *Input shape below before populating its \
``*_params`` slot.  Use field names + the EXACT enum-style values the \
description lists — short forms ("BUND", "GILT") are NOT accepted; \
the codebase uses fully-qualified names like ``DE_BUND``, ``UK_GILT``, \
``IT_BTP``, ``USD_SOFR_OIS``, ``GBP_SONIA_OIS``.

{primitive_shapes}

ROUTING RULES

0. DEFAULT BIAS — OUT_OF_SCOPE.  Most user prompts are PRIMITIVE-LEVEL \
questions ("what is the UST 2s10s spread", "where is SOFR 2Y", \
"compare X to Y") that the SUPERVISOR routes to a domain specialist; \
they are NOT workflow-template requests.  A workflow template is a \
PRE-DEFINED MULTI-STEP DAG with a specific archetype shape (event \
study, regime-conditioned relationship).  Only return ``route`` or \
``clarify`` when the user's prompt CLEARLY MATCHES one of the \
template archetype signatures below.  When in doubt → \
``out_of_scope``.

1. Match the user's question to the BEST template by reading each \
template's ``description`` and ``archetype_signature`` cues.  Pick \
exactly ONE template_id from the catalogue.  Multiple cues = OR (any \
matching cue makes that template a candidate); if more than one \
template matches, pick the one whose archetype shape best fits the \
question.

2. Once you have picked a template, bind every required slot \
(``required: true``) from the user's words.  Slot value TYPES MUST \
match the slot_schema declaration (str / int / float / bool / dict / \
list).  For ``dict`` slots (every ``*_params`` slot), assemble the \
nested dict shape the corresponding primitive's *Input declaration \
above documents.  DO NOT REUSE field names from a different \
primitive's shape.

3. ALL curve_family / ois_curve_family / sovereign_curve_family / \
curve_family_1 / curve_family_2 values MUST be the canonical \
enum-style identifiers from the *Input descriptions \
(``UST``, ``DE_BUND``, ``UK_GILT``, ``IT_BTP``, ``FR_OAT``, \
``ES_BONO``, ``JGB``, ``CANADA_GOVT``, ``AU_GOVT``, ``USD_SOFR_OIS``, \
``EUR_ESTR_OIS``, ``GBP_SONIA_OIS``, ``JPY_OIS``, ``AUD_OIS``, \
``CAD_OIS``).  If the user wrote a short form like "BUND" or "GILT", \
translate it: BUND → ``DE_BUND``, GILT → ``UK_GILT``, BTP → \
``IT_BTP``, OAT → ``FR_OAT``, BONO → ``ES_BONO``, SOFR → \
``USD_SOFR_OIS``, ESTR → ``EUR_ESTR_OIS``, SONIA → \
``GBP_SONIA_OIS``, TONA → ``JPY_OIS``, AONIA → ``AUD_OIS``, CORRA → \
``CAD_OIS``.

4. If a required slot cannot be inferred from the user's words AND \
no defensible default exists, return action=``clarify`` with a \
focused follow-up question naming the missing slot.  Do not guess \
slot values — guessing produces silently-wrong workflow results.

5. If NO template in the catalogue fits the user's question, return \
action=``out_of_scope``.  ``out_of_scope`` is for questions a \
workflow template cannot answer (free-form chat, primitive-only \
questions like "what's SOFR 2Y trading at?", curve-spread / yield- \
level / regime-snapshot lookups, out-of-domain prompts).

5a. FOLLOW-UP DISCIPLINE.  When the prompt is a SHORT FOLLOW-UP \
referencing a prior turn (e.g. "what about X", "now for Y", \
"compare with Z", "the same for W") AND the RECENT CONVERSATION \
block above shows the prior turn was a PRIMITIVE-LEVEL response \
(curve spread, yield level, single-number answer with units), then \
the current follow-up is ALSO a primitive-level call.  Return \
``out_of_scope`` — the supervisor will route the follow-up to the \
appropriate domain specialist, which has full conversation context \
via the same RECENT CONVERSATION block.  DO NOT escalate the \
follow-up into a workflow template just because it references a \
prior turn.

5b. RECENT CONVERSATION USAGE.  The HUMAN message you receive may \
begin with a RECENT CONVERSATION block (prior turns of this \
session).  Use it only to disambiguate the current request — it \
does NOT change which actions are available.  A prior primitive \
turn does not "promote" a follow-up to a workflow request.

6. NEVER invent a template_id that is not in the catalogue.  NEVER \
invent slot names that are not in the chosen template's slot_schema.  \
NEVER invent primitive *Input field names that are not in the \
relevant primitive's shape above.  All three errors are caught \
downstream and force the decision to ``clarify`` — which costs the \
user an extra turn.

7. Set the ``rationale`` field to one short sentence (≤25 words) \
naming the specific cue from the user's prompt that drove your \
choice.  E.g. "user asks about average forward move when spread \
widens >1.5σ — matches event_study cue".  Logged for observability, \
not shown to the user.

8. For clarification, write the question the way a senior PM would \
write it — short, direct, names the missing slot.  Bad: "Could you \
perhaps tell me which curve family you mean?"  Good: "UST or Bund?"

CANONICAL DESK PHRASING THE LLM SHOULD RECOGNIZE

- Event-study cues: "average forward move when X exceeds Y", "when \
the spread WIDENED by more than Nσ", "5-day move after a Mσ event", \
"abnormal forward move", "event study around X moves above N sigma".

- Regime-conditioned-relationship cues: "rolling beta of X CHANGE to \
Y CHANGE in steepening vs flattening regimes", "how does the \
relationship between A and B differ across regimes", "compare beta \
distributions across high vs low regimes", "split-sample regression \
analysis by regime".

These cues map to template_ids ``event_study`` and \
``regime_conditioned_relationship`` respectively.  When in doubt, \
read the catalogue's ``archetype_signature`` cues — they are the \
template authors' own phrasing.
"""


# ===========================================================================
# CATALOGUE RENDERER
# ===========================================================================


def _render_one_card(card_dict: dict) -> str:
    """Render one TemplateCard as a compact, LLM-readable text block.

    The card is JSON-friendly (a dict from ``card.model_dump(mode=
    'json')``); the renderer formats the most-LLM-load-bearing fields
    inline + dumps the slot_schema as nested JSON the LLM can parse
    deterministically.  Same compact-JSON discipline as the
    synthesis-step formatter in ``orchestrator/supervisor.py``.
    """
    lines = [
        f"## template_id: {card_dict['template_id']}",
        f"archetype: {card_dict['archetype']}",
        f"description: {card_dict['description']}",
        f"terminal_artifact_type: {card_dict['terminal_artifact_type']}",
        f"node_count: {card_dict['node_count']}, "
        f"edge_count: {card_dict['edge_count']}",
    ]
    cues = card_dict.get("archetype_signature") or []
    if cues:
        lines.append("archetype_signature_cues:")
        for cue in cues:
            lines.append(f"  - {cue}")
    primitives = card_dict.get("primitives_used") or []
    if primitives:
        lines.append(f"primitives_used: {primitives}")
    operators = card_dict.get("operators_used") or []
    if operators:
        lines.append(f"operators_used: {operators}")
    # slot_schema as compact JSON so the LLM sees typed slots
    # unambiguously (name, type, required, description, default).
    schema = card_dict.get("slot_schema") or []
    lines.append("slot_schema (JSON):")
    lines.append(json.dumps(schema, indent=2))
    return "\n".join(lines)


def render_catalogue() -> str:
    """Render every registered template's card as a single string the
    workflow-router system prompt can embed.

    Templates are listed in ``template_id`` sort order so the prompt
    is deterministic across runs.  When zero templates are registered
    (e.g. the registry was cleared), the catalogue renders as an
    explicit empty marker so the LLM sees the registry is empty
    rather than a silently truncated prompt.
    """
    cards = [card_for_template(t) for t in list_templates()]
    if not cards:
        return "(no templates registered — catalogue is empty)"
    return "\n\n".join(
        _render_one_card(c.model_dump(mode="json")) for c in cards
    )


# ===========================================================================
# PRIMITIVE *Input SHAPES RENDERER
# ===========================================================================
#
# The catalogue tells the LLM which slots to bind, but each ``*_params``
# slot is a free-shape dict the consumed primitive's *Input validator
# accepts.  The LLM cannot infer those field names + valid values from
# the slot description alone — empirical: it reuses param names across
# primitives (e.g. wraps ``sovereign_curve_family`` from swap_spread
# into yield_levels' params, where yield_levels actually wants
# ``curve_family``) and mangles enum-style identifiers (``BUND`` vs
# ``DE_BUND``).  Rendering each primitive's *Input shape directly into
# the system prompt closes that gap.


def _format_primitive_input_shape(
    tool_name: str, spec: PrimitiveSpec,
) -> str:
    """Render one primitive's *Input shape as a compact text block.

    Each field becomes one line:
        - <field_name> : <type> [required|optional, default=<default>]
            <description (if any)>

    Type rendering uses the JSON-schema type tag the field's Pydantic
    annotation generates (str / int / float / bool / object / array)
    so the LLM sees a uniform vocabulary across primitives.
    """
    schema = spec.input_class.model_json_schema()
    properties = schema.get("properties") or {}
    required_set = set(schema.get("required") or ())

    lines: List[str] = [f"### {tool_name}"]
    if not properties:
        lines.append("  (no documented fields)")
        return "\n".join(lines)

    for field_name, field_schema in properties.items():
        type_label = _json_schema_type_label(field_schema)
        required_label = (
            "required" if field_name in required_set else "optional"
        )
        default_repr = ""
        if "default" in field_schema:
            default_value = field_schema["default"]
            default_repr = f", default={default_value!r}"
        line = (
            f"  - {field_name} : {type_label}  "
            f"[{required_label}{default_repr}]"
        )
        lines.append(line)
        description = field_schema.get("description")
        if description:
            # Indent the description on a continuation line so the
            # field-list shape is grep-able.
            lines.append(f"      {description}")
    return "\n".join(lines)


def _json_schema_type_label(field_schema: dict) -> str:
    """Map a JSON-schema field's type to a short label the LLM
    parses cleanly.  Handles plain types + ``anyOf`` (Optional[X]
    on Pydantic v2)."""
    if "type" in field_schema:
        return str(field_schema["type"])
    any_of = field_schema.get("anyOf")
    if any_of:
        type_tags = [
            t.get("type", "?") for t in any_of if isinstance(t, dict)
        ]
        # Drop "null" so ``Optional[X]`` shows as just "X".
        non_null = [t for t in type_tags if t != "null"]
        if non_null:
            return "|".join(non_null)
    return "any"


def render_primitive_input_shapes(
    primitive_resolver: Optional[PrimitiveResolver] = None,
    primitive_names: Optional[List[str]] = None,
) -> str:
    """Render every registered primitive's *Input shape as one
    LLM-readable text block.

    Sources the primitive list from the rates resolver by default;
    callers can pass an explicit resolver / list for tests or for
    future agent-level overrides.

    When the resolver / list is unavailable (e.g. test environments
    that import this module without rates_agent on the path), an
    explicit empty marker is emitted so the LLM sees the primitive
    section is empty rather than a silently-truncated prompt.
    """
    # Resolve the primitive list.  Importing rates_agent is
    # finance-aware — fine here because workflow_prompts.py is
    # already finance-aware (it embeds rates-specific cue text in
    # the routing rules).  An FX-agent fork would call this with
    # an FX resolver instead.
    if primitive_resolver is None or primitive_names is None:
        try:
            from rates_agent.workflows import (
                known_rates_primitives as _known,
                rates_primitive_resolver as _resolver,
            )
        except ImportError:
            return "(no primitive resolver available — primitive shapes section is empty)"
        if primitive_resolver is None:
            primitive_resolver = _resolver
        if primitive_names is None:
            primitive_names = _known()

    if not primitive_names:
        return "(no primitives registered — primitive shapes section is empty)"

    blocks: List[str] = []
    for name in primitive_names:
        try:
            spec = primitive_resolver(name)
        except Exception as exc:  # defensive: resolver may raise
            blocks.append(f"### {name}\n  (resolver error: {exc})")
            continue
        blocks.append(_format_primitive_input_shape(name, spec))
    return "\n\n".join(blocks)


def render_router_system_prompt() -> str:
    """Build the workflow-router system prompt with the live
    catalogue + primitive shapes substituted.  Called at
    WorkflowRouter construction time so each session sees the
    catalogue as it stood when the router was built (the registry
    is process-wide; in practice this is a one-time build at
    startup).

    The router is responsible for re-rendering if templates are
    hot-swapped at runtime — V1 doesn't expose a hot-swap path, so
    a one-shot render is correct.
    """
    catalogue_text = render_catalogue()
    primitive_shapes_text = render_primitive_input_shapes()
    return _WORKFLOW_ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
        catalogue=catalogue_text,
        primitive_shapes=primitive_shapes_text,
    )


__all__ = [
    "render_catalogue",
    "render_primitive_input_shapes",
    "render_router_system_prompt",
]

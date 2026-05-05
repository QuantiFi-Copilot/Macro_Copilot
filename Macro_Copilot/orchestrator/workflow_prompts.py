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
from typing import List

from shared.workflow import card_for_template, list_templates


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

ROUTING RULES

1. Match the user's question to the BEST template by reading each \
template's ``description`` and ``archetype_signature`` cues.  Pick \
exactly ONE template_id from the catalogue.  Multiple cues = OR (any \
matching cue makes that template a candidate); if more than one \
template matches, pick the one whose archetype shape best fits the \
question.

2. Once you have picked a template, bind every required slot \
(``required: true``) from the user's words.  Slot value TYPES MUST \
match the slot_schema declaration (str / int / float / bool / dict / \
list).  For ``dict`` slots, assemble the nested dict shape the slot's \
``description`` documents (typically the consumed primitive's \
*Input arg names — the description gives the canonical example).

3. If a required slot cannot be inferred from the user's words AND \
no defensible default exists, return action=``clarify`` with a \
focused follow-up question naming the missing slot.  Do not guess \
slot values — guessing produces silently-wrong workflow results.

4. If NO template in the catalogue fits the user's question, return \
action=``out_of_scope``.  ``out_of_scope`` is for questions a \
workflow template cannot answer (free-form chat, primitive-only \
questions like "what's SOFR 2Y trading at?", out-of-domain prompts).

5. NEVER invent a template_id that is not in the catalogue.  NEVER \
invent slot names that are not in the chosen template's slot_schema.  \
Both errors are caught downstream and force the decision to \
``clarify`` — which costs the user an extra turn.

6. Set the ``rationale`` field to one short sentence (≤25 words) \
naming the specific cue from the user's prompt that drove your \
choice.  E.g. "user asks about average forward move when spread \
widens >1.5σ — matches event_study cue".  Logged for observability, \
not shown to the user.

7. For clarification, write the question the way a senior PM would \
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


def render_router_system_prompt() -> str:
    """Build the workflow-router system prompt with the live
    catalogue substituted.  Called at WorkflowRouter construction
    time so each session sees the catalogue as it stood when the
    router was built (the registry is process-wide; in practice
    this is a one-time build at startup).

    The router is responsible for re-rendering if templates are
    hot-swapped at runtime — V1 doesn't expose a hot-swap path, so
    a one-shot render is correct.
    """
    catalogue_text = render_catalogue()
    return _WORKFLOW_ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
        catalogue=catalogue_text,
    )


__all__ = [
    "render_catalogue",
    "render_router_system_prompt",
]

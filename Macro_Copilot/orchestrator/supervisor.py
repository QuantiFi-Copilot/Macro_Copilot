"""
orchestrator/supervisor.py — Thin routing + synthesis layer
============================================================

The supervisor is the parent in the manager/supervisor pattern.  It has
TWO discrete jobs, each a single bounded Claude call:

1. ``route(user_message) -> RouteDecision``
   Decide which domain specialist(s) should handle the query.  Uses
   ``with_structured_output`` so the decision is deterministic JSON.

2. ``synthesize(user_message, [ChildResponse, ...]) -> AsyncIterator[str]``
   Combine structured outputs from multiple domain children into ONE
   PM-facing answer.  Only called on the multi-domain path.  Streams
   tokens so the WebSocket can forward them live.

Guardrails enforced at the code level (not just the prompt)
-----------------------------------------------------------
- The supervisor is constructed with NO MCP tools.  It physically cannot
  call a rates tool, because none are bound to its model.
- ``route`` returns a Pydantic object, not free-form text, so the
  supervisor cannot smuggle an answer into the routing step.
- ``synthesize`` is invoked with pre-formatted child facts.  It cannot
  query the database or invent numbers; the only numeric input it sees
  is what the children already computed.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, List, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from orchestrator.contracts import (
    ChildResponse,
    RouteAction,
    RouteDecision,
)
from orchestrator.prompts import (
    SUPERVISOR_SYSTEM_PROMPT,
    SYNTHESIS_SYSTEM_PROMPT,
)

logger = logging.getLogger("orchestrator.supervisor")


# ============================================================================
# CACHED SYSTEM MESSAGES
# ============================================================================
# Both prompts are static across the lifetime of a session, so we place a
# ``cache_control: ephemeral`` breakpoint on each.  Anthropic caches the
# prefix ``[tools] + [system]``; the changing user message is after the
# breakpoint and is not cached.
#
# The supervisor and synthesiser each get their own cache entry because
# they use different system prompts.  First call of each creates cache;
# subsequent calls read at 10% cost.

_CACHED_SUPERVISOR_SYSTEM = SystemMessage(
    content=[
        {
            "type": "text",
            "text": SUPERVISOR_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
)

_CACHED_SYNTHESIS_SYSTEM = SystemMessage(
    content=[
        {
            "type": "text",
            "text": SYNTHESIS_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
)


# ============================================================================
# USAGE LOGGING
# ============================================================================

def _log_usage(label: str, response) -> None:
    """Log token usage and cache hits from an Anthropic response.

    After caching is working correctly you should see:
      - First call (cold):  cache_create > 0, cache_read = 0
      - Later calls (warm): cache_create = 0, cache_read > 0
    """
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return
    if not isinstance(meta, dict):
        try:
            meta = dict(meta)
        except (TypeError, ValueError):
            return
    details = meta.get("input_token_details", {}) or {}
    logger.info(
        "[%s] tokens input=%s output=%s cache_create=%s cache_read=%s",
        label,
        meta.get("input_tokens"),
        meta.get("output_tokens"),
        details.get("cache_creation"),
        details.get("cache_read"),
    )


# ============================================================================
# SUPERVISOR
# ============================================================================

class Supervisor:
    """Route + synthesize layer.

    Constructed once per session.  Holds no tools, no MCP subprocesses,
    no domain knowledge beyond what's in its prompt.
    """

    def __init__(
        self,
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int = 1024,
    ):
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens

        # Base model — no tools bound.  This is the guardrail: the
        # supervisor cannot call a rates tool because none are attached.
        self._base_model = ChatAnthropic(
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Route step uses structured output.  ``include_raw=True`` lets us
        # log usage metadata from the raw AIMessage while still receiving
        # the parsed Pydantic object.
        self._route_model = self._base_model.with_structured_output(
            RouteDecision,
            include_raw=True,
        )

    # ------------------------------------------------------------------
    # ROUTE
    # ------------------------------------------------------------------

    async def route(self, user_message: str) -> RouteDecision:
        """Decide which domain(s) should answer the user's query.

        Returns a ``RouteDecision`` with:
          - action: single_domain / multi_domain / clarify
          - domains: the specialists to invoke (empty for clarify)
          - rationale: one-sentence log trail
          - clarification_question: populated only on clarify path
        """
        messages = [
            _CACHED_SUPERVISOR_SYSTEM,
            HumanMessage(content=user_message),
        ]

        try:
            result = await self._route_model.ainvoke(messages)
        except Exception:
            logger.exception("Supervisor route call failed")
            raise

        # include_raw=True returns {"raw": AIMessage, "parsed": RouteDecision,
        # "parsing_error": Optional[Exception]}
        raw = result.get("raw") if isinstance(result, dict) else None
        parsed: Optional[RouteDecision] = result.get("parsed") if isinstance(result, dict) else None
        parsing_error = result.get("parsing_error") if isinstance(result, dict) else None

        if raw is not None:
            _log_usage("supervisor.route", raw)

        if parsed is None:
            raise RuntimeError(
                f"Supervisor failed to produce a valid RouteDecision: "
                f"{parsing_error!r}"
            )

        # Sanity-check the decision so downstream code can trust it.
        parsed = _normalise_route_decision(parsed)

        logger.info(
            "Route: action=%s domains=%s rationale=%r",
            parsed.action.value,
            [d.value for d in parsed.domains],
            parsed.rationale,
        )
        return parsed

    # ------------------------------------------------------------------
    # SYNTHESIZE (streaming)
    # ------------------------------------------------------------------

    async def synthesize_stream(
        self,
        user_message: str,
        child_responses: List[ChildResponse],
    ) -> AsyncIterator[str]:
        """Stream tokens of the synthesised answer, given structured child
        outputs.

        The synthesis model receives the user's original question plus a
        compact JSON dump of each child's structured output (status, facts,
        answer_markdown, follow_up_question, etc.).  It cannot call tools.
        """
        child_payload = _format_children_for_synthesis(child_responses)

        human_content = (
            "Original user question:\n"
            f"{user_message}\n\n"
            "Structured outputs from domain specialists:\n"
            f"{child_payload}\n\n"
            "Write the final answer for the user now."
        )

        messages = [
            _CACHED_SYNTHESIS_SYSTEM,
            HumanMessage(content=human_content),
        ]

        accumulated_text: list[str] = []
        final_chunk = None

        async for chunk in self._base_model.astream(messages):
            final_chunk = chunk
            text_piece = _extract_text_from_chunk(chunk)
            if text_piece:
                accumulated_text.append(text_piece)
                yield text_piece

        # The final streamed chunk carries cumulative usage metadata on
        # langchain-anthropic; log it for observability.
        if final_chunk is not None:
            _log_usage("supervisor.synthesize", final_chunk)


# ============================================================================
# HELPERS
# ============================================================================

def _normalise_route_decision(decision: RouteDecision) -> RouteDecision:
    """Enforce internal consistency between action and domains.

    The model occasionally returns an action/domains mismatch (e.g.
    single_domain with two domains, or multi_domain with duplicates like
    [ois, ois]).  We normalise rather than reject so a near-correct
    routing choice still produces a usable decision.

    Every rewrite is both logged AND recorded on the returned
    ``RouteDecision.adjustments`` list so it becomes visible to eval
    harnesses and debug panels via the ``route_decision`` streaming
    event.  If the adjustments list is consistently non-empty in
    production, that's the signal the supervisor prompt needs tuning.

    Order of operations matters: we deduplicate FIRST so that
    ``[ois, ois]`` collapses to ``[ois]`` before we decide whether it's
    a legitimate multi_domain request.  Otherwise a duplicated list
    survives the length check and we'd pay synthesis overhead (and
    suppress tokens) for what is effectively a single-domain query.
    """
    action = decision.action
    domains = list(decision.domains)
    # ``adjustments`` is a post-hoc, code-generated field.  Even though
    # the Pydantic field description tells the LLM not to populate it,
    # structured output can still surface whatever the model wrote.
    # Discard anything the LLM supplied here — only real normalization
    # notes from THIS function should appear downstream.
    adjustments: list[str] = []

    # ------------------------------------------------------------------
    # Step 1: deduplicate, preserving order.
    # ------------------------------------------------------------------
    seen: set = set()
    deduped: list = []
    for d in domains:
        if d not in seen:
            seen.add(d)
            deduped.append(d)
    if len(deduped) != len(domains):
        note = (
            f"deduplicated domains {[d.value for d in domains]} -> "
            f"{[d.value for d in deduped]}"
        )
        logger.warning("Supervisor: %s", note)
        adjustments.append(note)
    domains = deduped

    # ------------------------------------------------------------------
    # Step 2: reconcile action with the (deduped) domain count.
    # ------------------------------------------------------------------
    if action == RouteAction.SINGLE_DOMAIN and len(domains) != 1:
        if len(domains) > 1:
            note = (
                f"single_domain with {len(domains)} domains; "
                f"keeping first: {domains[0].value}"
            )
            logger.warning("Supervisor: %s", note)
            adjustments.append(note)
            domains = [domains[0]]
        else:
            note = "single_domain with no domains; promoting to clarify"
            logger.warning("Supervisor: %s", note)
            adjustments.append(note)
            action = RouteAction.CLARIFY
            domains = []

    if action == RouteAction.MULTI_DOMAIN and len(domains) < 2:
        if len(domains) == 1:
            note = (
                f"multi_domain with 1 unique domain ({domains[0].value}) "
                f"after dedup; demoting to single_domain"
            )
            logger.warning("Supervisor: %s", note)
            adjustments.append(note)
            action = RouteAction.SINGLE_DOMAIN
        else:
            note = "multi_domain with no domains; demoting to clarify"
            logger.warning("Supervisor: %s", note)
            adjustments.append(note)
            action = RouteAction.CLARIFY

    if action == RouteAction.CLARIFY and domains:
        # Clarify path should not carry domains; null them out for clarity.
        note = f"clarify with domains {[d.value for d in domains]}; clearing"
        logger.warning("Supervisor: %s", note)
        adjustments.append(note)
        domains = []

    return RouteDecision(
        action=action,
        domains=domains,
        rationale=decision.rationale,
        clarification_question=decision.clarification_question,
        adjustments=adjustments,
    )


def _format_children_for_synthesis(children: List[ChildResponse]) -> str:
    """Render children's structured outputs as a compact JSON document the
    synthesis model can consume without ambiguity.

    We hand-format rather than calling ``model_dump_json`` so the output is
    stable and readable for the LLM (sorted keys, no Pydantic-specific
    metadata leaks).
    """
    payload = []
    for c in children:
        payload.append(
            {
                "domain": c.domain.value,
                "status": c.status.value,
                "answer_markdown": c.answer_markdown,
                "facts": [f.model_dump(exclude_none=True) for f in c.facts],
                "follow_up_question": c.follow_up_question,
                "error_message": c.error_message,
                "tool_trace": [
                    {"tool": t.tool, "params": t.params, "error": t.error}
                    for t in c.tool_trace
                ],
            }
        )
    return json.dumps(payload, indent=2, default=str)


def _extract_text_from_chunk(chunk) -> str:
    """Pull plain text out of a streaming chunk regardless of whether
    ``chunk.content`` is a string or a list of content blocks."""
    content = getattr(chunk, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(block.get("text", ""))
            elif isinstance(block, str):
                out.append(block)
        return "".join(out)
    return ""

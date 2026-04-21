"""
orchestrator/session.py — Copilot Session Orchestrator
=========================================================

The top-level session lifecycle.  Composes the three layers of the
manager/supervisor architecture:

    User  ↔  CopilotSession
                  │
                  ├─► Supervisor (no tools; picks domain(s); synthesises)
                  │
                  └─► Child domain agents (own tools; own tool loop)
                          ├─► DomainAgentSession(sovereign_bonds)
                          └─► DomainAgentSession(ois)

Public API (preserved from the pre-supervisor implementation so that
``api/routes/chat.py`` and ``orchestrator/graph.py`` keep working):

    async with CopilotSession(thread_id=..., stateless=True) as s:
        async for event in s.stream("What's UST 2s10s?"):
            ...
        result = await s.invoke("Where is SOFR 2Y?")
        #  result = {"content": "...", "tool_calls": [...],
        #            "workspace_context": {...}|None, "messages": []}

Streaming event contract — the frontend receives these event types in
order per user turn:

    status            {"status": "routing"}
    route_decision    {"action", "domains", "rationale"}
    status            {"status": "thinking"}          # single-domain or first fan-out
    child_started     {"domain"}
    tool_call         {"tool", "label", "params", "domain"}
    tool_result       {"tool", "domain", "duration_ms"}
    token             {"content"}                     # streams child prose in single-domain,
                                                      #   OR synthesis prose in multi-domain
    child_finished    {"domain", "status", "duration_ms"}
    synthesis_started {}                              # only in multi-domain path
    clarification     {"question"}                    # only in clarify path
    done              {"workspace_context", "tool_calls", "total_duration_ms"}
    error             {"message"}

Cost discipline
---------------
- Three distinct LLM system prompts are cached with ephemeral breakpoints:
  supervisor route, each domain child, and synthesis.  First call of each
  creates cache; subsequent calls in the session read at 10% cost.
- Stateless turns (default) give each user turn a unique LangGraph
  ``thread_id`` per child so conversation history doesn't accumulate in
  the checkpointer.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import AsyncIterator, Optional

from orchestrator.config import (
    DOMAIN_MCP_SERVERS,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
)
from orchestrator.contracts import (
    ChildResponse,
    ChildStatus,
    Domain,
    RouteAction,
    RouteDecision,
)
from orchestrator.domain_agent import DomainAgentSession
from orchestrator.events import SessionEvent, extract_workspace_context
from orchestrator.prompts import (
    OIS_SYSTEM_PROMPT,
    SOVEREIGN_BONDS_SYSTEM_PROMPT,
)
from orchestrator.supervisor import Supervisor

logger = logging.getLogger("orchestrator.session")


# ============================================================================
# DOMAIN → SYSTEM PROMPT MAPPING
# ============================================================================
# Single source of truth for which prompt each child agent uses.  Adding a
# new domain = add entry here + in DOMAIN_MCP_SERVERS + in contracts.Domain.

_DOMAIN_PROMPTS: dict[Domain, str] = {
    Domain.SOVEREIGN_BONDS: SOVEREIGN_BONDS_SYSTEM_PROMPT,
    Domain.OIS: OIS_SYSTEM_PROMPT,
}


# ============================================================================
# COPILOT SESSION
# ============================================================================

class CopilotSession:
    """One copilot conversation session.

    Owns:
      - one Supervisor (no tools)
      - one DomainAgentSession per domain (each with its own MCP subprocess)

    Parameters
    ----------
    thread_id : Optional[str]
        Session id.  Auto-generated if omitted.
    stateless : bool, default True
        When True, each user turn gets a unique checkpointer thread_id
        per child so prior history doesn't accumulate in context.  Set
        False only for future "intelligence mode" multi-turn work.
    """

    def __init__(self, thread_id: Optional[str] = None, stateless: bool = True):
        self.thread_id = thread_id or f"ws-{uuid.uuid4().hex[:12]}"
        self.stateless = stateless
        self._turn_counter = 0

        self._supervisor: Supervisor | None = None
        self._children: dict[Domain, DomainAgentSession] = {}
        self._is_open = False

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "CopilotSession":
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Build the supervisor and spawn all domain children concurrently.

        Running the children's ``open()`` in parallel cuts startup latency
        roughly in half (two MCP subprocesses spawn simultaneously instead
        of sequentially).
        """
        if self._is_open:
            return

        logger.info("[%s] opening copilot session", self.thread_id)

        self._supervisor = Supervisor(
            model_name=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )

        for domain in Domain:
            if domain not in DOMAIN_MCP_SERVERS:
                logger.warning(
                    "[%s] domain %s has no MCP server config; skipping.",
                    self.thread_id,
                    domain.value,
                )
                continue
            if domain not in _DOMAIN_PROMPTS:
                logger.warning(
                    "[%s] domain %s has no system prompt; skipping.",
                    self.thread_id,
                    domain.value,
                )
                continue
            self._children[domain] = DomainAgentSession(
                domain=domain,
                system_prompt=_DOMAIN_PROMPTS[domain],
                mcp_servers=DOMAIN_MCP_SERVERS[domain],
                model_name=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
            )

        if not self._children:
            raise RuntimeError("No domain children could be constructed.")

        # Open all children in parallel.  If any fails, close those that
        # succeeded before propagating the error.
        open_tasks = [child.open() for child in self._children.values()]
        results = await asyncio.gather(*open_tasks, return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            logger.error(
                "[%s] %d child(ren) failed to open; closing those that succeeded.",
                self.thread_id,
                len(failures),
            )
            await self._close_children_quiet()
            raise failures[0]

        self._is_open = True
        logger.info(
            "[%s] session ready with domains: %s",
            self.thread_id,
            [d.value for d in self._children.keys()],
        )

    async def close(self) -> None:
        """Shut down all child MCP subprocesses."""
        if not self._is_open:
            return

        logger.info("[%s] closing session", self.thread_id)
        await self._close_children_quiet()
        self._children = {}
        self._supervisor = None
        self._is_open = False

    async def _close_children_quiet(self) -> None:
        """Close children in parallel, swallowing individual failures."""
        if not self._children:
            return
        close_tasks = [child.close() for child in self._children.values()]
        await asyncio.gather(*close_tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Non-streaming invoke (used by the CLI REPL)
    # ------------------------------------------------------------------

    async def invoke(self, user_message: str) -> dict:
        """Drain a single turn's event stream into a result dict.

        Returns
        -------
        dict
            Keys:
              - ``content``            final answer string for the user
              - ``tool_calls``         list of {tool, domain, duration_ms}
              - ``workspace_context``  dict or None
              - ``messages``           always [] in the new architecture;
                                       retained for legacy callers
              - ``route``              dict with action / domains / rationale
              - ``clarification``      str if route=clarify else None
        """
        content_parts: list[str] = []
        tool_calls: list[dict] = []
        workspace_context: Optional[dict] = None
        route_info: Optional[dict] = None
        clarification: Optional[str] = None

        async for event in self.stream(user_message):
            if event.type == "token":
                piece = event.data.get("content", "")
                if piece:
                    content_parts.append(piece)
            elif event.type == "tool_result":
                tool_calls.append(
                    {
                        "tool": event.data.get("tool"),
                        "domain": event.data.get("domain"),
                        "duration_ms": event.data.get("duration_ms"),
                    }
                )
            elif event.type == "route_decision":
                route_info = dict(event.data)
            elif event.type == "clarification":
                clarification = event.data.get("question")
            elif event.type == "done":
                workspace_context = event.data.get("workspace_context")
            elif event.type == "error":
                # Surface the error in the content so the CLI sees it.
                content_parts.append(
                    f"\n[ERROR] {event.data.get('message', 'unknown error')}"
                )

        return {
            "content": "".join(content_parts).strip(),
            "tool_calls": tool_calls,
            "workspace_context": workspace_context,
            "messages": [],
            "route": route_info,
            "clarification": clarification,
        }

    # ------------------------------------------------------------------
    # Streaming (used by the WebSocket)
    # ------------------------------------------------------------------

    async def stream(self, user_message: str) -> AsyncIterator[SessionEvent]:
        """Stream all events for one user turn through a single ordered
        channel.

        The pipeline runs in a background task and pushes events into an
        ``asyncio.Queue``; this method yields them to the caller in
        arrival order.  The queue serialises emissions from concurrent
        children during multi-domain fan-out, which is important because
        a WebSocket cannot have concurrent writes.
        """
        if not self._is_open:
            raise RuntimeError("Session is not open.")
        if self._supervisor is None:
            raise RuntimeError("Supervisor not initialised.")

        self._turn_counter += 1
        turn_label = f"turn-{self._turn_counter}"

        queue: asyncio.Queue = asyncio.Queue()
        _SENTINEL = object()

        async def emit(event: SessionEvent) -> None:
            await queue.put(event)

        async def pipeline() -> None:
            total_start = time.monotonic()
            try:
                await self._run_turn(user_message, turn_label, emit)
            except Exception as exc:
                logger.exception(
                    "[%s] turn %s pipeline error", self.thread_id, turn_label
                )
                await emit(
                    SessionEvent(type="error", data={"message": str(exc)})
                )
            finally:
                total_ms = round((time.monotonic() - total_start) * 1000)
                logger.info(
                    "[%s] turn %s complete in %dms",
                    self.thread_id,
                    turn_label,
                    total_ms,
                )
                await queue.put(_SENTINEL)

        task = asyncio.create_task(pipeline())

        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
        finally:
            # Ensure the pipeline task is awaited even if the consumer
            # stops early (e.g. WebSocket disconnect).
            if not task.done():
                try:
                    await task
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Internal turn pipeline
    # ------------------------------------------------------------------

    async def _run_turn(
        self,
        user_message: str,
        turn_label: str,
        emit,
    ) -> None:
        """End-to-end orchestration for one user turn.

        Steps:
          1. emit ``status=routing`` and get the RouteDecision from the
             supervisor.
          2. emit ``route_decision`` with action/domains/rationale.
          3. Branch: clarify, single_domain, or multi_domain.
          4. emit ``done`` with workspace_context and tool_calls from
             whichever children ran.
        """
        turn_start = time.monotonic()

        # ------------------------------------------------------------------
        # 1. Supervisor routing
        # ------------------------------------------------------------------
        await emit(SessionEvent(type="status", data={"status": "routing"}))

        try:
            decision: RouteDecision = await self._supervisor.route(user_message)
        except Exception as exc:
            logger.exception(
                "[%s] %s supervisor route failed", self.thread_id, turn_label
            )
            await emit(
                SessionEvent(
                    type="error",
                    data={"message": f"Routing failed: {exc}"},
                )
            )
            await emit(
                SessionEvent(
                    type="done",
                    data={
                        "workspace_context": None,
                        "tool_calls": [],
                        "total_duration_ms": round(
                            (time.monotonic() - turn_start) * 1000
                        ),
                    },
                )
            )
            return

        await emit(
            SessionEvent(
                type="route_decision",
                data={
                    "action": decision.action.value,
                    "domains": [d.value for d in decision.domains],
                    "rationale": decision.rationale,
                },
            )
        )

        # ------------------------------------------------------------------
        # 2. Branch by action
        # ------------------------------------------------------------------
        if decision.action == RouteAction.CLARIFY:
            question = (
                decision.clarification_question
                or "Could you clarify which market you're asking about?"
            )
            await emit(
                SessionEvent(type="clarification", data={"question": question})
            )
            # Also stream the question as tokens so the chat UI renders it
            # as a normal assistant reply.
            await emit(SessionEvent(type="token", data={"content": question}))
            await emit(
                SessionEvent(
                    type="done",
                    data={
                        "workspace_context": None,
                        "tool_calls": [],
                        "total_duration_ms": round(
                            (time.monotonic() - turn_start) * 1000
                        ),
                    },
                )
            )
            return

        # Validate that every routed domain has an open child.
        missing = [d for d in decision.domains if d not in self._children]
        if missing:
            msg = (
                f"Routing selected domain(s) with no open child: "
                f"{[d.value for d in missing]}"
            )
            logger.error("[%s] %s %s", self.thread_id, turn_label, msg)
            await emit(SessionEvent(type="error", data={"message": msg}))
            await emit(
                SessionEvent(
                    type="done",
                    data={
                        "workspace_context": None,
                        "tool_calls": [],
                        "total_duration_ms": round(
                            (time.monotonic() - turn_start) * 1000
                        ),
                    },
                )
            )
            return

        if decision.action == RouteAction.SINGLE_DOMAIN:
            await self._run_single_domain(
                user_message=user_message,
                domain=decision.domains[0],
                turn_label=turn_label,
                turn_start=turn_start,
                emit=emit,
            )
            return

        # MULTI_DOMAIN
        await self._run_multi_domain(
            user_message=user_message,
            domains=decision.domains,
            turn_label=turn_label,
            turn_start=turn_start,
            emit=emit,
        )

    # ------------------------------------------------------------------
    # Single-domain branch
    # ------------------------------------------------------------------

    async def _run_single_domain(
        self,
        *,
        user_message: str,
        domain: Domain,
        turn_label: str,
        turn_start: float,
        emit,
    ) -> None:
        """Run one child with streaming tokens forwarded straight to the
        user.  This is the fast path — no synthesis, no double narration.

        Also guarantees the user sees *something* even when the child
        fails before streaming: on ERROR we emit both an explicit error
        event and a user-facing token so the chat doesn't go silent.
        """
        await emit(SessionEvent(type="status", data={"status": "thinking"}))

        child = self._children[domain]
        await emit(
            SessionEvent(
                type="child_started",
                data={"domain": domain.value},
            )
        )

        child_thread_id = self._child_thread_id(turn_label, domain)
        child_start = time.monotonic()

        # Wrap emit so we can tell after the run whether the child
        # actually streamed any token to the user.  If it errored before
        # streaming, we need to synthesise a fallback message so the
        # chat isn't silent.
        tokens_emitted = False

        async def tracked_emit(event: SessionEvent) -> None:
            nonlocal tokens_emitted
            if event.type == "token" and event.data.get("content"):
                tokens_emitted = True
            await emit(event)

        child_response: ChildResponse = await child.run(
            user_message=user_message,
            domain_boundary=None,  # no boundary in single-domain
            emit_tokens=True,      # child's prose IS the user-facing answer
            on_event=tracked_emit,
            turn_thread_id=child_thread_id,
        )

        child_duration_ms = round((time.monotonic() - child_start) * 1000)

        # --------------------------------------------------------------
        # Handle non-OK child statuses so the frontend never goes silent.
        # --------------------------------------------------------------
        if child_response.status == ChildStatus.ERROR:
            error_msg = (
                child_response.error_message
                or f"The {domain.value} specialist failed to complete the request."
            )
            logger.warning(
                "[%s] %s child error: %s", self.thread_id, turn_label, error_msg
            )
            await emit(
                SessionEvent(type="error", data={"message": error_msg})
            )
            if not tokens_emitted:
                # Give the user a visible assistant message too.
                await emit(
                    SessionEvent(
                        type="token",
                        data={
                            "content": (
                                f"The {domain.value} specialist ran into a "
                                f"problem and couldn't complete this request: "
                                f"{error_msg}"
                            )
                        },
                    )
                )
        elif child_response.status == ChildStatus.NEEDS_CLARIFICATION:
            question = (
                child_response.follow_up_question
                or "Could you clarify what you're asking about?"
            )
            await emit(
                SessionEvent(type="clarification", data={"question": question})
            )
            if not tokens_emitted:
                await emit(
                    SessionEvent(type="token", data={"content": question})
                )
        elif child_response.status == ChildStatus.OUT_OF_SCOPE:
            # The child's prose already explains the scope issue and was
            # streamed live to the user.  Nothing extra to emit; log for
            # observability so we can track misroutes.
            logger.info(
                "[%s] %s child flagged out_of_scope for domain=%s",
                self.thread_id, turn_label, domain.value,
            )
            if not tokens_emitted:
                # Very unusual: out_of_scope detected without any streamed
                # tokens.  Emit the stored answer_markdown as a fallback.
                fallback = child_response.answer_markdown or (
                    f"That question is outside the {domain.value} domain."
                )
                await emit(
                    SessionEvent(type="token", data={"content": fallback})
                )

        await emit(
            SessionEvent(
                type="child_finished",
                data={
                    "domain": domain.value,
                    "status": child_response.status.value,
                    "duration_ms": child_duration_ms,
                },
            )
        )

        # Done
        await emit(
            SessionEvent(
                type="done",
                data={
                    "workspace_context": child_response.workspace_context,
                    "tool_calls": [
                        {
                            "tool": t.tool,
                            "domain": domain.value,
                            "duration_ms": t.duration_ms,
                        }
                        for t in child_response.tool_trace
                    ],
                    "total_duration_ms": round(
                        (time.monotonic() - turn_start) * 1000
                    ),
                },
            )
        )

    # ------------------------------------------------------------------
    # Multi-domain branch
    # ------------------------------------------------------------------

    async def _run_multi_domain(
        self,
        *,
        user_message: str,
        domains: list[Domain],
        turn_label: str,
        turn_start: float,
        emit,
    ) -> None:
        """Fan out to multiple children in parallel, then synthesise.

        Child token streams are SUPPRESSED at the user level
        (emit_tokens=False): the user sees only the supervisor's
        synthesis, not each child's internal prose.  Tool calls and
        status events from children are still forwarded so the execution
        trace stays visible.
        """
        await emit(SessionEvent(type="status", data={"status": "thinking"}))

        # Build the domain-boundary hint for each child.  Tells the child
        # to stay in its lane and leave other domains to their specialists.
        boundaries = _build_domain_boundaries(domains)

        # Emit child_started for all upfront so the UI can show parallel
        # execution indicators.
        for domain in domains:
            await emit(
                SessionEvent(type="child_started", data={"domain": domain.value})
            )

        async def run_one(domain: Domain):
            child = self._children[domain]
            start = time.monotonic()
            response = await child.run(
                user_message=user_message,
                domain_boundary=boundaries.get(domain),
                emit_tokens=False,   # suppress to user; synthesis is what they see
                on_event=emit,       # tool events still forwarded
                turn_thread_id=self._child_thread_id(turn_label, domain),
            )
            duration_ms = round((time.monotonic() - start) * 1000)
            return domain, response, duration_ms

        child_tasks = [run_one(d) for d in domains]
        completed = await asyncio.gather(*child_tasks, return_exceptions=True)

        child_responses: list[ChildResponse] = []
        tool_call_summary: list[dict] = []
        workspace_parts: list[dict] = []

        for item in completed:
            if isinstance(item, BaseException):
                # A child raised.  Emit an error event and move on; the
                # synthesis step will deal with missing children.
                logger.exception(
                    "[%s] %s child run raised", self.thread_id, turn_label
                )
                await emit(
                    SessionEvent(
                        type="error",
                        data={"message": f"Child agent error: {item}"},
                    )
                )
                continue

            domain, response, duration_ms = item
            await emit(
                SessionEvent(
                    type="child_finished",
                    data={
                        "domain": domain.value,
                        "status": response.status.value,
                        "duration_ms": duration_ms,
                    },
                )
            )
            child_responses.append(response)
            for t in response.tool_trace:
                tool_call_summary.append(
                    {
                        "tool": t.tool,
                        "domain": domain.value,
                        "duration_ms": t.duration_ms,
                        "params": t.params,
                    }
                )
            if response.workspace_context:
                workspace_parts.append(response.workspace_context)

        # --------------------------------------------------------------
        # Synthesis
        # --------------------------------------------------------------
        if not child_responses:
            await emit(
                SessionEvent(
                    type="error",
                    data={"message": "All domain children failed."},
                )
            )
            await emit(
                SessionEvent(
                    type="done",
                    data={
                        "workspace_context": None,
                        "tool_calls": tool_call_summary,
                        "total_duration_ms": round(
                            (time.monotonic() - turn_start) * 1000
                        ),
                    },
                )
            )
            return

        await emit(SessionEvent(type="synthesis_started", data={}))
        await emit(SessionEvent(type="status", data={"status": "synthesising"}))

        try:
            async for piece in self._supervisor.synthesize_stream(
                user_message=user_message,
                child_responses=child_responses,
            ):
                if piece:
                    await emit(
                        SessionEvent(type="token", data={"content": piece})
                    )
        except Exception as exc:
            logger.exception(
                "[%s] %s synthesis failed", self.thread_id, turn_label
            )
            await emit(
                SessionEvent(
                    type="error",
                    data={"message": f"Synthesis failed: {exc}"},
                )
            )

        # Done
        workspace_context = _merge_workspace_contexts(workspace_parts)
        await emit(
            SessionEvent(
                type="done",
                data={
                    "workspace_context": workspace_context,
                    "tool_calls": [
                        {k: v for k, v in tc.items() if k != "params"}
                        for tc in tool_call_summary
                    ],
                    "total_duration_ms": round(
                        (time.monotonic() - turn_start) * 1000
                    ),
                },
            )
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _child_thread_id(self, turn_label: str, domain: Domain) -> str:
        """Generate a per-(turn, domain) thread id for the child's
        LangGraph checkpointer.  In stateless mode this is unique per
        turn; otherwise it's shared per domain across turns."""
        if self.stateless:
            return f"{self.thread_id}-{turn_label}-{domain.value}"
        return f"{self.thread_id}-{domain.value}"


# ============================================================================
# MODULE HELPERS
# ============================================================================

def _build_domain_boundaries(domains: list[Domain]) -> dict[Domain, str]:
    """For each domain in a multi-domain fan-out, build a short scope
    instruction that tells the child which part of the user's compound
    query it should answer and which parts to leave to its siblings.
    """
    domain_labels = {
        Domain.SOVEREIGN_BONDS: "cash sovereign bonds",
        Domain.OIS: "OIS swaps",
    }
    out: dict[Domain, str] = {}
    for d in domains:
        self_label = domain_labels.get(d, d.value)
        others = [
            domain_labels.get(x, x.value) for x in domains if x is not d
        ]
        others_phrase = " / ".join(others) if others else "other domains"
        out[d] = (
            f"This is a multi-domain query. Answer ONLY the portions "
            f"relevant to {self_label}. Leave the {others_phrase} "
            f"portion(s) to the other specialist(s); a synthesis step "
            f"will combine the outputs."
        )
    return out


def _merge_workspace_contexts(parts: list[dict]) -> Optional[dict]:
    """Combine per-child workspace_context dicts into one.  Each child
    contributes a list under the ``tools`` key; we concatenate preserving
    domain attribution."""
    if not parts:
        return None
    merged_tools: list = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        tools = p.get("tools")
        if isinstance(tools, list):
            merged_tools.extend(tools)
    # Re-compute via extract_workspace_context semantics for consistency.
    merged = extract_workspace_context(merged_tools) if merged_tools else None
    return merged

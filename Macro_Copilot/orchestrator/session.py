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
from typing import TYPE_CHECKING, AsyncIterator, Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from orchestrator.config import (
    DOMAIN_MCP_SERVERS,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool
    from sqlalchemy.engine import Engine

    from orchestrator.reference_resolver import (
        ReferenceResolution,
        ReferenceResolver,
    )
    from orchestrator.state import TurnContext
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
    render_working_set_block,
)
from orchestrator.supervisor import Supervisor
from orchestrator.workflow_contracts import (
    WorkflowExecutionResult,
    WorkflowRouteAction,
    WorkflowRouteDecision,
)
from orchestrator.workflow_router import WorkflowRouter

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
    stateless : bool, default False
        When True, each user turn gets a unique checkpointer thread_id
        per child (legacy behavior preserved for tests + CLI) AND the
        session uses an in-memory ``MemorySaver`` regardless of
        ``checkpointer_pool``.  When False (the new default after
        Phase 0 PR 5), conversation state persists across turns within
        a session — and if a ``checkpointer_pool`` is provided, it
        persists across server restarts as well.
    checkpointer_pool : Optional[AsyncConnectionPool]
        psycopg3 async connection pool to back the LangGraph
        checkpointer.  When provided AND ``stateless=False``, the
        session uses ``AsyncPostgresSaver(pool)`` so conversation
        state lives in the ``langgraph_checkpoint`` Postgres schema
        and survives Python process restarts.  When ``None``, the
        session uses ``MemorySaver`` (in-process; lost on restart).
        Tests that don't need durability omit this; the production
        WebSocket path injects it from
        ``api.dependencies.get_checkpointer_pool()``.

    Checkpointer matrix
    -------------------
    +-----------+--------------+--------------+------------------+----------------+
    | stateless | pool         | thread_id    | checkpointer     | survives       |
    |           |              |              |                  | restart?       |
    +===========+==============+==============+==================+================+
    | True      | any          | per-turn     | MemorySaver      | no             |
    +-----------+--------------+--------------+------------------+----------------+
    | False     | None         | per-session  | MemorySaver      | no (in-proc    |
    |           |              | stable       |                  | only)          |
    +-----------+--------------+--------------+------------------+----------------+
    | False     | provided     | per-session  | AsyncPostgres-   | YES            |
    |           |              | stable       | Saver(pool)      |                |
    +-----------+--------------+--------------+------------------+----------------+
    """

    def __init__(
        self,
        thread_id: Optional[str] = None,
        stateless: bool = False,
        checkpointer_pool: Optional["AsyncConnectionPool"] = None,
        session_id: Optional[uuid.UUID] = None,
        engine: Optional["Engine"] = None,
    ):
        self.thread_id = thread_id or f"ws-{uuid.uuid4().hex[:12]}"
        self.stateless = stateless
        self._checkpointer_pool = checkpointer_pool
        self._turn_counter = 0

        # Phase 0 PR 8: persistent turn lifecycle.  When ``engine`` is
        # provided AND ``stateless`` is False, every turn is bracketed
        # by ``begin_turn`` / ``commit_turn`` against the
        # ``copilot_state.turns`` table; the resolver runs at the top
        # of each turn against the visible working-set names.  When
        # ``engine`` is None (the test / CLI path) we skip the DB
        # lifecycle entirely — tests that don't spin up Postgres can
        # still construct a CopilotSession.
        self._session_id: Optional[uuid.UUID] = session_id
        self._engine: Optional["Engine"] = engine
        self._resolver: Optional["ReferenceResolver"] = None

        self._supervisor: Supervisor | None = None
        # PR 10: workflow router parallel to the supervisor.  Built at
        # ``open()`` time alongside the supervisor.  Gates every turn
        # BEFORE the supervisor so a prompt that fits a registered
        # workflow template (event_study, regime_conditioned_relationship)
        # routes to the workflow path instead of the per-domain ReAct
        # path.  When the workflow router returns OUT_OF_SCOPE (e.g. a
        # primitive-only question like "where's SOFR 2Y?"), the turn
        # falls through to the existing supervisor flow unchanged.
        self._workflow_router: WorkflowRouter | None = None
        self._children: dict[Domain, DomainAgentSession] = {}
        # Per-domain async locks serialise concurrent "open" requests for
        # the same child.  Child objects are constructed eagerly at
        # session open time but MCP subprocesses are spawned lazily — on
        # first use of each domain.  This keeps session startup cheap as
        # the domain roster grows from 2 toward ~10.
        self._child_open_locks: dict[Domain, asyncio.Lock] = {}
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
        """Build the supervisor and construct (but do not open) the
        domain children.

        Session startup is intentionally cheap: we only instantiate the
        ``Supervisor`` and construct ``DomainAgentSession`` objects plus
        their async locks.  MCP subprocesses are NOT spawned until a
        user query actually routes to that domain (see
        ``_ensure_child_open``).  This keeps startup time O(1) in the
        number of registered domains and avoids paying for subprocesses
        that may never be used in a given session.
        """
        if self._is_open:
            return

        logger.info("[%s] opening copilot session (lazy children)", self.thread_id)

        self._supervisor = Supervisor(
            model_name=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )

        # Phase 0 PR 8: reference resolver.  Same model / cache prefix
        # as the supervisor; small max_tokens because the output is a
        # two-field structured record.  Created unconditionally so the
        # resolver-failure path stays simple — if there's no engine,
        # we just never call it.
        try:
            from orchestrator.reference_resolver import ReferenceResolver

            self._resolver = ReferenceResolver(
                model_name=LLM_MODEL,
                temperature=LLM_TEMPERATURE,
                max_tokens=256,
            )
        except Exception as exc:
            logger.warning(
                "[%s] failed to build reference resolver; turns will "
                "skip working-set NL resolution: %s",
                self.thread_id, exc,
            )
            self._resolver = None

        # PR 10: workflow router.  Constructing it triggers the rendered
        # template-catalogue + primitive-shape system prompt build (one
        # static prefix, cache-eligible).  Importing the registered
        # template packages here makes sure the substrate's process-wide
        # template registry is populated before the router renders its
        # catalogue.
        try:
            import rates_agent.workflows.event_study  # noqa: F401
            import rates_agent.workflows.regime_conditioned_relationship  # noqa: F401
        except Exception as exc:
            logger.warning(
                "[%s] failed to import workflow templates; workflow "
                "routing disabled for this session: %s",
                self.thread_id, exc,
            )
        else:
            try:
                self._workflow_router = WorkflowRouter(
                    model_name=LLM_MODEL,
                    temperature=LLM_TEMPERATURE,
                    max_tokens=LLM_MAX_TOKENS,
                )
                logger.info(
                    "[%s] workflow router ready (templates registered)",
                    self.thread_id,
                )
            except Exception as exc:
                logger.warning(
                    "[%s] failed to build workflow router; workflow "
                    "routing disabled for this session: %s",
                    self.thread_id, exc,
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
                # Phase 0 PR 5: every domain child shares the same
                # checkpointer.  Per-domain thread isolation is achieved
                # by ``_child_thread_id`` returning a domain-suffixed
                # thread id, NOT by giving each child its own
                # checkpointer object.
                checkpointer=self._make_checkpointer(),
            )
            self._child_open_locks[domain] = asyncio.Lock()

        if not self._children:
            raise RuntimeError("No domain children could be constructed.")

        self._is_open = True
        logger.info(
            "[%s] session ready; domains registered (not yet spawned): %s",
            self.thread_id,
            [d.value for d in self._children.keys()],
        )

    async def close(self) -> None:
        """Shut down any child MCP subprocesses that were actually
        spawned during the session."""
        if not self._is_open:
            return

        logger.info("[%s] closing session", self.thread_id)
        await self._close_children_quiet()
        self._children = {}
        self._child_open_locks = {}
        self._supervisor = None
        self._is_open = False

    async def _close_children_quiet(self) -> None:
        """Close children in parallel, swallowing individual failures.
        ``DomainAgentSession.close()`` is a no-op if the child was never
        opened, so un-spawned children incur no cost here."""
        if not self._children:
            return
        close_tasks = [child.close() for child in self._children.values()]
        await asyncio.gather(*close_tasks, return_exceptions=True)

    async def _ensure_child_open(self, domain: Domain) -> DomainAgentSession:
        """Lazily spawn a child's MCP subprocess on first use.

        Concurrency: multiple fan-out tasks can call this for the same
        domain simultaneously (multi-domain branch fires off parallel
        runs).  A per-domain ``asyncio.Lock`` ensures the subprocess is
        spawned exactly once; subsequent callers wait on the lock and
        then take the fast ``_is_open`` check.
        """
        child = self._children.get(domain)
        if child is None:
            raise RuntimeError(
                f"No child registered for domain {domain.value}."
            )
        # Fast path: already open, no lock contention.
        if child._is_open:
            return child

        lock = self._child_open_locks.get(domain)
        if lock is None:
            # Defensive: should never happen given ``open()`` creates
            # locks alongside children, but keep the runtime honest.
            raise RuntimeError(
                f"No open-lock registered for domain {domain.value}."
            )

        async with lock:
            # Double-check under lock — another task may have opened it
            # while we were waiting.
            if not child._is_open:
                logger.info(
                    "[%s] lazily opening child for domain=%s",
                    self.thread_id, domain.value,
                )
                await child.open()
        return child

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
                        # Preserve per-tool error so CLI callers (and any
                        # other non-streaming consumer) can render partial
                        # failures rather than pretending everything
                        # succeeded.
                        "error": event.data.get("error"),
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
            except asyncio.CancelledError:
                # Client disconnected mid-turn.  Don't try to emit
                # events (the queue consumer is gone anyway) — just
                # propagate so asyncio completes the task.
                logger.info(
                    "[%s] turn %s cancelled mid-run",
                    self.thread_id, turn_label,
                )
                raise
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
                # Use put_nowait so this finally block can't itself be
                # suspended during cancellation.  The queue is unbounded.
                try:
                    queue.put_nowait(_SENTINEL)
                except asyncio.QueueFull:  # unreachable for unbounded queue
                    pass

        task = asyncio.create_task(pipeline())

        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
        finally:
            # If the consumer stops early (e.g. WebSocket disconnect),
            # CANCEL the pipeline rather than waiting for it to finish.
            # Waiting would keep burning Anthropic tokens and tool calls
            # for a client that's already gone.  asyncio.CancelledError
            # propagates through LangGraph's astream_events and the LLM
            # call, terminating promptly.
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    # Expected — that's what we asked for.
                    pass
                except Exception:
                    logger.debug(
                        "[%s] stream pipeline raised during cancel cleanup",
                        self.thread_id, exc_info=True,
                    )

    # ------------------------------------------------------------------
    # Internal turn pipeline
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # PR 10 — workflow-router pre-gate
    # ------------------------------------------------------------------

    async def _maybe_run_workflow(
        self,
        user_message: str,
        turn_label: str,
        emit,
        turn_start: float,
    ) -> bool:
        """Try to handle the turn as a workflow-template execution.

        Returns ``True`` when the workflow router emitted ROUTE and the
        turn was completed via the workflow event channel.  Returns
        ``False`` when the router emitted OUT_OF_SCOPE — the caller
        falls through to the existing supervisor flow.

        ``CLARIFY`` is treated like ``ROUTE`` (turn-completing) so the
        workflow-aware clarification question doesn't bounce through
        the supervisor a second time.

        Errors during workflow routing or execution are logged and
        the gate returns ``False`` so the supervisor still gets a
        chance to answer the prompt — graceful degradation.
        """
        assert self._workflow_router is not None
        try:
            decision = await self._workflow_router.route(user_message)
        except Exception as exc:
            logger.exception(
                "[%s] %s workflow router failed; falling through to "
                "supervisor: %s",
                self.thread_id, turn_label, exc,
            )
            return False

        # OUT_OF_SCOPE → not a workflow shape.  Fall through cleanly;
        # the supervisor will handle it on the existing path.  We
        # intentionally do NOT emit a ``workflow_route_decision`` event
        # for the out-of-scope case — that would surface workflow-
        # internals to a turn that the user expected as a primitive
        # query.
        if decision.action == WorkflowRouteAction.OUT_OF_SCOPE:
            return False

        # Surface the routing decision regardless of ROUTE / CLARIFY so
        # the frontend can render the structured envelope.
        await emit(
            SessionEvent(
                type="workflow_route_decision",
                data={
                    "action": decision.action.value,
                    "template_id": decision.template_id,
                    "slot_values": dict(decision.slot_values or {}),
                    "rationale": decision.rationale,
                    "clarification_question": decision.clarification_question,
                    "adjustments": list(decision.adjustments),
                },
            )
        )

        if decision.action == WorkflowRouteAction.CLARIFY:
            question = (
                decision.clarification_question
                or "Could you clarify which workflow you want to run?"
            )
            # Emit a generic clarification event for the existing chat
            # UI plus a token stream so the assistant message renders
            # the clarification as ordinary prose.
            await emit(
                SessionEvent(type="clarification", data={"question": question})
            )
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
            return True

        # ROUTE → execute the bound workflow.  Surface a workflow_status
        # "running" chip so the UI can show progress.
        await emit(
            SessionEvent(
                type="workflow_status",
                data={"status": "running"},
            )
        )

        # Lazy-import the runner so the orchestrator package stays
        # transport-blind w.r.t. the rates resolver until a workflow
        # actually runs.
        from rates_agent.workflows._runner import run_template

        # Source the SQLAlchemy engine the same way the REST routes do —
        # the substrate's executor needs a live engine to dispatch the
        # primitives' DB-bound calls.  Falling through with engine=None
        # surfaces as ``AttributeError: 'NoneType' has no attribute
        # 'connect'`` deep inside the first primitive, which is the bug
        # the chat path showed.  We swallow init errors and degrade
        # gracefully so a missing DB doesn't take down the chat.
        engine = None
        try:
            from api.dependencies import get_engine, init_engine
            try:
                engine = get_engine()
            except RuntimeError:
                # Lifespan hook didn't run (e.g. orchestrator launched
                # outside FastAPI) — initialise the singleton on first
                # use.
                engine = init_engine()
        except Exception as exc:
            logger.warning(
                "[%s] %s could not acquire DB engine for workflow run: %s",
                self.thread_id, turn_label, exc,
            )

        # The substrate's executor is synchronous; offload to a thread
        # so the WebSocket event loop isn't blocked.
        try:
            envelope = await asyncio.to_thread(
                run_template,
                decision.template_id,
                dict(decision.slot_values or {}),
                engine=engine,
            )
        except Exception as exc:
            logger.exception(
                "[%s] %s workflow execution raised: %s",
                self.thread_id, turn_label, exc,
            )
            envelope = {
                "ok": False,
                "template_id": decision.template_id,
                "error": f"Workflow execution raised: {exc}",
            }

        await emit(
            SessionEvent(
                type="workflow_status",
                data={
                    "status": "complete" if envelope.get("ok") else "error",
                },
            )
        )

        # Stream a one-line prose summary as a token so the chat
        # assistant bubble shows readable text alongside the structured
        # workflow_result card.
        prose = _format_workflow_prose(envelope)
        if prose:
            await emit(SessionEvent(type="token", data={"content": prose}))

        await emit(
            SessionEvent(
                type="workflow_result",
                data={
                    "ok": bool(envelope.get("ok")),
                    "template_id": envelope.get("template_id"),
                    "terminal_artifact": envelope.get("terminal_artifact"),
                    "workflow_lineage_summary": envelope.get(
                        "workflow_lineage_summary"
                    ),
                    "error": envelope.get("error"),
                    "route": {
                        "template_id": decision.template_id,
                        "slot_values": dict(decision.slot_values or {}),
                        "rationale": decision.rationale,
                    },
                },
            )
        )

        await emit(
            SessionEvent(
                type="done",
                data={
                    # Workflow turns don't currently surface a
                    # workspace_context (the workflow result IS the
                    # workspace surface).  Future PR can route the
                    # bound workflow back into the existing workspace
                    # by translating the slot_values into the legacy
                    # ?tool=... query string when applicable.
                    "workspace_context": None,
                    "tool_calls": [],
                    "total_duration_ms": round(
                        (time.monotonic() - turn_start) * 1000
                    ),
                },
            )
        )
        return True

    # ------------------------------------------------------------------
    # PR 8 — persistent turn lifecycle + reference resolver
    # ------------------------------------------------------------------

    def _persistent_turn_enabled(self) -> bool:
        """True iff this session should bracket turns with the
        ``copilot_state.turns`` lifecycle.

        Requires:
          - an SQLAlchemy engine (so we can write)
          - a session_id UUID (so the turn knows what session it
            belongs to)
          - stateless=False (stateless sessions are deliberately
            ephemeral — no DB writes)

        When False, ``_run_turn`` skips ``begin_turn`` / ``commit_turn``
        entirely.  Tests and the CLI REPL hit this branch.
        """
        return (
            not self.stateless
            and self._engine is not None
            and self._session_id is not None
        )

    def _begin_turn_if_possible(
        self, user_message: str
    ) -> Optional["TurnContext"]:
        """Open a ``copilot_state.turns`` row.  Returns None on failure
        or when persistent lifecycle is disabled.

        Failures are LOGGED but never propagate — a DB outage should
        not break the chat (degraded operation, same contract as
        the checkpointer pool).
        """
        if not self._persistent_turn_enabled():
            return None
        try:
            from orchestrator.state import begin_turn as _begin_turn

            with self._engine.begin() as conn:
                return _begin_turn(
                    user_message,
                    session_id=self._session_id,
                    conn=conn,
                )
        except Exception as exc:
            logger.warning(
                "[%s] begin_turn failed; running turn without "
                "persistence: %s",
                self.thread_id, exc,
            )
            return None

    def _commit_turn_if_possible(
        self,
        turn_ctx: Optional["TurnContext"],
        *,
        status: str,
        assistant_response: Optional[str],
        save_as: Optional[str],
    ) -> None:
        """Finalize the turn row, if one was opened.  Best-effort:
        commit-time failures are LOGGED, not raised.

        ``terminal_artifact_hash`` is None in PR 8 — the chat path
        does not yet produce a content-addressed terminal artifact
        per turn (workflow turns produce envelopes; the artifact
        store wiring lives in the workflow runner, not here).  Once
        a future PR wires terminal artifacts back through the chat
        path, this method gains a ``terminal_artifact_hash`` arg.
        """
        if turn_ctx is None:
            return
        try:
            from orchestrator.state import commit_turn as _commit_turn

            with self._engine.begin() as conn:
                _commit_turn(
                    turn_ctx,
                    conn=conn,
                    status=status,
                    assistant_response=assistant_response,
                    terminal_artifact_hash=None,
                    save_as=save_as,
                )
        except Exception as exc:
            logger.warning(
                "[%s] commit_turn failed (status=%s); turn row left "
                "in 'running' state: %s",
                self.thread_id, status, exc,
            )

    async def _resolve_references(
        self, user_message: str
    ) -> Optional["ReferenceResolution"]:
        """Run the reference resolver against ``user_message``.

        Returns None when:
          - persistent lifecycle is disabled (no engine / no session
            id / stateless),
          - the resolver wasn't built at open() time,
          - the LLM call failed (the resolver itself swallows + logs).

        Pulls visible names from ``state.working_set.list_visible``
        in a fresh transaction so the resolver sees the latest set.
        """
        if not self._persistent_turn_enabled() or self._resolver is None:
            return None
        try:
            from state.working_set import list_visible

            with self._engine.connect() as conn:
                names = [n.name for n in list_visible(
                    session_id=self._session_id, conn=conn,
                )]
        except Exception as exc:
            logger.warning(
                "[%s] could not load visible working-set names for "
                "resolver; skipping: %s",
                self.thread_id, exc,
            )
            return None

        try:
            return await self._resolver.resolve(
                user_message, visible_names=names,
            )
        except Exception as exc:
            logger.warning(
                "[%s] resolver raised; continuing without it: %s",
                self.thread_id, exc,
            )
            return None

    def _augment_user_message(
        self,
        user_message: str,
        visible_names: list[str],
    ) -> str:
        """Prepend the working-set block to the user message.

        The supervisor + each domain child see the augmented text;
        the raw user_message lands unchanged in
        ``copilot_state.turns.user_message`` because that's stored
        BEFORE this augmentation runs (in ``begin_turn``).
        """
        if not visible_names:
            return user_message
        block = render_working_set_block(visible_names)
        return f"{block}\n\nUSER MESSAGE:\n{user_message}"

    async def _run_turn(
        self,
        user_message: str,
        turn_label: str,
        emit,
    ) -> None:
        """End-to-end orchestration for one user turn.

        PR 10 inserted a workflow-router pre-gate BEFORE the supervisor
        path.  The pre-gate runs the prompt against the
        ``WorkflowRouter`` (a separate LLM call returning a typed
        ``WorkflowRouteDecision``).  When the action is ROUTE, the
        bound workflow template executes and the turn closes via the
        workflow event channel — the supervisor + per-domain agents
        are NEVER called.  When the action is OUT_OF_SCOPE (e.g. a
        primitive-only question like "where's SOFR 2Y?") or CLARIFY,
        the turn falls through to the existing supervisor flow
        unchanged.

        Phase 0 PR 8 adds persistent turn lifecycle around this flow:

          - begin_turn at entry (when engine + session_id are set);
            failures log a warning and the turn runs without
            persistence rather than failing the user-visible path.
          - resolver runs once at entry to extract save_as +
            referenced_names from the natural-language message.  Its
            output is captured and used by commit_turn (save_as) and
            by the prompt augmentation (visible-name block injection).
          - commit_turn at exit, with status / assistant_response /
            save_as bound from the run.

        Steps:
          0. (PR 8) begin_turn + resolver + visible-name block injection.
          1. (PR 10) workflow router pre-gate.  ROUTE → run workflow +
             return; OUT_OF_SCOPE → fall through; CLARIFY → emit
             question + return (same shape as supervisor's clarify).
          2. emit ``status=routing`` and get the RouteDecision from the
             supervisor.
          3. emit ``route_decision`` with action/domains/rationale.
          4. Branch: clarify, single_domain, or multi_domain.
          5. emit ``done`` with workspace_context and tool_calls from
             whichever children ran.
          6. (PR 8) commit_turn finalises the turn row.
        """
        turn_start = time.monotonic()

        # ------------------------------------------------------------------
        # 0. PR 8 — persistent turn lifecycle: begin_turn + resolver
        # ------------------------------------------------------------------
        turn_ctx = self._begin_turn_if_possible(user_message)
        resolution = await self._resolve_references(user_message)
        save_as: Optional[str] = (
            resolution.save_as if resolution is not None else None
        )

        # Capture assistant-visible token stream so commit_turn can
        # persist it as ``assistant_response``.  This is the text the
        # user actually saw; wraps emit to tee the token events.
        emitted_text_parts: list[str] = []
        original_emit = emit

        async def teeing_emit(event: SessionEvent) -> None:
            if event.type == "token":
                piece = event.data.get("content", "")
                if piece:
                    emitted_text_parts.append(piece)
            await original_emit(event)

        emit = teeing_emit

        # Inject the working-set block into the user message.  Names
        # come from the resolver's visible-name set (the same list it
        # saw); empty list → no augmentation.
        augmented_message = user_message
        if resolution is not None:
            # The resolver received the visible names already; we
            # re-load them here from working_set so the augmentation
            # is in sync with what the LLM saw.  Cheap (single index
            # scan).
            try:
                from state.working_set import list_visible

                with self._engine.connect() as conn:
                    visible = [
                        n.name
                        for n in list_visible(
                            session_id=self._session_id, conn=conn,
                        )
                    ]
            except Exception:
                visible = []
            augmented_message = self._augment_user_message(
                user_message, visible
            )

        turn_status: str = "completed"
        try:
            # --------------------------------------------------------------
            # 0. WORKFLOW ROUTER PRE-GATE (PR 10)
            # --------------------------------------------------------------
            # The workflow router runs on the RAW user_message: it
            # routes on prompt shape, not conversational context, so
            # the working-set block would just be noise.
            if self._workflow_router is not None:
                workflow_handled = await self._maybe_run_workflow(
                    user_message, turn_label, emit, turn_start,
                )
                if workflow_handled:
                    return

            # --------------------------------------------------------------
            # 1. Supervisor routing — sees augmented message so it can
            #    route on "compare that with tips_2y_v1"-style refs.
            # --------------------------------------------------------------
            await emit(
                SessionEvent(type="status", data={"status": "routing"})
            )

            try:
                decision: RouteDecision = await self._supervisor.route(
                    augmented_message
                )
            except Exception as exc:
                logger.exception(
                    "[%s] %s supervisor route failed",
                    self.thread_id, turn_label,
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
                turn_status = "failed"
                return

            await emit(
                SessionEvent(
                    type="route_decision",
                    data={
                        "action": decision.action.value,
                        "domains": [d.value for d in decision.domains],
                        "rationale": decision.rationale,
                        "adjustments": list(decision.adjustments),
                    },
                )
            )

            # --------------------------------------------------------------
            # 2. Branch by action
            # --------------------------------------------------------------
            if decision.action == RouteAction.CLARIFY:
                question = (
                    decision.clarification_question
                    or "Could you clarify which market you're asking about?"
                )
                await emit(
                    SessionEvent(
                        type="clarification", data={"question": question}
                    )
                )
                await emit(
                    SessionEvent(type="token", data={"content": question})
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

            missing = [
                d for d in decision.domains if d not in self._children
            ]
            if missing:
                msg = (
                    f"Routing selected domain(s) with no open child: "
                    f"{[d.value for d in missing]}"
                )
                logger.error(
                    "[%s] %s %s", self.thread_id, turn_label, msg
                )
                await emit(
                    SessionEvent(type="error", data={"message": msg})
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
                turn_status = "failed"
                return

            if decision.action == RouteAction.SINGLE_DOMAIN:
                await self._run_single_domain(
                    user_message=augmented_message,
                    domain=decision.domains[0],
                    turn_label=turn_label,
                    turn_start=turn_start,
                    emit=emit,
                )
                return

            # MULTI_DOMAIN
            await self._run_multi_domain(
                user_message=augmented_message,
                domains=decision.domains,
                turn_label=turn_label,
                turn_start=turn_start,
                emit=emit,
            )
        except asyncio.CancelledError:
            # Cancellation isn't a "failed" turn — the user closed the
            # WebSocket / explicitly cancelled.  Record as cancelled
            # so the audit trail is honest, then re-raise so the
            # outer pipeline task observes the cancellation.
            turn_status = "cancelled"
            raise
        except Exception:
            turn_status = "failed"
            raise
        finally:
            assistant_response = (
                "".join(emitted_text_parts).strip() or None
            )
            self._commit_turn_if_possible(
                turn_ctx,
                status=turn_status,
                assistant_response=assistant_response,
                save_as=save_as,
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

        # Lazily spawn the child's MCP subprocess on first use.  If the
        # spawn fails, surface the error cleanly rather than crashing
        # the whole turn pipeline.
        try:
            child = await self._ensure_child_open(domain)
        except Exception as exc:
            logger.exception(
                "[%s] %s failed to open child for domain=%s",
                self.thread_id, turn_label, domain.value,
            )
            await emit(
                SessionEvent(
                    type="error",
                    data={
                        "message": (
                            f"Could not start the {domain.value} "
                            f"specialist: {exc}"
                        ),
                    },
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
        elif child_response.status == ChildStatus.OK and not tokens_emitted:
            # Narrow silent-failure case: tools ran successfully but the
            # LLM produced no final narration.  Surface whatever we have
            # rather than leaving the chat blank.
            logger.warning(
                "[%s] %s child returned OK with no streamed tokens; "
                "emitting fact fallback",
                self.thread_id, turn_label,
            )
            fallback = _format_ok_fallback(
                domain=domain,
                answer_markdown=child_response.answer_markdown,
                facts=child_response.facts,
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
                            "error": t.error,
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
            # Lazy-open this child's MCP subprocess if it hasn't been used
            # yet in this session.  Concurrent fan-out is safe: the
            # per-domain lock in _ensure_child_open serialises the spawn.
            child = await self._ensure_child_open(domain)
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
        # ``return_exceptions=True`` preserves input order: ``completed[i]``
        # corresponds to ``domains[i]``, so we can zip them to recover
        # which domain each result (or exception) belongs to.
        completed = await asyncio.gather(*child_tasks, return_exceptions=True)

        child_responses: list[ChildResponse] = []
        tool_call_summary: list[dict] = []
        workspace_parts: list[dict] = []

        for domain, item in zip(domains, completed):
            if isinstance(item, BaseException):
                # A child raised before returning a ChildResponse — e.g.
                # the MCP subprocess failed to lazy-open, or the LangGraph
                # stream crashed.  We must still tell the synthesis step
                # that this domain was requested and failed, otherwise
                # synthesis produces a confident partial answer for what
                # the user asked to be a cross-domain comparison.
                logger.exception(
                    "[%s] %s child run raised for domain=%s",
                    self.thread_id, turn_label, domain.value,
                )
                error_msg = f"{type(item).__name__}: {item}"

                await emit(
                    SessionEvent(
                        type="error",
                        data={
                            "message": (
                                f"{domain.value} specialist error: {error_msg}"
                            ),
                        },
                    )
                )
                # Emit child_finished so the frontend can balance the
                # child_started event we fired before fan-out began.
                await emit(
                    SessionEvent(
                        type="child_finished",
                        data={
                            "domain": domain.value,
                            "status": ChildStatus.ERROR.value,
                            "duration_ms": None,
                        },
                    )
                )
                # Synthesize a placeholder ChildResponse so the synthesis
                # step sees the failure and the synthesis prompt's
                # "state errors plainly" rule kicks in.
                child_responses.append(
                    ChildResponse(
                        status=ChildStatus.ERROR,
                        domain=domain,
                        answer_markdown="",
                        facts=[],
                        workspace_context=None,
                        tool_trace=[],
                        error_message=error_msg,
                    )
                )
                continue

            _domain, response, duration_ms = item
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
                        "error": t.error,
                    }
                )
            if response.workspace_context:
                workspace_parts.append(response.workspace_context)

        # --------------------------------------------------------------
        # Synthesis
        # --------------------------------------------------------------
        # After the fan-out fix above, ``child_responses`` always contains
        # one entry per requested domain (real or error-placeholder).
        # The all-failed short-circuit therefore checks status, not list
        # emptiness.
        all_failed = (
            bool(child_responses)
            and all(r.status == ChildStatus.ERROR for r in child_responses)
        )
        if not child_responses or all_failed:
            msg = (
                "All domain children failed; no answer could be produced."
            )
            await emit(
                SessionEvent(type="error", data={"message": msg})
            )
            # Give the user visible text too so the chat isn't blank.
            await emit(
                SessionEvent(type="token", data={"content": msg})
            )
            await emit(
                SessionEvent(
                    type="done",
                    data={
                        "workspace_context": None,
                        # Keep shape consistent with the normal done
                        # path: strip ``params`` (internal only) but
                        # preserve ``error`` so any surviving tool calls
                        # render with their failure state.
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
        """Generate the child's LangGraph checkpointer thread id.

        In stateless mode the id includes ``turn_label`` so each turn
        writes to a fresh thread; the legacy V0 behavior used by tests
        and the CLI REPL that explicitly opt out of durability.

        In stateful mode (the new Phase 0 PR 5 default) the id is
        stable per-(session, domain), so multiple turns of the same
        session share the same thread and the checkpointer accumulates
        conversation state.  When ``self._checkpointer_pool`` is
        provided, that state lives in Postgres and survives Python
        process restart — opening a fresh CopilotSession against the
        same ``thread_id`` resumes the conversation.
        """
        if self.stateless:
            return f"{self.thread_id}-{turn_label}-{domain.value}"
        return f"{self.thread_id}-{domain.value}"

    def _make_checkpointer(self) -> BaseCheckpointSaver:
        """Build the checkpointer this session's domain children share.

        Selection matrix (see class docstring):

          - stateless=True            → MemorySaver (in-memory; per-turn
                                        fresh thread defeats persistence)
          - stateless=False, pool=None → MemorySaver (in-memory;
                                        in-process persistence only;
                                        suitable for unit tests that
                                        don't spin up Postgres)
          - stateless=False, pool=set  → AsyncPostgresSaver(pool); durable
                                        across process restart; this is
                                        the production WebSocket path.

        Called once per child at session ``open()`` time, NOT per turn.
        """
        if self.stateless:
            return MemorySaver()
        if self._checkpointer_pool is None:
            return MemorySaver()
        # Lazy import keeps the load-time cost off the no-pool path.
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        return AsyncPostgresSaver(self._checkpointer_pool)


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


def _format_ok_fallback(
    domain: Domain,
    answer_markdown: str,
    facts: list,
) -> str:
    """Fallback text when a child returned OK but produced no streamed
    narration (tool executed, LLM didn't summarise).

    Preference order:
      1. Whatever prose we have in ``answer_markdown`` (even if it wasn't
         streamed live — e.g. content came back in a non-text block).
      2. A compact one-line summary built from the structured facts.
      3. A generic "result available" line with the domain name.
    """
    if answer_markdown and answer_markdown.strip():
        return answer_markdown.strip()

    if facts:
        fragments: list[str] = []
        # Cap the number of facts we surface so the fallback stays short.
        for f in facts[:6]:
            metric = getattr(f, "metric", None) or "?"
            value = getattr(f, "value", None)
            units = getattr(f, "units", None)
            curve = getattr(f, "curve_family", None)
            value_str = _format_scalar(value)
            unit_str = f" {units}" if units else ""
            prefix = f"{curve} " if curve else ""
            fragments.append(f"{prefix}{metric}={value_str}{unit_str}")
        body = "; ".join(fragments)
        suffix = "" if len(facts) <= 6 else f" (+{len(facts) - 6} more)"
        return (
            f"The {domain.value} specialist returned data without a "
            f"narration. Key facts: {body}{suffix}."
        )

    return (
        f"The {domain.value} specialist completed the request but "
        f"produced no summary. Please rephrase or try again."
    )


def _format_scalar(value) -> str:
    """Best-effort scalar formatter for fact fallback text."""
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


# ===========================================================================
# PR 10 — workflow-result prose formatter
# ===========================================================================


def _format_workflow_prose(envelope: dict) -> str:
    """Render a one-line PM-readable summary of a workflow execution
    envelope.  Streamed as a single ``token`` event so the chat
    assistant bubble shows readable text alongside the structured
    ``workflow_result`` event the frontend renders separately.
    """
    template_id = envelope.get("template_id") or "?"
    if not envelope.get("ok"):
        err = envelope.get("error") or "(no detail)"
        return (
            f"Workflow ``{template_id}`` did not execute cleanly: {err}"
        )
    terminal = envelope.get("terminal_artifact") or {}
    units = terminal.get("units") or "?"
    n_rows = terminal.get("n_rows", "?")
    # Event-relative Series (e.g. event_study terminal aggregate) is
    # indexed by integer horizon, not calendar date — so we use the
    # noun "horizons" instead of "rows".
    row_noun = (
        "horizons" if terminal.get("index_kind") == "event_relative_offset"
        else "rows"
    )
    summary_stats = terminal.get("summary_stats") or {}
    mean = summary_stats.get("mean")
    if mean is not None and isinstance(mean, (int, float)):
        return (
            f"Ran ``{template_id}``. Terminal Series ({units}, "
            f"{n_rows} {row_noun}) — mean {mean:.4g}. See the workflow "
            "result card for the full per-offset / per-regime view."
        )
    artifact_type = terminal.get("type", "Series")
    return (
        f"Ran ``{template_id}``. Terminal artifact: {artifact_type} "
        f"({units}, {n_rows} {row_noun}). See the workflow result card "
        "for details."
    )

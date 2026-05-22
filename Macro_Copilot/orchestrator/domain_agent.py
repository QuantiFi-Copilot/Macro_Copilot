"""
orchestrator/domain_agent.py — Domain-specialist child agent session
=====================================================================

One ``DomainAgentSession`` instance per domain (sovereign_bonds, ois).  Each
owns:

- an isolated ``MultiServerMCPClient`` connected ONLY to its domain's MCP
  server subprocess (hard guardrail: the child physically cannot access
  another domain's tools)
- its cached system prompt
- a LangGraph ReAct state machine over its tool set
- its own logging namespace

The public API is minimal:

- ``open()``     — spawn the MCP subprocess and build the graph
- ``close()``    — shut the subprocess down
- ``run(...)``   — invoke the agent on a verbatim user message, return a
                   ``ChildResponse``, and emit streaming events via an
                   optional callback so the parent session can forward them
                   to the WebSocket.

Design choices
--------------
- **Verbatim passthrough.**  The child receives the user's exact words as
  a ``HumanMessage``.  The supervisor never paraphrases.
- **Optional domain boundary.**  In the multi-domain path, the parent
  passes a ``domain_boundary`` string that becomes a short prefix on
  the ``HumanMessage`` ("[scope for this query] ...\n\n<user_message>")
  telling the child to stay in its lane.  Embedded in the HumanMessage
  rather than a separate SystemMessage — see PR 16 commentary in
  ``run()`` for why.
- **Structured output.**  The LangGraph ReAct loop still produces
  prose + tool_calls.  We parse the tool-call trajectory into structured
  ``FactRow`` entries so the supervisor's synthesis step consumes numbers,
  not the child's narrative.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.prebuilt import ToolNode, tools_condition

from orchestrator.contracts import (
    ChildResponse,
    ChildStatus,
    ChildToolCallTrace,
    Domain,
    FactRow,
)
from orchestrator.events import (
    SessionEvent,
    extract_workspace_context,
    is_workspace_tool,
    make_tool_label,
)

logger = logging.getLogger("orchestrator.domain_agent")


# Signature of the streaming-event callback the parent session passes in.
EventSink = Callable[[SessionEvent], Awaitable[None]]


# ============================================================================
# USAGE LOGGING
# ============================================================================

def _log_usage(label: str, response) -> None:
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
# DOMAIN AGENT SESSION
# ============================================================================

class DomainAgentSession:
    """A domain-specialist child agent.

    Parameters
    ----------
    domain : Domain
        Which specialist this session represents.
    system_prompt : str
        Raw prompt text.  Wrapped in a cached SystemMessage at open() time.
    mcp_servers : dict
        A subset of the MCP_SERVERS config containing ONLY this domain's
        server.  Ensures tool-level isolation from other domains.
    model_name, temperature, max_tokens : llm config
    """

    def __init__(
        self,
        domain: Domain,
        system_prompt: str,
        mcp_servers: dict,
        model_name: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        checkpointer: Optional[BaseCheckpointSaver] = None,
    ):
        self.domain = domain
        self._system_prompt = system_prompt
        self._mcp_servers = mcp_servers
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Phase 0 PR 5: checkpointer is supplied by the parent
        # CopilotSession.  ``None`` means "use a per-domain MemorySaver"
        # — preserved as the backward-compat default for tests and
        # one-shot CLI use that don't want durability.  In the
        # production WebSocket path, the parent passes an
        # ``AsyncPostgresSaver`` so conversation state survives server
        # restarts.
        self._checkpointer: BaseCheckpointSaver = checkpointer or MemorySaver()

        self._mcp_client: MultiServerMCPClient | None = None
        self._graph = None
        self._cached_system_message: SystemMessage | None = None
        self._tool_names: list[str] = []
        self._is_open = False

    # ------------------------------------------------------------------
    # LIFECYCLE
    # ------------------------------------------------------------------

    async def open(self) -> None:
        """Spawn the MCP subprocess, discover tools, build the graph."""
        if self._is_open:
            return

        logger.info("[%s] opening domain agent session", self.domain.value)

        self._mcp_client = MultiServerMCPClient(self._mcp_servers)
        tools = await self._mcp_client.get_tools()
        self._tool_names = [t.name for t in tools]

        if not tools:
            raise RuntimeError(
                f"Domain agent {self.domain.value} discovered no tools from "
                f"MCP servers: {list(self._mcp_servers.keys())}"
            )
        logger.info(
            "[%s] tools discovered: %s", self.domain.value, self._tool_names
        )

        # Cache the system prompt at the content-block level.  Anthropic
        # caches the stable prefix [tools] + [system]; the user message
        # is after the breakpoint and is not cached.
        self._cached_system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": self._system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )

        model = ChatAnthropic(
            model=self._model_name,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        model_with_tools = model.bind_tools(tools)

        async def agent_node(state: MessagesState) -> dict:
            # Always prepend the cached SystemMessage.  Anthropic's API
            # requires SystemMessages to be CONSECUTIVE at the start of
            # the message list — any SystemMessage that surfaces later
            # in the list (separated by Human/AI/Tool messages) trips
            # ``ValueError: Received multiple non-consecutive system
            # messages`` inside ``langchain_anthropic``.
            #
            # In stateful mode (PR 5), ``state["messages"]`` is restored
            # from the checkpointer across turns.  If a prior turn added
            # any SystemMessage to state (the multi-domain ``scope``
            # SystemMessage was the historical culprit — PR 16 removed
            # that injection at the input layer, but checkpointers
            # opened BEFORE this fix still contain old SystemMessages),
            # we MUST filter them out here.  Filtering is defensive
            # belt-and-braces: even if the input layer never adds a
            # SystemMessage, this guarantees correctness against any
            # historical / future state shape.
            sanitized_history = [
                m for m in state["messages"] if not isinstance(m, SystemMessage)
            ]
            messages_for_model = (
                [self._cached_system_message] + sanitized_history
            )
            response = await model_with_tools.ainvoke(messages_for_model)
            _log_usage(f"{self.domain.value}.agent", response)
            return {"messages": [response]}

        tool_node = ToolNode(tools)

        builder = StateGraph(MessagesState)
        builder.add_node("agent", agent_node)
        builder.add_node("tools", tool_node)
        builder.add_edge(START, "agent")
        builder.add_conditional_edges("agent", tools_condition)
        builder.add_edge("tools", "agent")

        # The checkpointer was selected by the parent CopilotSession
        # (see CopilotSession._make_checkpointer).  In stateless / no-pool
        # paths this is ``MemorySaver``; in the durable path it's an
        # ``AsyncPostgresSaver`` sharing a pool with all sibling
        # DomainAgentSessions of this CopilotSession.
        self._graph = builder.compile(checkpointer=self._checkpointer)

        self._is_open = True
        logger.info(
            "[%s] session ready (checkpointer=%s)",
            self.domain.value,
            type(self._checkpointer).__name__,
        )

    async def close(self) -> None:
        """Shut down the MCP subprocess."""
        if not self._is_open:
            return

        logger.info("[%s] closing session", self.domain.value)
        if self._mcp_client is not None:
            try:
                if hasattr(self._mcp_client, "__aexit__"):
                    await self._mcp_client.__aexit__(None, None, None)
                elif hasattr(self._mcp_client, "close"):
                    await self._mcp_client.close()
            except Exception:
                logger.debug(
                    "[%s] MCP client cleanup exception (non-fatal)",
                    self.domain.value,
                    exc_info=True,
                )
            self._mcp_client = None

        self._graph = None
        self._cached_system_message = None
        self._is_open = False

    # ------------------------------------------------------------------
    # RUN
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        domain_boundary: Optional[str] = None,
        emit_tokens: bool = True,
        on_event: Optional[EventSink] = None,
        turn_thread_id: str = "turn",
    ) -> ChildResponse:
        """Run the agent on a verbatim user message.

        Parameters
        ----------
        user_message : str
            The user's exact words.  Passed through verbatim — never
            paraphrased by the supervisor or this method.
        domain_boundary : Optional[str]
            When set, a short scope instruction prepended before the user
            message.  Used in the multi-domain fan-out to keep each child
            in its lane.
        emit_tokens : bool
            Single-domain path: True — child's tokens stream to the user
            as the final answer.
            Multi-domain path: False — child's prose is buffered into
            ``answer_markdown`` but not streamed to the user; the
            supervisor's synthesis is what the user sees.
        on_event : Optional[EventSink]
            Async callback invoked for every streamable event.
        turn_thread_id : str
            Unique id for this turn's LangGraph checkpointer slot.
        """
        if not self._is_open:
            raise RuntimeError(f"Domain agent {self.domain.value} is not open.")

        started = time.monotonic()

        # Build the input message list.
        # LangGraph MessagesState applies reducers; we send the initial
        # messages for this turn (excluding the system, which the agent
        # node prepends).
        #
        # PR 16: the scope hint used to be a separate ``SystemMessage``
        # prepended here.  That worked single-turn but broke
        # cross-turn in stateful mode (PR 5): the SystemMessage flowed
        # through the ``add_messages`` reducer into ``state["messages"]``,
        # then on the NEXT turn ``agent_node`` prepended a fresh cached
        # SystemMessage in front of state — producing two SystemMessages
        # separated by Human/AI/Tool messages, which Anthropic's API
        # rejects as "non-consecutive".  Once tripped, EVERY subsequent
        # turn for that domain in the same session failed.
        #
        # Fix: embed the scope hint as a prefix on the ``HumanMessage``
        # content.  Functionally equivalent for the LLM (a sentence at
        # the top of the user message telling it to stay in its lane),
        # but ``state["messages"]`` never accumulates SystemMessages.
        # Combined with ``agent_node``'s defensive filter, prior
        # poisoned state is also recoverable.
        if domain_boundary:
            message_content = (
                f"[scope for this query] {domain_boundary}\n\n{user_message}"
            )
        else:
            message_content = user_message
        initial_messages: list = [HumanMessage(content=message_content)]

        turn_config = {"configurable": {"thread_id": turn_thread_id}}

        # Accumulators we build from the event stream.
        answer_parts: list[str] = []
        tool_calls_seen: list[ChildToolCallTrace] = []
        raw_tool_outputs: list[tuple[str, dict, str]] = []  # (tool, params, raw_json)
        # PR-D — per-invocation context for the tool event loop.
        # Pre-PR-D this code used three SHARED MUTABLE variables
        # (``current_tool_name`` / ``current_tool_params`` /
        # ``current_tool_start``) to correlate ``on_tool_start`` ↔
        # ``on_tool_end`` events.  That assumed strict serial pairing
        # — but Claude can emit multiple ``tool_use`` blocks in a
        # single response, and LangGraph's ``ToolNode`` then dispatches
        # them concurrently.  When events interleave (start-A,
        # start-B, start-C, end-A, end-B, end-C) every shared
        # assignment in start-B / start-C clobbered A's params before
        # end-A could read them, and the post-end reset wiped state
        # so end-B and end-C saw ``params={}``.  Result on the user
        # surface: one card with correct params, N-1 cards with all
        # params missing → PR-B-β's "missing params" tile fires for
        # genuinely-correct tool invocations.  See the screenshots
        # attached to PR-D's description.
        #
        # PR-D keys per-invocation context by the LangGraph
        # ``run_id`` (a UUID present on every astream_events v2
        # event for the same invocation — see LangChain docs).  Each
        # ``on_tool_end`` looks up ITS OWN start's data by id, so
        # interleaved events are handled correctly without any
        # shared mutable state.  The dict is bounded — entries are
        # ``pop``ped on the matching end, and orphan starts (no end
        # event before the loop exits) are negligible memory (the
        # whole turn is short-lived).
        inflight_tools: Dict[str, Dict[str, Any]] = {}

        try:
            async for event in self._graph.astream_events(
                {"messages": initial_messages},
                config=turn_config,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})
                # PR-D — the run_id correlator.  Defensive: some
                # LangChain versions may emit events without it for
                # synthetic / wrapping nodes; we fall back to
                # ``f"__no_run_id__:{name}"`` so the inflight lookup
                # still does something useful for the serial single-
                # tool case (the bug we're fixing only triggers when
                # multiple tools fire in parallel, which they only
                # do under astream_events v2 where ``run_id`` is
                # always present per the v2 contract).
                run_id = event.get("run_id") or f"__no_run_id__:{name}"

                if kind == "on_tool_start":
                    tool_input = data.get("input", {})
                    if isinstance(tool_input, str):
                        try:
                            tool_input = json.loads(tool_input)
                        except (json.JSONDecodeError, TypeError):
                            tool_input = {}
                    params = (
                        tool_input if isinstance(tool_input, dict) else {}
                    )
                    inflight_tools[run_id] = {
                        "name": name,
                        "params": params,
                        "start_time": time.monotonic(),
                    }

                    if on_event is not None:
                        label = make_tool_label(name, params)
                        await on_event(
                            SessionEvent(
                                type="tool_call",
                                data={
                                    "tool": name,
                                    "label": label,
                                    "params": params,
                                    "domain": self.domain.value,
                                },
                            )
                        )

                elif kind == "on_tool_end":
                    # Look up THIS invocation's context by run_id.
                    # ``pop`` removes the entry so the dict stays
                    # bounded; missing entries (orphan end events)
                    # fall back to the event's ``name`` + empty
                    # params — same fail-soft contract the pre-PR-D
                    # code had when ``current_tool_name`` happened
                    # to be ``None`` (the ``or name`` branch).
                    ctx = inflight_tools.pop(run_id, None)
                    tool_name = (ctx["name"] if ctx else None) or name
                    params: Dict[str, Any] = (
                        ctx["params"] if ctx else {}
                    )
                    start_time = ctx["start_time"] if ctx else None
                    duration_ms: Optional[int] = (
                        round((time.monotonic() - start_time) * 1000)
                        if start_time is not None
                        else None
                    )

                    # The tool output is a JSON string (per our MCP server
                    # convention).  Save the raw text for fact extraction.
                    tool_output_text = _stringify_tool_output(data.get("output"))
                    raw_tool_outputs.append(
                        (tool_name, params, tool_output_text)
                    )
                    # If the tool returned {"error": "..."}, surface it on
                    # the trace so partial same-domain failures are
                    # visible in ChildResponse.tool_trace even when
                    # another tool in the same run succeeded.
                    tool_error = _tool_error_from_output(tool_output_text)
                    tool_calls_seen.append(
                        ChildToolCallTrace(
                            tool=tool_name,
                            params=params,
                            duration_ms=duration_ms,
                            error=tool_error,
                        )
                    )

                    if on_event is not None:
                        await on_event(
                            SessionEvent(
                                type="tool_result",
                                data={
                                    "tool": tool_name,
                                    "domain": self.domain.value,
                                    "duration_ms": duration_ms,
                                    # Frontend can render an error badge
                                    # without having to re-parse the raw
                                    # tool output.
                                    "error": tool_error,
                                },
                            )
                        )

                elif kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk is None:
                        continue
                    text = _extract_text_from_chunk(chunk)
                    if not text:
                        continue
                    answer_parts.append(text)
                    if emit_tokens and on_event is not None:
                        await on_event(
                            SessionEvent(type="token", data={"content": text})
                        )

        except Exception as exc:
            logger.exception("[%s] run failed", self.domain.value)
            return ChildResponse(
                status=ChildStatus.ERROR,
                domain=self.domain,
                answer_markdown="",
                facts=[],
                workspace_context=None,
                tool_trace=tool_calls_seen,
                error_message=f"{type(exc).__name__}: {exc}",
            )

        answer_markdown = "".join(answer_parts).strip()

        # Extract structured facts from tool outputs.
        facts = _extract_facts(raw_tool_outputs)

        # Build workspace_context from the trace.
        # PR-B-α — propagate the trace's optional ``error`` +
        # ``duration_ms`` fields so the workspace_context entries can
        # carry per-call status metadata.  Build's Ask → Build canvas
        # consumes this to render an honest "this tool failed" tile
        # alongside the working cards instead of silently dropping the
        # entry.  Backward compatible: extract_workspace_context only
        # emits the optional fields when the source dict has them.
        trace_dicts = [
            {
                "tool": t.tool,
                "params": t.params,
                "domain": self.domain.value,
                "error": t.error,
                "duration_ms": t.duration_ms,
            }
            for t in tool_calls_seen
        ]
        workspace_context = extract_workspace_context(trace_dicts)

        status, follow_up_question, error_message = _classify_status(
            answer_markdown=answer_markdown,
            tool_calls=tool_calls_seen,
            raw_tool_outputs=raw_tool_outputs,
        )

        duration_ms = round((time.monotonic() - started) * 1000)
        logger.info(
            "[%s] run complete status=%s tools=%d facts=%d duration=%dms",
            self.domain.value,
            status.value,
            len(tool_calls_seen),
            len(facts),
            duration_ms,
        )

        return ChildResponse(
            status=status,
            domain=self.domain,
            answer_markdown=answer_markdown,
            facts=facts,
            workspace_context=workspace_context,
            tool_trace=tool_calls_seen,
            follow_up_question=follow_up_question,
            error_message=error_message,
        )


# ============================================================================
# HELPERS
# ============================================================================

def _extract_text_from_chunk(chunk) -> str:
    """Pull plain text out of a streaming chunk regardless of list vs str
    content shape."""
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


def _stringify_tool_output(output) -> str:
    """Coerce a tool's raw output to a string.  MCP tools return JSON
    strings, but LangGraph may pass them as ToolMessage or dict."""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, ToolMessage):
        content = output.content
        return content if isinstance(content, str) else str(content)
    if isinstance(output, dict):
        return json.dumps(output, default=str)
    return str(output)


def _tool_error_from_output(raw: str) -> Optional[str]:
    """Return the ``error`` string from a tool's raw JSON output, or
    None if the tool succeeded (or output wasn't parseable JSON).

    Our MCP servers wrap error states as ``{"error": "..."}``; anything
    else is treated as a successful payload.
    """
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if not err:
        return None
    text = str(err).strip()
    return text or None


def _extract_facts(
    raw_tool_outputs: list[tuple[str, dict, str]],
) -> list[FactRow]:
    """Parse each tool's JSON output and extract scalar metrics as FactRow
    entries.

    Canonical tool envelope shapes
    ------------------------------
      - ``{"current_metrics": {...}}`` — most rates tools (spread, yield,
        butterfly, cross-market, regime)
      - ``{"results": [...]}``          — scanner tool
      - ``{"error": "..."}``            — skipped (not a fact)

    New domain tools (FX, credit, futures, etc.) SHOULD adopt one of
    these envelopes.  As a safety net against accidental divergence, a
    fallback below performs a best-effort extraction from unrecognized
    envelopes and logs a warning naming the offending tool — so the
    problem surfaces in development rather than producing a silent
    fact-free synthesis payload.
    """
    facts: list[FactRow] = []

    for tool_name, params, raw in raw_tool_outputs:
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        if not isinstance(payload, dict):
            continue

        if "error" in payload:
            continue

        cm = payload.get("current_metrics")
        if isinstance(cm, dict):
            facts.extend(_facts_from_current_metrics(tool_name, params, cm))
            continue

        results = payload.get("results")
        if isinstance(results, list):
            facts.extend(_facts_from_scanner_results(tool_name, results))
            continue

        # --------------------------------------------------------------
        # Fallback: unrecognized envelope.  Try to salvage facts rather
        # than silently producing zero so future domain tools that drift
        # from convention don't degrade synthesis quality without notice.
        # --------------------------------------------------------------
        fallback_facts = _facts_from_unrecognized_envelope(
            tool_name, params, payload
        )
        if fallback_facts:
            logger.warning(
                "Tool %s returned an unrecognized envelope with top-level "
                "keys %s. Fallback extraction produced %d fact(s). "
                "Prefer 'current_metrics' or 'results' for new tools.",
                tool_name,
                sorted(payload.keys()),
                len(fallback_facts),
            )
            facts.extend(fallback_facts)
        else:
            logger.warning(
                "Tool %s returned an unrecognized envelope with top-level "
                "keys %s and no extractable facts. Synthesis for this tool "
                "will rely on prose only.",
                tool_name,
                sorted(payload.keys()),
            )

    return facts


def _facts_from_unrecognized_envelope(
    tool_name: str,
    params: dict,
    payload: dict,
) -> list[FactRow]:
    """Best-effort extraction when a tool returns a shape we don't
    recognise.  We handle two common patterns:

    1. **Single nested dict** — e.g. ``{"fx_data": {"eurusd": 1.08, ...}}``.
       Treat the inner dict as if it were ``current_metrics``.
    2. **Flat scalars at top level** — e.g. ``{"eurusd": 1.08, "gbpusd": 1.27}``.
       Treat each scalar as its own fact.

    Nested lists and deeper structures are skipped; those belong in the
    canonical ``results`` envelope.
    """
    # Identify scalar and dict top-level values, ignoring envelope
    # metadata that's never a fact.
    ignore_keys = {"error", "status", "as_of_date"}
    scalar_items = []
    nested_dicts = []
    for key, value in payload.items():
        if key in ignore_keys:
            continue
        if _is_scalar(value):
            scalar_items.append((key, value))
        elif isinstance(value, dict):
            nested_dicts.append((key, value))
        # Other value types (lists, None, etc.) are skipped — they're
        # neither scalar facts nor a nested ``current_metrics`` shape.

    # Case 1: exactly one nested dict and no scalar facts → treat as
    # current_metrics.  _facts_from_current_metrics already enforces
    # scalar-only values, so deeper nesting like
    # {"fx_data": {"quotes": {"eurusd": 1.08}}} won't leak dict-valued
    # facts: the "quotes" entry simply gets skipped.
    if len(nested_dicts) == 1 and not scalar_items:
        inner_key, inner_dict = nested_dicts[0]
        facts = _facts_from_current_metrics(tool_name, params, inner_dict)
        # Prefix each metric with the envelope key so it's traceable
        # back to the non-canonical shape.
        return [
            FactRow(
                tool=f.tool,
                curve_family=f.curve_family,
                metric=f"{inner_key}.{f.metric}",
                value=f.value,
                units=f.units,
                as_of=f.as_of,
            )
            for f in facts
        ]

    # Case 2: top-level flat scalars.
    as_of = payload.get("as_of_date")
    curve_family = params.get("curve_family")
    out: list[FactRow] = []
    for key, value in scalar_items:
        out.append(
            FactRow(
                tool=tool_name,
                curve_family=curve_family,
                metric=key,
                value=value,
                units=_infer_units(key),
                as_of=as_of,
            )
        )
    return out


def _facts_from_current_metrics(
    tool_name: str,
    params: dict,
    cm: dict,
) -> list[FactRow]:
    """Flatten a ``current_metrics`` dict into FactRow entries.

    Invariant: a ``FactRow.value`` must be a scalar.  Nested dicts and
    lists are silently ignored — the synthesis model works on structured
    per-metric facts, not on sub-trees, so emitting a dict-valued
    FactRow would pollute the synthesis payload with non-scalar "facts"
    and mislead the supervisor.

    If a new tool legitimately needs to surface nested structure, it
    should either flatten before returning (``spread_metrics.long``) or
    use the ``results`` envelope.
    """
    as_of = cm.get("as_of_date")
    curve_family = cm.get("curve_family") or params.get("curve_family")

    # Pure-context keys that carry no measurement (they become the
    # ``as_of`` / ``curve_family`` fields on every emitted FactRow).
    skip_keys = {
        "as_of_date",
        "curve_family",
        "spread_label",
        "regime_description",   # legacy field name (from REST cards/detail wire format)
        "description",          # new curve_move_classifier narration field
        "lookback_period",
    }

    out: list[FactRow] = []
    for key, value in cm.items():
        if key in skip_keys:
            continue
        if not _is_scalar(value):
            # Silently skip nested dicts / lists / other complex values.
            # Canonical rates tools never produce these; defensive check
            # for future tools that drift from convention.
            continue
        out.append(
            FactRow(
                tool=tool_name,
                curve_family=curve_family,
                metric=key,
                value=value,
                units=_infer_units(key),
                as_of=as_of,
            )
        )
    return out


def _is_scalar(value) -> bool:
    """Return True if ``value`` is a primitive we can safely embed as a
    FactRow value.

    None is rejected so callers don't have to check separately.
    ``bool`` is allowed because ``isinstance(True, int)`` would already
    admit it, and explicit is better than implicit.
    """
    if value is None:
        return False
    return isinstance(value, (bool, int, float, str))


def _facts_from_scanner_results(
    tool_name: str,
    results: list,
) -> list[FactRow]:
    """Scanner returns a list of ranked extreme rows; each row becomes
    one FactRow per scalar metric field.

    Domain-agnostic: any scalar field in the row that isn't a
    pure-context key (curve_family, tenor, as_of_date, rank, etc.)
    becomes a fact.  That way a future OIS / FX / credit scanner that
    uses ``current_rate`` or ``spot`` instead of ``current_yield`` still
    contributes structured facts to synthesis without requiring this
    extractor to be updated.

    Non-scalar row values (nested dicts, lists) are skipped via
    ``_is_scalar`` to uphold the FactRow scalar invariant.
    """
    # Pure-context keys that identify the row rather than measure it.
    # These become FactRow.curve_family / as_of attributes instead of
    # emitting their own rows.
    context_keys = {
        "curve_family",
        "tenor",
        "as_of_date",
        "trade_date",
        "rank",
    }

    out: list[FactRow] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        curve = row.get("curve_family")
        tenor = row.get("tenor")
        as_of = row.get("as_of_date") or row.get("trade_date")
        scope_label = f"{curve} {tenor}" if curve and tenor else curve
        for key, value in row.items():
            if key in context_keys:
                continue
            if not _is_scalar(value):
                continue
            out.append(
                FactRow(
                    tool=tool_name,
                    curve_family=scope_label,
                    metric=key,
                    value=value,
                    units=_infer_units(key),
                    as_of=as_of,
                )
            )
    return out


def _infer_units(metric_key: str) -> Optional[str]:
    """Best-effort unit inference from a metric's name."""
    k = metric_key.lower()
    if k.endswith("_bps"):
        return "bps"
    if k.endswith("_yield") or k.endswith("_rate") or k == "current_yield":
        return "%"
    if "z_score" in k:
        return "sigma"
    if k.endswith("_days"):
        return "days"
    if k == "percentile":
        return "percentile"
    return None


# Keyword patterns the child uses when it can't fulfil the request.
# Kept deliberately tight to minimise false positives on legitimate answers
# that happen to contain one of these phrases in a different context.
_OUT_OF_SCOPE_SIGNALS: tuple[str, ...] = (
    "outside my domain",
    "outside the sovereign bond",
    "outside the ois",
    "out of scope",
    "not in my coverage",
    "not covered by my tools",
    "this is a question for the",
    "this is handled by the",
    "handled by the credit",
    "handled by the fx",
    "handled by the ois",
    "handled by the sovereign",
    "handled by the futures",
)

_CLARIFICATION_SIGNALS: tuple[str, ...] = (
    "could you clarify",
    "can you clarify",
    "please clarify",
    "could you specify",
    "can you specify",
    "please specify",
    "which do you mean",
    "which one do you mean",
    "did you mean",
    "to be sure,",
    "to confirm,",
)


def _classify_status(
    answer_markdown: str,
    tool_calls: list[ChildToolCallTrace],
    raw_tool_outputs: list[tuple[str, dict, str]],
) -> tuple[ChildStatus, Optional[str], Optional[str]]:
    """Deterministic post-hoc classification of a child run.

    Rules (no LLM call — latency-free on the hot path):

    1. If at least one tool call returned a non-error payload, the child
       did meaningful work → OK.
    2. If tool calls were made but every one returned an error → ERROR,
       with ``error_message`` aggregated from the per-tool error strings
       so the supervisor's synthesis step has actionable context.
    3. If no tool calls were made and the answer is empty → ERROR.
    4. If no tool calls were made and the answer contains an
       out-of-scope signal → OUT_OF_SCOPE.
    5. If no tool calls were made and the answer contains a clarification
       signal OR ends with a question → NEEDS_CLARIFICATION, with
       ``follow_up_question`` extracted from the prose.
    6. Fallback → OK (the child answered from domain knowledge without
       needing a tool; rare but legitimate for meta questions).

    Returns
    -------
    (status, follow_up_question, error_message)
        - ``follow_up_question`` is populated only for NEEDS_CLARIFICATION.
        - ``error_message`` is populated for ERROR paths (both
          all-tools-errored and empty-answer cases).
    """
    # Count successful vs errored tool outputs, capturing error strings.
    successful = 0
    errored = 0
    tool_errors: list[str] = []
    for tool_name, _params, raw in raw_tool_outputs:
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(payload, dict):
            continue
        if "error" in payload:
            errored += 1
            err_text = str(payload.get("error", "")).strip()
            if err_text:
                tool_errors.append(f"{tool_name}: {err_text}")
            else:
                tool_errors.append(f"{tool_name}: (unspecified error)")
        else:
            successful += 1

    # Rule 1: any successful tool → OK, BUT if some tools also errored,
    # attach a partial-failure note in error_message so the supervisor's
    # synthesis and the observability layer can see that part of a
    # compound same-domain query failed.  The child's prose is still
    # used as the user-facing answer (the child's LLM saw the tool
    # errors live, so it should already reflect partial coverage), but
    # status=OK + error_message=non-null lets downstream consumers
    # render an incomplete-data badge.
    if successful > 0:
        if errored > 0:
            partial = _summarise_tool_errors(tool_errors)
            partial_note = (
                f"Partial data: {errored} of {successful + errored} tool "
                f"call(s) in this run failed. {partial}"
            )
            return ChildStatus.OK, None, partial_note
        return ChildStatus.OK, None, None

    # Rule 2: tools were called but none succeeded → ERROR.
    if tool_calls and errored == len(tool_calls) and errored > 0:
        error_summary = _summarise_tool_errors(tool_errors)
        return ChildStatus.ERROR, None, error_summary

    # Rule 3: no tools and empty answer → ERROR.
    cleaned = answer_markdown.strip()
    if not cleaned:
        return (
            ChildStatus.ERROR,
            None,
            "The specialist produced no answer and called no tools.",
        )

    lowered = cleaned.lower()

    # Rule 4: out-of-scope prose signals.
    for signal in _OUT_OF_SCOPE_SIGNALS:
        if signal in lowered:
            return ChildStatus.OUT_OF_SCOPE, None, None

    # Rule 5: clarification signals or a trailing question.
    for signal in _CLARIFICATION_SIGNALS:
        if signal in lowered:
            return (
                ChildStatus.NEEDS_CLARIFICATION,
                _extract_last_question(cleaned),
                None,
            )

    if cleaned.endswith("?"):
        return (
            ChildStatus.NEEDS_CLARIFICATION,
            _extract_last_question(cleaned),
            None,
        )

    # Rule 6: default OK.
    return ChildStatus.OK, None, None


def _summarise_tool_errors(tool_errors: list[str]) -> str:
    """Compact, readable aggregation of per-tool error strings, bounded
    so a runaway child's error list doesn't explode the synthesis prompt.
    """
    if not tool_errors:
        return "All tool calls failed with no error message."
    if len(tool_errors) == 1:
        return tool_errors[0]
    # Cap each entry and the number of entries.
    capped = [e[:240] for e in tool_errors[:4]]
    suffix = ""
    if len(tool_errors) > 4:
        suffix = f" (+{len(tool_errors) - 4} more)"
    return "; ".join(capped) + suffix


def _extract_last_question(text: str) -> Optional[str]:
    """Pull the last question-like sentence out of a response for use as
    ``follow_up_question``.

    Looks at the final '?' and walks back to the nearest sentence
    boundary — including a prior '?' — to get a single trailing question.
    Example: for "JGB cash or JPY OIS? Which one do you mean?" this
    returns "Which one do you mean?" rather than the whole string.
    """
    if "?" not in text:
        return None
    end = text.rfind("?") + 1
    start = 0
    # Include '?' in the separators so multi-question replies return only
    # the trailing question, not the earlier ones.
    for sep in (".", "!", "?", "\n"):
        idx = text.rfind(sep, 0, end - 1)
        if idx > start:
            start = idx + 1
    question = text[start:end].strip()
    return question or None

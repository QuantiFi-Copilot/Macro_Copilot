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
  appends a "scope" SystemMessage telling the child to stay in its lane.
- **Structured output.**  The LangGraph ReAct loop still produces
  prose + tool_calls.  We parse the tool-call trajectory into structured
  ``FactRow`` entries so the supervisor's synthesis step consumes numbers,
  not the child's narrative.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Awaitable, Callable, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_mcp_adapters.client import MultiServerMCPClient
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
    ):
        self.domain = domain
        self._system_prompt = system_prompt
        self._mcp_servers = mcp_servers
        self._model_name = model_name
        self._temperature = temperature
        self._max_tokens = max_tokens

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
            # Always prepend the cached system message.  If the caller
            # added a scope hint (multi-domain path), it's already in
            # state["messages"] before the HumanMessage.
            messages_for_model = [self._cached_system_message] + state["messages"]
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

        memory = MemorySaver()
        self._graph = builder.compile(checkpointer=memory)

        self._is_open = True
        logger.info("[%s] session ready", self.domain.value)

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
        initial_messages: list = []
        if domain_boundary:
            initial_messages.append(
                SystemMessage(content=f"[scope for this query] {domain_boundary}")
            )
        initial_messages.append(HumanMessage(content=user_message))

        turn_config = {"configurable": {"thread_id": turn_thread_id}}

        # Accumulators we build from the event stream.
        answer_parts: list[str] = []
        tool_calls_seen: list[ChildToolCallTrace] = []
        raw_tool_outputs: list[tuple[str, dict, str]] = []  # (tool, params, raw_json)
        current_tool_name: Optional[str] = None
        current_tool_params: dict = {}
        current_tool_start: Optional[float] = None

        try:
            async for event in self._graph.astream_events(
                {"messages": initial_messages},
                config=turn_config,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})

                if kind == "on_tool_start":
                    current_tool_name = name
                    current_tool_start = time.monotonic()
                    tool_input = data.get("input", {})
                    if isinstance(tool_input, str):
                        try:
                            tool_input = json.loads(tool_input)
                        except (json.JSONDecodeError, TypeError):
                            tool_input = {}
                    current_tool_params = tool_input if isinstance(tool_input, dict) else {}

                    if on_event is not None:
                        label = make_tool_label(name, current_tool_params)
                        await on_event(
                            SessionEvent(
                                type="tool_call",
                                data={
                                    "tool": name,
                                    "label": label,
                                    "params": current_tool_params,
                                    "domain": self.domain.value,
                                },
                            )
                        )

                elif kind == "on_tool_end":
                    duration_ms = None
                    if current_tool_start is not None:
                        duration_ms = round((time.monotonic() - current_tool_start) * 1000)

                    # The tool output is a JSON string (per our MCP server
                    # convention).  Save the raw text for fact extraction.
                    tool_output_text = _stringify_tool_output(data.get("output"))
                    raw_tool_outputs.append(
                        (current_tool_name or name, current_tool_params, tool_output_text)
                    )
                    tool_calls_seen.append(
                        ChildToolCallTrace(
                            tool=current_tool_name or name,
                            params=current_tool_params,
                            duration_ms=duration_ms,
                        )
                    )

                    if on_event is not None:
                        await on_event(
                            SessionEvent(
                                type="tool_result",
                                data={
                                    "tool": current_tool_name or name,
                                    "domain": self.domain.value,
                                    "duration_ms": duration_ms,
                                },
                            )
                        )

                    current_tool_name = None
                    current_tool_start = None
                    current_tool_params = {}

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
        trace_dicts = [
            {"tool": t.tool, "params": t.params, "domain": self.domain.value}
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


def _extract_facts(
    raw_tool_outputs: list[tuple[str, dict, str]],
) -> list[FactRow]:
    """Parse each tool's JSON output and extract scalar metrics as FactRow
    entries.

    Shapes supported:
      - ``{"current_metrics": {...}}`` — most rates tools
      - ``{"results": [...]}``          — scanner tool
      - ``{"error": "..."}``            — skipped (not a fact)
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

    return facts


def _facts_from_current_metrics(
    tool_name: str,
    params: dict,
    cm: dict,
) -> list[FactRow]:
    """Flatten a ``current_metrics`` dict into FactRow entries.

    We pick out the numeric fields (spreads, yields, rates, z-scores,
    butterflies) and emit one FactRow per metric.  Non-numeric fields
    (date, labels) provide context for the facts as ``as_of`` or
    ``curve_family`` — they aren't emitted as their own rows.
    """
    as_of = cm.get("as_of_date")
    curve_family = cm.get("curve_family") or params.get("curve_family")

    # Every other scalar-looking field becomes a fact.
    # We skip fields that are pure context (already captured above) or
    # structural (rolling_window_days is useful but not a fact-about-the-market).
    skip_keys = {
        "as_of_date",
        "curve_family",
        "spread_label",
        "regime_description",
        "lookback_period",
    }

    out: list[FactRow] = []
    for key, value in cm.items():
        if key in skip_keys:
            continue
        if value is None:
            continue
        units = _infer_units(key)
        out.append(
            FactRow(
                tool=tool_name,
                curve_family=curve_family,
                metric=key,
                value=value,
                units=units,
                as_of=as_of,
            )
        )
    return out


def _facts_from_scanner_results(
    tool_name: str,
    results: list,
) -> list[FactRow]:
    """Scanner returns a list of ranked extreme rows; each row becomes
    a small set of facts keyed by its curve/tenor."""
    out: list[FactRow] = []
    for row in results:
        if not isinstance(row, dict):
            continue
        curve = row.get("curve_family")
        tenor = row.get("tenor")
        as_of = row.get("as_of_date") or row.get("trade_date")
        scope_label = f"{curve} {tenor}" if curve and tenor else curve
        for key in ("current_yield", "daily_change_bps", "z_score"):
            if key in row and row[key] is not None:
                out.append(
                    FactRow(
                        tool=tool_name,
                        curve_family=scope_label,
                        metric=key,
                        value=row[key],
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

    # Rule 1: any successful tool → trust the child's answer.
    if successful > 0:
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

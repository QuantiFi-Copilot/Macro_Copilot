"""
orchestrator/session.py — Copilot Session Manager
====================================================

Encapsulates the lifecycle of a single copilot conversation session:

    1. Start MCP subprocess(es) and discover tools.
    2. Build the LangGraph state machine with those tools.
    3. Handle multi-turn conversation with memory.
    4. Clean up MCP subprocess(es) on close.

This is the reusable core that both the CLI REPL (graph.py) and the
WebSocket endpoint (api/routes/chat.py) consume.  Neither of them
manages MCP subprocesses directly.

Cost optimisation
-----------------
Two key mechanisms reduce Anthropic API costs:

1.  **Explicit prompt caching** — The system prompt is structured as a
    content block with ``cache_control``, placing the cache breakpoint
    on the last *static* content.  Anthropic caches ``tools + system``
    (the stable prefix) and reads from cache on subsequent calls.
    The changing user message sits *after* the breakpoint and is not
    cached — which is correct.  See:
    https://platform.claude.com/docs/en/build-with-claude/prompt-caching

2.  **Stateless turns** — In deterministic mode (default), each user
    turn gets a unique ``thread_id`` so the checkpointer does not load
    prior conversation history.  Each call sends only
    ``[system + current_message]`` instead of the growing transcript.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from orchestrator.config import (
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
    MCP_SERVERS,
)
from orchestrator.prompts import RATES_AGENT_SYSTEM_PROMPT

logger = logging.getLogger("orchestrator.session")


# ============================================================================
# PROMPT CACHING — EXPLICIT BLOCK-LEVEL BREAKPOINT
# ============================================================================
# The system prompt is structured as a content block list with an explicit
# cache_control breakpoint.  Anthropic caches the full prefix in order:
#   tools → system → messages
# By placing cache_control on the system block, we cache tools + system.
# The user message (which changes every request) is AFTER the breakpoint
# and is NOT cached.
#
# This avoids the "common mistake" documented by Anthropic where automatic
# caching places the breakpoint on the last (changing) block and never
# gets cache reads across different questions.

CACHED_SYSTEM_MESSAGE = SystemMessage(
    content=[
        {
            "type": "text",
            "text": RATES_AGENT_SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
)


# ============================================================================
# USAGE LOGGING
# ============================================================================

def _log_usage(response) -> None:
    """Log token usage from an Anthropic model response.

    After implementing caching, you should see:
      - First call:  cache_create > 0, cache_read = 0
      - Later calls: cache_create = 0, cache_read > 0

    If you only see input_tokens with no cache fields, the cache
    breakpoint is not being honoured (check langchain-anthropic version).
    """
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return

    # usage_metadata may be a dict or a TypedDict-like object
    if not isinstance(meta, dict):
        try:
            meta = dict(meta)
        except (TypeError, ValueError):
            return

    input_details = meta.get("input_token_details", {}) or {}

    logger.info(
        "Token usage: input=%s output=%s cache_create=%s cache_read=%s",
        meta.get("input_tokens"),
        meta.get("output_tokens"),
        input_details.get("cache_creation"),
        input_details.get("cache_read"),
    )


# ============================================================================
# STREAMING EVENT TYPES
# ============================================================================

@dataclass
class SessionEvent:
    """A typed event emitted during streaming."""

    type: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, **self.data}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ============================================================================
# WORKSPACE CONTEXT DETECTION
# ============================================================================

_WORKSPACE_TOOLS = {
    "calculate_curve_spread_tool",
    "calculate_cross_market_spread_tool",
    "calculate_butterfly_tool",
    "scan_extremes_tool",
    "calculate_ois_curve_spread_tool",
}

_TOOL_LABEL_TEMPLATES = {
    "calculate_curve_spread_tool": lambda p: (
        f"Fetching {p.get('curve_family', '?')} "
        f"{p.get('short_tenor', '2Y')}/{p.get('long_tenor', '10Y')} spread"
    ),
    "calculate_ois_curve_spread_tool": lambda p: (
        f"Fetching OIS {p.get('curve_family', '?')} "
        f"{p.get('short_tenor', '2Y')}/{p.get('long_tenor', '10Y')} spread"
    ),
    "calculate_cross_market_spread_tool": lambda p: (
        f"Computing {p.get('curve_family_1', '?')}-{p.get('curve_family_2', '?')} "
        f"{p.get('tenor', '10Y')} spread"
    ),
    "calculate_butterfly_tool": lambda p: (
        f"Computing {p.get('curve_family', '?')} "
        f"{p.get('short_tenor', '2Y')}/{p.get('belly_tenor', '5Y')}/{p.get('long_tenor', '10Y')} butterfly"
    ),
    "classify_curve_regime_tool": lambda p: (
        f"Classifying {p.get('curve_family', '?')} "
        f"{p.get('lookback_period', '1d')} regime"
    ),
    "scan_extremes_tool": lambda p: "Scanning for z-score extremes",
    "get_yield_levels_tool": lambda p: (
        f"Fetching {p.get('curve_family', '?')} {p.get('tenor', '?')} yield"
    ),
}


def _make_tool_label(tool_name: str, params: dict) -> str:
    template = _TOOL_LABEL_TEMPLATES.get(tool_name)
    if template:
        try:
            return template(params)
        except Exception:
            pass
    return f"Running {tool_name}"


def _extract_workspace_context(tool_calls: list[dict]) -> Optional[dict]:
    workspace_items = [
        {"tool": tc["tool"], "params": tc["params"]}
        for tc in tool_calls
        if tc["tool"] in _WORKSPACE_TOOLS
    ]
    if not workspace_items:
        return None
    return {"tools": workspace_items, "tool_count": len(workspace_items)}


# ============================================================================
# COPILOT SESSION
# ============================================================================

class CopilotSession:
    """Manages one copilot conversation session.

    Use as an async context manager::

        async with CopilotSession() as session:
            async for event in session.stream("Hello"):
                ...

    Parameters
    ----------
    thread_id : str, optional
        Session identifier.  Auto-generated if not provided.
    stateless : bool, default True
        When True (deterministic mode), each user turn starts with a
        clean slate — no prior conversation history.  Set to False
        for future "intelligence mode" with multi-turn context.
    """

    def __init__(self, thread_id: str | None = None, stateless: bool = True):
        self.thread_id = thread_id or f"ws-{uuid.uuid4().hex[:12]}"
        self.stateless = stateless
        self._turn_counter = 0
        self._mcp_client: MultiServerMCPClient | None = None
        self._graph = None
        self._config = {"configurable": {"thread_id": self.thread_id}}
        self._is_open = False

    def _get_turn_config(self) -> dict:
        """Return the LangGraph config for the current turn.

        Stateless: unique thread_id per turn (no history accumulation).
        Stateful: shared thread_id (history grows across turns).
        """
        if self.stateless:
            self._turn_counter += 1
            turn_id = f"{self.thread_id}-turn-{self._turn_counter}"
            return {"configurable": {"thread_id": turn_id}}
        return self._config

    async def __aenter__(self) -> "CopilotSession":
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def open(self) -> None:
        """Start MCP subprocesses and build the graph."""
        if self._is_open:
            return

        logger.info("[%s] Starting MCP subprocesses...", self.thread_id)
        self._mcp_client = MultiServerMCPClient(MCP_SERVERS)
        tools = await self._mcp_client.get_tools()

        tool_names = [t.name for t in tools]
        logger.info("[%s] MCP tools discovered: %s", self.thread_id, tool_names)

        if not tools:
            raise RuntimeError("No tools discovered from MCP servers.")

        # Build LLM with tools — no model_kwargs for caching here.
        # Caching is handled by the explicit breakpoint on
        # CACHED_SYSTEM_MESSAGE (see module-level constant).
        model = ChatAnthropic(
            model=LLM_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
        model_with_tools = model.bind_tools(tools)

        # Build graph
        async def agent_node(state: MessagesState) -> dict:
            messages_for_model = [CACHED_SYSTEM_MESSAGE] + state["messages"]
            response = await model_with_tools.ainvoke(messages_for_model)
            _log_usage(response)
            return {"messages": [response]}

        tool_node = ToolNode(tools)

        builder = StateGraph(MessagesState)
        builder.add_node("rates_agent", agent_node)
        builder.add_node("tools", tool_node)
        builder.add_edge(START, "rates_agent")
        builder.add_conditional_edges("rates_agent", tools_condition)
        builder.add_edge("tools", "rates_agent")

        memory = MemorySaver()
        self._graph = builder.compile(checkpointer=memory)

        self._is_open = True
        logger.info("[%s] Session ready.", self.thread_id)

    async def close(self) -> None:
        """Shut down MCP subprocesses and release resources."""
        if not self._is_open:
            return

        logger.info("[%s] Closing session...", self.thread_id)

        if self._mcp_client is not None:
            try:
                if hasattr(self._mcp_client, "__aexit__"):
                    await self._mcp_client.__aexit__(None, None, None)
                elif hasattr(self._mcp_client, "close"):
                    await self._mcp_client.close()
            except Exception:
                logger.debug("[%s] MCP client cleanup exception (non-fatal)",
                             self.thread_id, exc_info=True)
            self._mcp_client = None

        self._graph = None
        self._is_open = False
        logger.info("[%s] Session closed.", self.thread_id)

    # ------------------------------------------------------------------
    # Non-streaming invoke (for CLI and simple use cases)
    # ------------------------------------------------------------------

    async def invoke(self, user_message: str) -> dict:
        """Send a message and get the full response."""
        if not self._is_open:
            raise RuntimeError("Session is not open.")

        turn_config = self._get_turn_config()
        result = await self._graph.ainvoke(
            {"messages": [HumanMessage(content=user_message)]},
            turn_config,
        )

        ai_message = result["messages"][-1]
        tool_msgs = [
            m for m in result["messages"]
            if isinstance(m, ToolMessage)
        ]

        tool_calls = []
        for tm in tool_msgs:
            tool_calls.append({
                "tool": tm.name,
                "params": {},
                "duration_ms": None,
            })

        return {
            "content": ai_message.content,
            "tool_calls": tool_calls,
            "workspace_context": _extract_workspace_context(tool_calls),
            "messages": result["messages"],
        }

    # ------------------------------------------------------------------
    # Streaming (for WebSocket)
    # ------------------------------------------------------------------

    async def stream(self, user_message: str) -> AsyncIterator[SessionEvent]:
        """Stream events for a single user turn."""
        if not self._is_open:
            raise RuntimeError("Session is not open.")

        yield SessionEvent(type="status", data={"status": "thinking"})

        tool_calls_seen: list[dict] = []
        current_tool_start: float | None = None
        current_tool_name: str | None = None
        current_tool_params: dict = {}
        tools_were_called = False
        token_started = False
        total_start = time.monotonic()
        turn_config = self._get_turn_config()

        try:
            async for event in self._graph.astream_events(
                {"messages": [HumanMessage(content=user_message)]},
                config=turn_config,
                version="v2",
            ):
                kind = event.get("event", "")
                name = event.get("name", "")
                data = event.get("data", {})

                # Tool call starts
                if kind == "on_tool_start":
                    tools_were_called = True
                    current_tool_name = name
                    current_tool_start = time.monotonic()

                    tool_input = data.get("input", {})
                    if isinstance(tool_input, str):
                        try:
                            tool_input = json.loads(tool_input)
                        except (json.JSONDecodeError, TypeError):
                            tool_input = {}
                    current_tool_params = tool_input if isinstance(tool_input, dict) else {}

                    label = _make_tool_label(name, current_tool_params)
                    yield SessionEvent(
                        type="tool_call",
                        data={"tool": name, "label": label, "params": current_tool_params},
                    )

                # Tool call completes
                elif kind == "on_tool_end":
                    duration_ms = None
                    if current_tool_start is not None:
                        duration_ms = round((time.monotonic() - current_tool_start) * 1000)

                    tool_calls_seen.append({
                        "tool": current_tool_name or name,
                        "params": current_tool_params,
                        "duration_ms": duration_ms,
                    })

                    yield SessionEvent(
                        type="tool_result",
                        data={"tool": current_tool_name or name, "duration_ms": duration_ms},
                    )

                    current_tool_name = None
                    current_tool_start = None
                    current_tool_params = {}

                # LLM token streaming
                elif kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk is None:
                        continue

                    content = ""
                    if hasattr(chunk, "content"):
                        if isinstance(chunk.content, str):
                            content = chunk.content
                        elif isinstance(chunk.content, list):
                            for block in chunk.content:
                                if isinstance(block, dict) and block.get("type") == "text":
                                    content += block.get("text", "")
                                elif isinstance(block, str):
                                    content += block

                    if content:
                        if not token_started and tools_were_called:
                            yield SessionEvent(type="status", data={"status": "synthesising"})
                        token_started = True
                        yield SessionEvent(type="token", data={"content": content})

        except Exception as exc:
            logger.exception("[%s] Stream error", self.thread_id)
            yield SessionEvent(type="error", data={"message": str(exc)})
            return

        # Done
        total_ms = round((time.monotonic() - total_start) * 1000)
        workspace_ctx = _extract_workspace_context(tool_calls_seen)

        yield SessionEvent(
            type="done",
            data={
                "workspace_context": workspace_ctx,
                "tool_calls": [
                    {"tool": tc["tool"], "duration_ms": tc["duration_ms"]}
                    for tc in tool_calls_seen
                ],
                "total_duration_ms": total_ms,
            },
        )

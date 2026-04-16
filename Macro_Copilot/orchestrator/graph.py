"""
orchestrator/graph.py — LangGraph Orchestrator (The Brain)
===========================================================

This module is purely routing logic.  It does three things:

    1. Connects to MCP servers and discovers tools.
    2. Wires a ReAct state machine (agent ↔ tools loop).
    3. Runs an interactive CLI for local testing.

Everything else lives where it belongs:

    - Prompts        → ``orchestrator/prompts.py``
    - Config / keys  → ``orchestrator/config.py``  (loaded from ``.env``)
    - Tool logic     → ``rates_agent/`` (behind the MCP wall)
    - DB credentials → ``database/database.py``  (env vars from Docker)

Running
-------
::

    python -m orchestrator.graph

Dependencies
------------
::

    pip install langchain-anthropic langchain-mcp-adapters langgraph mcp python-dotenv
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, MessagesState, START, END
from langgraph.prebuilt import ToolNode, tools_condition

# ---------------------------------------------------------------------------
# Internal imports — config and prompts live in their own modules
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.config import (  # noqa: E402
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_MAX_TOKENS,
    MCP_SERVERS,
    validate,
)
from orchestrator.prompts import RATES_AGENT_SYSTEM_PROMPT  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("orchestrator.graph")


# ===========================================================================
# GRAPH BUILDER
# ===========================================================================

async def build_graph():
    """
    Construct and compile the LangGraph state machine.

    1. Connect to every MCP server in ``config.MCP_SERVERS`` and
       discover their tools.
    2. Bind those tools to the LLM.
    3. Wire the ReAct loop: agent → (tools?) → agent → … → END.
    4. Attach a MemorySaver checkpointer for multi-turn memory.

    Returns the compiled graph and the MCP client reference.
    """

    # ------------------------------------------------------------------
    # 1. MCP Client — discover tools from all configured servers
    # ------------------------------------------------------------------
    mcp_client = MultiServerMCPClient(MCP_SERVERS)
    tools = await mcp_client.get_tools()

    tool_names = [t.name for t in tools]
    logger.info("MCP tools discovered: %s", tool_names)

    if not tools:
        raise RuntimeError(
            "No tools discovered from MCP servers.  Check that the "
            "rates_agent MCP server starts without errors:\n"
            "    python -m rates_agent.mcp_server"
        )

    # ------------------------------------------------------------------
    # 2. LLM — bind tools so the model can emit structured tool calls
    # ------------------------------------------------------------------
    model = ChatAnthropic(
        model=LLM_MODEL,
        temperature=LLM_TEMPERATURE,
        max_tokens=LLM_MAX_TOKENS,
    )
    model_with_tools = model.bind_tools(tools)

    # ------------------------------------------------------------------
    # 3. Graph nodes
    # ------------------------------------------------------------------

    async def rates_agent_node(state: MessagesState) -> dict:
        """
        Reasoning node.  Prepends the system prompt before every LLM
        call but never writes it to state (avoids duplication across
        turns in the checkpointer).
        """
        messages_for_model = [
            SystemMessage(content=RATES_AGENT_SYSTEM_PROMPT),
        ] + state["messages"]

        response = await model_with_tools.ainvoke(messages_for_model)
        return {"messages": [response]}

    tool_node = ToolNode(tools)

    # ------------------------------------------------------------------
    # 4. Wire the state machine
    # ------------------------------------------------------------------
    #
    #   START → rates_agent ──┬──→ END  (no tool calls)
    #                         │
    #                         └──→ tools → rates_agent  (loop)
    #
    #   Future: insert a "supervisor" node at START that routes to
    #   rates_agent OR fx_agent based on query domain.

    builder = StateGraph(MessagesState)

    builder.add_node("rates_agent", rates_agent_node)
    builder.add_node("tools", tool_node)

    builder.add_edge(START, "rates_agent")
    builder.add_conditional_edges("rates_agent", tools_condition)
    builder.add_edge("tools", "rates_agent")

    # ------------------------------------------------------------------
    # 5. Compile with checkpointer
    # ------------------------------------------------------------------
    memory = MemorySaver()
    graph = builder.compile(checkpointer=memory)

    logger.info("Graph compiled.  Nodes: %s", list(builder.nodes))
    return graph, mcp_client


# ===========================================================================
# INTERACTIVE CLI
# ===========================================================================

def _print_banner():
    print("\n" + "=" * 64)
    print("  MACRO COPILOT — Rates Agent v1")
    print("  Type 'exit' or 'quit' to stop.")
    print("=" * 64 + "\n")


def _print_tool_trace(messages: list) -> None:
    """Print a compact summary of tool calls that were executed."""
    tool_msgs = [m for m in messages if hasattr(m, "type") and m.type == "tool"]
    if not tool_msgs:
        return

    print("  ── tool trace ──")
    for tm in tool_msgs:
        try:
            data = json.loads(tm.content)
            if "error" in data:
                print(f"  ⚠  {tm.name}: {data['error'][:120]}")
            elif "current_metrics" in data:
                m = data["current_metrics"]
                # Spread tool output
                if "spread_label" in m:
                    print(
                        f"  ✓  {tm.name}: "
                        f"{m['spread_label']} = "
                        f"{m.get('current_spread_bps', '?')} bps | "
                        f"z = {m.get('current_z_score', 'n/a')} | "
                        f"Δ = {m.get('daily_change_bps', 'n/a')} bps"
                    )
                # Yield level tool output
                elif "current_yield_pct" in m:
                    print(
                        f"  ✓  {tm.name}: "
                        f"{m.get('curve_family', '?')} {m.get('tenor', '?')} = "
                        f"{m.get('current_yield_pct', '?')}% | "
                        f"z = {m.get('z_score', 'n/a')} | "
                        f"Δ1d = {m.get('daily_change_bps', 'n/a')} bps"
                    )
                else:
                    print(f"  ✓  {tm.name}: OK")
            else:
                print(f"  ✓  {tm.name}: OK")
        except (json.JSONDecodeError, TypeError):
            print(f"  ✓  {tm.name}: (raw output)")
    print()


async def chat_loop():
    """
    Interactive terminal loop.  Builds the graph once, then runs a
    multi-turn conversation until the user exits.
    """
    _print_banner()

    logger.info("Building graph and connecting to MCP servers...")
    graph, mcp_client = await build_graph()
    logger.info("Ready.\n")

    config = {"configurable": {"thread_id": "cli-session-1"}}

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except EOFError:
                break

            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                break

            try:
                result = await graph.ainvoke(
                    {"messages": [HumanMessage(content=user_input)]},
                    config,
                )
            except Exception as exc:
                logger.exception("Graph invocation failed")
                print(f"\n  [ERROR] {exc}\n")
                continue

            ai_message = result["messages"][-1]
            print(f"\nRates Agent: {ai_message.content}\n")
            _print_tool_trace(result["messages"])

    except KeyboardInterrupt:
        print("\n\nInterrupted.")
    finally:
        print("Shutting down MCP connections...")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    validate()
    asyncio.run(chat_loop())

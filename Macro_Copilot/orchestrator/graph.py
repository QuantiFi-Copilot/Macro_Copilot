"""
orchestrator/graph.py — LangGraph Orchestrator (The Brain)
===========================================================

This module provides the interactive CLI for local testing.  The graph
construction, caching, and stateless logic are all in ``session.py``.
The CLI simply wraps ``CopilotSession`` in a terminal REPL.

This ensures the CLI and the WebSocket API use **exactly the same code
path** — no drift between two separate graph builders.

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

from langchain_core.messages import ToolMessage

# ---------------------------------------------------------------------------
# Internal imports
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from orchestrator.config import validate  # noqa: E402
from orchestrator.session import CopilotSession  # noqa: E402

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
# CLI HELPERS
# ===========================================================================

def _print_banner():
    print("\n" + "=" * 64)
    print("  MACRO COPILOT — Rates Agent v1")
    print("  Type 'exit' or 'quit' to stop.")
    print("=" * 64 + "\n")


def _print_tool_trace(messages: list) -> None:
    """Print a compact summary of tool calls that were executed."""
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
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
                if "spread_label" in m:
                    print(
                        f"  ✓  {tm.name}: "
                        f"{m['spread_label']} = "
                        f"{m.get('current_spread_bps', '?')} bps | "
                        f"z = {m.get('current_z_score', 'n/a')} | "
                        f"Δ = {m.get('daily_change_bps', 'n/a')} bps"
                    )
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


# ===========================================================================
# INTERACTIVE CLI
# ===========================================================================

async def chat_loop():
    """
    Interactive terminal loop.  Uses CopilotSession(stateless=True)
    so each turn starts fresh — no conversation history accumulation,
    same behaviour as the WebSocket endpoint.
    """
    _print_banner()

    logger.info("Building graph and connecting to MCP servers...")

    async with CopilotSession(thread_id="cli", stateless=True) as session:
        logger.info("Ready.\n")

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
                    result = await session.invoke(user_input)
                except Exception as exc:
                    logger.exception("Graph invocation failed")
                    print(f"\n  [ERROR] {exc}\n")
                    continue

                print(f"\nRates Agent: {result['content']}\n")
                _print_tool_trace(result.get("messages", []))

        except KeyboardInterrupt:
            print("\n\nInterrupted.")

    print("Session closed.")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    validate()
    asyncio.run(chat_loop())

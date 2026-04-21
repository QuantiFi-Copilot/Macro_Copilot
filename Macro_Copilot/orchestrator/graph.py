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
import logging
import sys
from pathlib import Path

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


def _print_route(route: dict | None) -> None:
    """Print the supervisor's routing decision (observability)."""
    if not route:
        return
    action = route.get("action", "?")
    domains = route.get("domains") or []
    rationale = route.get("rationale", "")
    pretty = f"[{action}]"
    if domains:
        pretty += f" → {', '.join(domains)}"
    if rationale:
        pretty += f"  ({rationale})"
    print(f"  ── route ──  {pretty}\n")


def _print_tool_trace(tool_calls: list) -> None:
    """Print a compact summary of tool calls that were executed."""
    if not tool_calls:
        return

    print("  ── tool trace ──")
    for tc in tool_calls:
        tool = tc.get("tool", "?")
        domain = tc.get("domain", "?")
        dur = tc.get("duration_ms")
        dur_str = f" ({dur}ms)" if dur is not None else ""
        print(f"  ✓  [{domain}] {tool}{dur_str}")
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

                _print_route(result.get("route"))
                print(f"\nRates Agent: {result['content']}\n")
                _print_tool_trace(result.get("tool_calls", []))

        except KeyboardInterrupt:
            print("\n\nInterrupted.")

    print("Session closed.")


# ===========================================================================
# ENTRY POINT
# ===========================================================================

if __name__ == "__main__":
    validate()
    asyncio.run(chat_loop())

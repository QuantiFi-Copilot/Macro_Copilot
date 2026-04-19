"""
test_ws_chat.py — WebSocket Chat Test Client
==============================================

A simple terminal client for testing the copilot WebSocket endpoint
without a browser.  Connects, waits for 'ready', then enters a
REPL loop.

Usage
-----
::

    python tests/test_ws_chat.py

Requires ``websockets`` (pip install websockets).
"""

import asyncio
import json
import sys


async def main():
    try:
        import websockets
    except ImportError:
        print("Install websockets first:  pip install websockets")
        sys.exit(1)

    url = "ws://localhost:8000/api/chat"
    print(f"Connecting to {url}...")

    async with websockets.connect(url, ping_timeout=None) as ws:
        # Wait for the 'ready' event
        raw = await ws.recv()
        event = json.loads(raw)
        print(f"  [{event['type']}] {event.get('message', '')}")

        if event["type"] == "error":
            print("Server error during init.  Exiting.")
            return

        print("\n" + "=" * 60)
        print("  COPILOT WS TEST CLIENT")
        print("  Type a query and watch events stream back.")
        print("  Type 'exit' to quit.")
        print("=" * 60 + "\n")

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input or user_input.lower() in ("exit", "quit", "q"):
                break

            # Send user message
            msg = json.dumps({"type": "user_message", "content": user_input})
            await ws.send(msg)

            # Receive and display events until 'done' or 'error'
            full_text = ""
            trace_lines = []

            while True:
                raw = await ws.recv()
                event = json.loads(raw)
                etype = event.get("type", "")

                if etype == "status":
                    status = event.get("status", "")
                    if status == "thinking":
                        print("  ● Understanding query...", end="", flush=True)
                    elif status == "synthesising":
                        print("\n  ● Constructing analysis...", end="", flush=True)

                elif etype == "tool_call":
                    label = event.get("label", event.get("tool", ""))
                    print(f"\n  ● {label}...", end="", flush=True)

                elif etype == "tool_result":
                    dur = event.get("duration_ms")
                    dur_str = f" ({dur}ms)" if dur else ""
                    tool = event.get("tool", "")
                    print(f" ✓{dur_str}", flush=True)
                    trace_lines.append(f"  ✓ {tool}{dur_str}")

                elif etype == "token":
                    content = event.get("content", "")
                    if not full_text:
                        print("\n")  # Newline before first token
                    full_text += content
                    print(content, end="", flush=True)

                elif etype == "done":
                    total = event.get("total_duration_ms", 0)
                    tools_used = event.get("tool_calls", [])
                    ws_ctx = event.get("workspace_context")

                    print(f"\n\n  ── {len(tools_used)} tools · {total}ms total ──")
                    for tl in trace_lines:
                        print(tl)

                    if ws_ctx:
                        print(f"  📊 Workspace context: {json.dumps(ws_ctx, indent=2)}")

                    print()
                    break

                elif etype == "error":
                    print(f"\n  [ERROR] {event.get('message', 'Unknown error')}\n")
                    break

    print("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

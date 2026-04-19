"""
chat.py — Copilot WebSocket Endpoint
======================================

Manages one ``CopilotSession`` per WebSocket connection.  The session
starts MCP subprocesses on connect and tears them down on disconnect.

Protocol
--------
Client → Server::

    { "type": "user_message", "content": "What is the BTP-Bund spread?" }

Server → Client (streamed)::

    { "type": "status",      "status": "thinking" }
    { "type": "tool_call",   "tool": "...", "label": "...", "params": {...} }
    { "type": "tool_result", "tool": "...", "duration_ms": 84 }
    { "type": "status",      "status": "synthesising" }
    { "type": "token",       "content": "The " }
    { "type": "token",       "content": "BTP-Bund..." }
    { "type": "done",        "workspace_context": {...}|null, "tool_calls": [...], "total_duration_ms": 2800 }

Error handling::

    { "type": "error", "message": "..." }

Connection lifecycle
--------------------
1. Client connects → server starts MCP subprocesses + builds graph (~1-2s).
2. Server sends ``{ "type": "ready" }`` when the session is initialized.
3. Client sends ``user_message`` → server streams response events.
4. Repeat step 3 for multi-turn conversation.
5. Client disconnects → server tears down MCP subprocesses.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from orchestrator.config import validate as validate_config
from orchestrator.session import CopilotSession

logger = logging.getLogger("api.routes.chat")

router = APIRouter()


# ============================================================================
# WEBSOCKET ENDPOINT
# ============================================================================

@router.websocket("/chat")
async def copilot_chat(ws: WebSocket):
    """
    One WebSocket connection = one CopilotSession = one MCP subprocess.

    The session persists for the lifetime of the connection, maintaining
    conversation history via LangGraph's MemorySaver checkpointer.
    """
    await ws.accept()
    logger.info("WebSocket connected: %s", ws.client)

    # ------------------------------------------------------------------
    # Validate config before starting the session
    # ------------------------------------------------------------------
    try:
        validate_config()
    except SystemExit:
        await _send_event(ws, "error", {
            "message": "Server configuration error.  Check ANTHROPIC_API_KEY.",
        })
        await ws.close(code=1011, reason="Configuration error")
        return

    # ------------------------------------------------------------------
    # Start the copilot session (MCP subprocesses + graph)
    # ------------------------------------------------------------------
    session: CopilotSession | None = None
    try:
        session = CopilotSession()
        await session.open()

        await _send_event(ws, "ready", {
            "thread_id": session.thread_id,
            "message": "Copilot session initialized.  Ready for queries.",
        })

    except Exception as exc:
        logger.exception("Failed to initialize copilot session")
        await _send_event(ws, "error", {
            "message": f"Failed to start copilot: {exc}",
        })
        await ws.close(code=1011, reason="Session initialization failed")
        return

    # ------------------------------------------------------------------
    # Message loop
    # ------------------------------------------------------------------
    try:
        while True:
            # Wait for a message from the client
            raw = await ws.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_event(ws, "error", {
                    "message": "Invalid JSON",
                })
                continue

            msg_type = msg.get("type")
            content = msg.get("content", "").strip()

            if msg_type != "user_message" or not content:
                await _send_event(ws, "error", {
                    "message": (
                        "Expected { \"type\": \"user_message\", "
                        "\"content\": \"...\" }"
                    ),
                })
                continue

            # ----------------------------------------------------------
            # Stream the response
            # ----------------------------------------------------------
            logger.info("[%s] User: %s", session.thread_id, content[:120])

            try:
                async for event in session.stream(content):
                    await ws.send_text(event.to_json())

            except WebSocketDisconnect:
                raise  # Let the outer handler catch it

            except Exception as exc:
                logger.exception(
                    "[%s] Error during stream", session.thread_id
                )
                await _send_event(ws, "error", {
                    "message": f"Stream error: {exc}",
                })

    except WebSocketDisconnect:
        logger.info(
            "WebSocket disconnected: %s (thread=%s)",
            ws.client,
            session.thread_id if session else "?",
        )
    except Exception as exc:
        logger.exception("Unexpected WebSocket error")
        try:
            await _send_event(ws, "error", {
                "message": f"Unexpected error: {exc}",
            })
        except Exception:
            pass  # Connection may already be closed

    # ------------------------------------------------------------------
    # Cleanup — always runs, even on unclean disconnect
    # ------------------------------------------------------------------
    finally:
        if session is not None:
            try:
                await session.close()
            except Exception:
                logger.debug("Session cleanup exception (non-fatal)", exc_info=True)
        logger.info("WebSocket session fully cleaned up.")


# ============================================================================
# HELPERS
# ============================================================================

async def _send_event(ws: WebSocket, event_type: str, data: dict) -> None:
    """Send a typed JSON event to the client."""
    payload = {"type": event_type, **data}
    await ws.send_text(json.dumps(payload, default=str))

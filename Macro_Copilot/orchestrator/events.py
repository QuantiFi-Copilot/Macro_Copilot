"""
orchestrator/events.py — Streaming event contract + tool metadata
==================================================================

Typed events emitted to the WebSocket during a copilot session, plus the
tool-name → label / workspace-tool metadata used to render the execution
trace on the frontend.

Event types
-----------
- ``status``            generic status chip: {"status": "thinking" | "synthesising" | "routing" | ...}
- ``route_decision``    supervisor's pick: {"action", "domains", "rationale", "adjustments"}
- ``child_started``     a domain child begins: {"domain": "ois"}
- ``child_finished``    a domain child finishes: {"domain", "status", "duration_ms"}
- ``tool_call``         a child's tool is invoked: {"tool", "label", "params", "domain"}
- ``tool_result``       a child's tool returns: {"tool", "domain", "duration_ms", "error"?}
                        ``error`` is the tool's error string when the MCP
                        output was ``{"error": "..."}``; null/absent on
                        success.  Frontends should render per-tool
                        failure state based on this field.
- ``token``             LLM output chunk: {"content"}
- ``synthesis_started`` supervisor begins multi-domain synthesis: {}
- ``clarification``     supervisor asked the user to clarify: {"question"}
- ``done``              final event: {"workspace_context", "tool_calls", "total_duration_ms"}
                        Each ``tool_calls`` entry carries
                        {"tool", "domain", "duration_ms", "error"?}
                        with the same error semantics as ``tool_result``.
- ``error``             {"message"}

The frontend ignores unknown event types (forward-compatible), so we can add
new ones without breaking the chat UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# SESSION EVENT
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
# TOOLS THAT WARRANT A FRONTEND WORKSPACE VIEW
# ============================================================================
# When one of these tools is called, the session emits a workspace_context
# block in the ``done`` event so the UI can render a "See more in workspace"
# button for the user to open a dedicated analytical surface.

_WORKSPACE_TOOLS: set[str] = {
    # Sovereign bonds
    "calculate_curve_spread_tool",
    "calculate_cross_market_spread_tool",
    "calculate_butterfly_tool",
    "scan_extremes_tool",
    # OIS
    "calculate_ois_curve_spread_tool",
    "calculate_ois_cross_market_spread_tool",
    "calculate_ois_forward_rate_tool",
    "scan_ois_extremes_tool",
    # FX
    "get_fx_spot_level_tool",
    "get_fx_carry_tool",
    "get_fx_forward_curve_tool",
    # rate_level is intentionally NOT in the workspace set — a single-
    # point yield/rate is better viewed inline in the chat than in a
    # dedicated analytical workspace (same decision as get_yield_levels
    # on the sovereign side).
}


# ============================================================================
# TOOL LABEL TEMPLATES
# ============================================================================
# Maps a tool name + parameter dict to a short human-readable label, which is
# rendered in the chat's tool-execution trace (e.g. "Fetching UST 2Y/10Y spread").

_TOOL_LABEL_TEMPLATES: dict = {
    # Sovereign bonds
    "calculate_curve_spread_tool": lambda p: (
        f"Fetching {p.get('curve_family', '?')} "
        f"{p.get('short_tenor', '2Y')}/{p.get('long_tenor', '10Y')} spread"
    ),
    "calculate_cross_market_spread_tool": lambda p: (
        f"Computing {p.get('curve_family_1', '?')}-{p.get('curve_family_2', '?')} "
        f"{p.get('tenor', '10Y')} spread"
    ),
    "calculate_butterfly_tool": lambda p: (
        f"Computing {p.get('curve_family', '?')} "
        f"{p.get('short_tenor', '2Y')}/{p.get('belly_tenor', '5Y')}/"
        f"{p.get('long_tenor', '10Y')} butterfly"
    ),
    "classify_curve_move_tool": lambda p: (
        f"Classifying {p.get('curve_family', '?')} "
        f"{p.get('lookback_period', '1d')} move"
    ),
    "scan_extremes_tool": lambda p: "Scanning for z-score extremes",
    "get_yield_levels_tool": lambda p: (
        f"Fetching {p.get('curve_family', '?')} {p.get('tenor', '?')} yield"
    ),
    # OIS
    "calculate_ois_rate_level_tool": lambda p: (
        f"Fetching OIS {p.get('curve_family', '?')} {p.get('tenor', '?')} rate"
    ),
    "calculate_ois_curve_spread_tool": lambda p: (
        f"Fetching OIS {p.get('curve_family', '?')} "
        f"{p.get('short_tenor', '2Y')}/{p.get('long_tenor', '10Y')} spread"
    ),
    "calculate_ois_forward_rate_tool": lambda p: _ois_forward_label(p),
    "calculate_ois_cross_market_spread_tool": lambda p: (
        f"Computing OIS {p.get('curve_family_1', '?')}-{p.get('curve_family_2', '?')} "
        f"{p.get('tenor', '10Y')} spread"
    ),
    "scan_ois_extremes_tool": lambda p: "Scanning OIS for z-score extremes",
    # FX
    "get_fx_spot_level_tool": lambda p: (
        f"Fetching {p.get('pair', 'EURUSD')} spot snapshot"
    ),
    "get_fx_carry_tool": lambda p: (
        f"Ranking FX carry for {p.get('tenor', '1M')}"
    ),
    "get_fx_forward_curve_tool": lambda p: (
        f"Fetching {p.get('pair', 'EURUSD')} forward curve"
    ),
}


def _ois_forward_label(p: dict) -> str:
    """Render a forward_rate label covering both tenor-based and
    date-based invocations."""
    curve = p.get("curve_family", "?")
    st = p.get("start_tenor")
    et = p.get("end_tenor")
    sd = p.get("start_date")
    ed = p.get("end_date")
    if st and et:
        return f"Computing OIS {curve} {st}/{et} forward"
    if sd and ed:
        return f"Computing OIS {curve} forward {sd} to {ed}"
    return f"Computing OIS {curve} forward"


def make_tool_label(tool_name: str, params: dict) -> str:
    """Render a human-readable label for a tool call, or fall back to the
    raw tool name if no template is registered."""
    template = _TOOL_LABEL_TEMPLATES.get(tool_name)
    if template is not None:
        try:
            return template(params)
        except Exception:
            pass
    return f"Running {tool_name}"


def is_workspace_tool(tool_name: str) -> bool:
    """Whether this tool's output should surface a workspace button."""
    return tool_name in _WORKSPACE_TOOLS


def extract_workspace_context(tool_calls: list[dict]) -> Optional[dict]:
    """Given the list of tool calls seen in a turn, return the workspace
    context dict (or None if nothing qualifies)."""
    workspace_items = [
        {"tool": tc["tool"], "params": tc.get("params", {}), "domain": tc.get("domain")}
        for tc in tool_calls
        if tc["tool"] in _WORKSPACE_TOOLS
    ]
    if not workspace_items:
        return None
    return {"tools": workspace_items, "tool_count": len(workspace_items)}

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

PR 10 — workflow-template events (the LLM can choose to run a workflow
DAG instead of routing to a domain agent).  These flow on the same
WebSocket alongside the existing event types so the frontend's
streaming-message reducer can handle them as additional cases:

- ``workflow_route_decision`` workflow router's pick:
                        {"action": "route" | "out_of_scope" | "clarify",
                         "template_id"?: str,
                         "slot_values"?: dict,
                         "rationale": str,
                         "clarification_question"?: str,
                         "adjustments": list[str]}
- ``workflow_status``   workflow execution status chip:
                        {"status": "running" | "complete" | "error"}
- ``workflow_result``   final workflow execution envelope:
                        {"ok": bool,
                         "template_id": str,
                         "terminal_artifact"?: dict,
                         "workflow_lineage_summary"?: str,
                         "error"?: str}

The frontend ignores unknown event types (forward-compatible), so we can add
new ones without breaking the chat UI.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional


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
#
# PR-B-α — the set is now COMPUTED from the backend's authoritative
# primitive registry (``rates_agent.workflows._PRIMITIVE_SPECS``).  Adding
# a new ``PrimitiveSpec`` entry to that registry automatically opts the
# tool into Ask → Build hand-off — no second hand-maintained list to
# keep in sync.  Pre-PR-B-α this file owned a small hard-coded subset
# that drifted from the registry, leaving real tools (``get_yield_levels_tool``,
# ``calculate_swap_spread_tool``, ``calculate_breakeven_inflation_tool``,
# ``calculate_zscore_custom_tool``, ``build_sovereign_yield_panel_tool``,
# ``compute_financing_rate_tool``, ``get_ois_rate_level_tool``) silently
# excluded.  Symptom: "Open in Build" appeared disabled on every Ask
# answer that used only these tools — see the screenshots attached to
# PR-B-α's description.
#
# The frontend ``src/components/ask/messages/__tests__/buildHandoffContract.test.ts``
# (PR-B-α) snapshots this set and asserts every frontend-routeable tool
# is included, so the contract stays honest as new primitives ship.

# Manifest-only typed-view tools — declared in the rates manifest but
# with NO ``PrimitiveSpec`` entry (no ``POST /api/v1/tools/{name}/run``
# endpoint).  Build still has rendering for each of these:
#   - ``calculate_butterfly_tool``   → typed butterfly chart
#   - ``scan_extremes_tool``         → typed scanner table
#   - ``classify_curve_move_tool``   → typed regime classification
#   - ``scan_ois_extremes_tool``     → honest unsupported-known card
# We include them explicitly so Ask traces using these tools still
# emit a workspace_context that the frontend can decode into the right
# read-only surface.
_MANIFEST_ONLY_BUILD_TOOLS: frozenset[str] = frozenset(
    {
        "calculate_butterfly_tool",
        "scan_extremes_tool",
        "classify_curve_move_tool",
        "scan_ois_extremes_tool",
    }
)

# MCP-exposed name → registry-canonical name aliases.  Some tools are
# defined under one name on the MCP server (``@mcp.tool()`` decorator)
# but registered in ``_PRIMITIVE_SPECS`` under a different name — the
# rates ``calculate_ois_rate_level_tool`` MCP tool maps to the
# ``get_ois_rate_level_tool`` primitive spec.  When the LLM invokes
# the tool via MCP the trace records the MCP name; we accept BOTH
# names in the workspace gate so the route fires regardless of which
# entry point the tool came through (the frontend's
# ``normalizeToolName`` then canonicalises before lookup).  Tested
# explicitly in ``tests/test_workspace_handoff_completeness.py``.
_MCP_ALIAS_TO_CANONICAL: Mapping[str, str] = {
    "calculate_ois_rate_level_tool": "get_ois_rate_level_tool",
}


def _compute_workspace_tools() -> frozenset[str]:
    """Build the canonical workspace-tools set.

    Composition:
      1. Every key in ``rates_agent.workflows._PRIMITIVE_SPECS`` (via
         ``known_rates_primitives``).  This is the BACKEND's
         authoritative list of runnable primitives — every entry has a
         working ``POST /api/v1/tools/{name}/run`` endpoint.
      2. Plus ``_MANIFEST_ONLY_BUILD_TOOLS`` for typed-view manifest
         entries.
      3. Plus the keys of ``_MCP_ALIAS_TO_CANONICAL`` so an Ask trace
         recorded under the MCP-exposed name still passes the gate.

    Lazy import + memoisation:
      - ``known_rates_primitives`` transitively imports the rates
        analytics stack (pandas / numpy / etc.).  Importing
        ``orchestrator.events`` at module load time should NOT require
        that heavy chain — pre-PR-B-α it did not.  We defer the import
        until the function is first called and memoise the result in
        ``_workspace_tools_cache`` so subsequent calls are O(1).
      - The cache is reset by ``_reset_workspace_tools_for_test`` so
        tests that monkeypatch ``known_rates_primitives`` see fresh
        values.
    """
    from rates_agent.workflows import known_rates_primitives

    out: set[str] = set(known_rates_primitives())
    out.update(_MANIFEST_ONLY_BUILD_TOOLS)
    out.update(_MCP_ALIAS_TO_CANONICAL.keys())
    return frozenset(out)


# Lazy memoisation — see ``_compute_workspace_tools`` docstring.
_workspace_tools_cache: Optional[frozenset[str]] = None


def _get_workspace_tools() -> frozenset[str]:
    """Module-private accessor.  Computes the set on first access and
    memoises it.  Use this internally; downstream code should call
    ``is_workspace_tool`` or read ``workspace_tools_snapshot`` instead. """
    global _workspace_tools_cache
    if _workspace_tools_cache is None:
        _workspace_tools_cache = _compute_workspace_tools()
    return _workspace_tools_cache


def _reset_workspace_tools_for_test() -> None:
    """Test hook — clears the memoised set so tests that monkeypatch
    ``rates_agent.workflows.known_rates_primitives`` get a fresh
    computation on the next call.  No production code path should
    ever call this."""
    global _workspace_tools_cache
    _workspace_tools_cache = None


# Module-level proxy for the legacy ``_WORKSPACE_TOOLS`` symbol that a
# few existing call sites still reference directly (string-membership
# checks).  We can't make this a property on a module, so we expose a
# ``__getattr__`` that resolves the symbol on first access.
def __getattr__(name: str) -> Any:
    if name == "_WORKSPACE_TOOLS":
        return _get_workspace_tools()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    # PR-B-α — OIS rate-level has a known MCP/registry name asymmetry
    # (see ``_MCP_ALIAS_TO_CANONICAL``: ``rates_agent/ois/mcp_server.py``
    # exposes ``calculate_ois_rate_level_tool``; the workflow registry's
    # ``_PRIMITIVE_SPECS`` uses ``get_ois_rate_level_tool``).  Pre-PR-B-α
    # only the MCP name had a label entry; a workflow-driven call landed
    # under the registry name and fell through to the generic
    # ``f"Running {tool_name}"`` fallback.  We register the same label
    # under both names so the chat trace renders the same friendly
    # string regardless of which entry point invoked the tool.
    "calculate_ois_rate_level_tool": lambda p: (
        f"Fetching OIS {p.get('curve_family', '?')} {p.get('tenor', '?')} rate"
    ),
    "get_ois_rate_level_tool": lambda p: (
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
    # Analytical models
    "calculate_rolling_regression_tool": lambda p: _rolling_regression_label(p),
    "calculate_pca_yield_curve_tool": lambda p: (
        f"PCA on {p.get('curve_family', '?')} curve "
        f"({p.get('n_components', 3)} components)"
    ),
    "calculate_yield_change_attribution_pca_tool": lambda p: (
        f"PCA attribution · {p.get('curve_family', '?')} {p.get('target_tenor', '?')}"
    ),
    "calculate_half_life_tool": lambda p: "Estimating mean-reversion half-life",
    "calculate_beta_adjusted_spread_tool": lambda p: "Computing beta-adjusted spread",
}


def _rolling_regression_label(p: dict) -> str:
    """Render a rolling_regression label.  Both target_spec and
    regressor_specs are nested, so we walk into them when the dict
    shape is the canonical one."""
    target = p.get("target_spec") or {}
    regs = p.get("regressor_specs") or []
    target_label = (
        f"{target.get('curve_family', '?')} {target.get('tenor', '?')}"
        if isinstance(target, dict) else "?"
    )
    if isinstance(regs, list) and regs:
        first = regs[0] if isinstance(regs[0], dict) else {}
        reg_label = f"{first.get('curve_family', '?')} {first.get('tenor', '?')}"
        if len(regs) > 1:
            reg_label += f" + {len(regs) - 1} more"
    else:
        reg_label = "?"
    win = p.get("regression_window_days", "?")
    return f"Rolling β · {target_label} ~ {reg_label} ({win}d window)"


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
    return tool_name in _get_workspace_tools()


def workspace_tools_snapshot() -> list[str]:
    """Return the canonical workspace-tools set as a sorted list of
    names.  Exposed for the frontend contract test + diagnostics —
    snapshot consumers compare this list against the set of tools the
    Build canvas knows how to route.  See
    ``tests/test_workspace_handoff_completeness.py`` and the matching
    TypeScript snapshot at
    ``UI/macro-copilot-dashboard-polished/src/components/ask/messages/__tests__/buildHandoffContract.test.ts``.
    """
    return sorted(_get_workspace_tools())


def extract_workspace_context(tool_calls: list[dict]) -> Optional[dict]:
    """Given the list of tool calls seen in a turn, return the workspace
    context dict (or None if nothing qualifies).

    PR-B-α — preserves invocation order, params, AND optional
    ``status`` / ``error`` fields when the source trace recorded them.
    Per-entry shape:

        {
            "tool": str,
            "params": dict,
            "domain": str | None,
            # Optional, only present when the source trace carried it:
            "status": "ok" | "error",
            "error": str,           # the tool's error string when status == "error"
            "duration_ms": int,
        }

    All optional fields are added without changing the wire shape of
    existing entries — pre-PR-B-α consumers only read ``tool`` /
    ``params`` and ignore the rest.  The frontend's
    ``WorkspaceContext`` TypeScript type was widened to declare the
    optional fields so future surfaces (e.g. an honest "this tool
    failed" tile next to the working cards) can consume them safely.
    """
    ws_tools = _get_workspace_tools()
    workspace_items: list[dict[str, Any]] = []
    for tc in tool_calls:
        if tc["tool"] not in ws_tools:
            continue
        item: dict[str, Any] = {
            "tool": tc["tool"],
            "params": tc.get("params", {}),
            "domain": tc.get("domain"),
        }
        # Optional propagations — only emit when the source trace had
        # them.  Avoids polluting the JSON envelope with null fields
        # that would force every consumer to handle them.
        err = tc.get("error")
        if err is not None:
            item["status"] = "error"
            item["error"] = err
        elif "status" in tc:
            # An explicit ``status`` from upstream (e.g. "ok") — pass
            # through verbatim so future statuses (e.g. "timeout")
            # surface without an events.py change.
            item["status"] = tc["status"]
        if "duration_ms" in tc and tc["duration_ms"] is not None:
            item["duration_ms"] = tc["duration_ms"]
        workspace_items.append(item)
    if not workspace_items:
        return None
    return {"tools": workspace_items, "tool_count": len(workspace_items)}

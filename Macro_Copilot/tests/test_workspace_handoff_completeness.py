"""
tests/test_workspace_handoff_completeness.py — PR-B-α coverage lock
====================================================================

Locks the workspace-tools gate that decides whether an Ask trace
emits a ``workspace_context`` block (and therefore whether the
frontend's "Open in Build" button is enabled).

Background
----------
Pre-PR-B-α ``orchestrator/events.py`` owned a hand-maintained
``_WORKSPACE_TOOLS`` set that drifted from the backend's authoritative
primitive registry.  Symptom: Ask answers that ran only
``get_yield_levels_tool`` / ``calculate_breakeven_inflation_tool`` /
``calculate_swap_spread_tool`` / ``calculate_zscore_custom_tool`` /
``build_sovereign_yield_panel_tool`` / ``compute_financing_rate_tool``
/ ``get_ois_rate_level_tool`` produced NO ``workspace_context``, so
"Open in Build" was disabled even though Build had a generic-builder
or typed view for every one of them.

PR-B-α derives the set from ``rates_agent.workflows._PRIMITIVE_SPECS``
plus an explicit manifest-only typed-view list and an MCP alias
mapping.  These checks lock the new contract:

  1. Every backend ``_PRIMITIVE_SPECS`` key appears in
     ``_WORKSPACE_TOOLS`` — adding a new primitive auto-opts it into
     hand-off without a second list to update.

  2. Each manifest-only typed-view tool is in the set so the typed
     ``cross_market`` / ``butterfly`` / ``scanner`` / ``regime`` /
     ``unsupported_known`` cards still render off Ask traces.

  3. The OIS-rate-level MCP / registry name asymmetry is reconciled:
     the MCP-exposed name ``calculate_ois_rate_level_tool`` AND the
     registry name ``get_ois_rate_level_tool`` both pass
     ``is_workspace_tool``.  Pre-PR-B-α only one half passed.

  4. ``extract_workspace_context`` preserves invocation order +
     params + the new optional ``status`` / ``error`` / ``duration_ms``
     metadata when the source trace carries it.  Wire shape is
     backward-compatible: pre-PR-B-α consumers that read only
     ``tool`` / ``params`` continue to work; new consumers read the
     optional fields to render honest "this tool failed" tiles.
"""

from __future__ import annotations

from typing import Optional

import pytest

from orchestrator import events as ev


# ---------------------------------------------------------------------------
# Fixture — a tiny stub for ``known_rates_primitives`` keeps the test
# independent of pandas/numpy.  Real production code imports the real
# function lazily.
# ---------------------------------------------------------------------------


STUB_RATES_PRIMITIVES: list[str] = [
    # Sovereign bonds — matches the keys in
    # ``rates_agent.workflows._PRIMITIVE_SPECS`` as of PR-B-α.
    "calculate_curve_spread_tool",
    "calculate_cross_market_spread_tool",
    "calculate_swap_spread_tool",
    "get_yield_levels_tool",
    "calculate_breakeven_inflation_tool",
    "calculate_zscore_custom_tool",
    "build_sovereign_yield_panel_tool",
    "compute_financing_rate_tool",
    # OIS
    "calculate_ois_curve_spread_tool",
    "calculate_ois_cross_market_spread_tool",
    "calculate_ois_forward_rate_tool",
    "get_ois_rate_level_tool",
    # Analytical models
    "calculate_rolling_regression_tool",
    "calculate_pca_yield_curve_tool",
    "calculate_yield_change_attribution_pca_tool",
    "calculate_half_life_tool",
    "calculate_beta_adjusted_spread_tool",
]


@pytest.fixture(autouse=True)
def stub_known_rates_primitives(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace ``_compute_workspace_tools`` so the events-layer tests
    don't need to import the rates analytics stack (pandas / numpy /
    TimescaleDB drivers).

    Patching at the events.py boundary keeps the stub minimal: we
    inject a known-good primitive list, the manifest-only set + MCP
    aliases come from events.py itself, and the lazy-cache reset
    fixture ensures each test sees a fresh computation.
    """
    expected: set[str] = (
        set(STUB_RATES_PRIMITIVES)
        | set(ev._MANIFEST_ONLY_BUILD_TOOLS)
        | set(ev._MCP_ALIAS_TO_CANONICAL.keys())
    )
    monkeypatch.setattr(
        ev, "_compute_workspace_tools", lambda: frozenset(expected)
    )
    ev._reset_workspace_tools_for_test()
    yield
    ev._reset_workspace_tools_for_test()


# ---------------------------------------------------------------------------
# §A — Coverage of the backend primitive registry
# ---------------------------------------------------------------------------


def test_every_backend_primitive_is_a_workspace_tool() -> None:
    """The full ``_PRIMITIVE_SPECS`` keyset must show up — this is the
    invariant that PR-B-α was created to guarantee."""
    snap = set(ev.workspace_tools_snapshot())
    missing = set(STUB_RATES_PRIMITIVES) - snap
    assert missing == set(), (
        f"Backend-registered primitives missing from the workspace gate: "
        f"{sorted(missing)}.  Pre-PR-B-α some of these were silently "
        f"excluded; adding/removing entries from "
        f"``rates_agent.workflows._PRIMITIVE_SPECS`` must auto-update the "
        f"gate."
    )


def test_manifest_only_typed_view_tools_are_included() -> None:
    """Tools that ship in the rates manifest but have no
    ``PrimitiveSpec`` (no backend run endpoint) still need to surface
    a workspace_context so Build can render their typed view / honest
    unsupported-known card."""
    snap = set(ev.workspace_tools_snapshot())
    for t in (
        "calculate_butterfly_tool",
        "scan_extremes_tool",
        "classify_curve_move_tool",
        "scan_ois_extremes_tool",
    ):
        assert t in snap, (
            f"Manifest-only typed-view tool {t!r} missing from the "
            f"workspace gate — Build's typed view / unsupported-known "
            f"card cannot render off Ask traces."
        )


# ---------------------------------------------------------------------------
# §B — OIS rate-level alias
# ---------------------------------------------------------------------------


def test_ois_rate_level_both_names_resolve() -> None:
    """The MCP-exposed name and the registry name BOTH resolve to a
    workspace-tool match.  Pre-PR-B-α only the registry name passed;
    Ask traces recorded under the MCP name fell through the gate."""
    assert ev.is_workspace_tool("calculate_ois_rate_level_tool"), (
        "MCP-exposed name must pass — Ask traces record the function "
        "name decorated by @mcp.tool() which is "
        "``calculate_ois_rate_level_tool`` even though the registry "
        "key is ``get_ois_rate_level_tool``."
    )
    assert ev.is_workspace_tool("get_ois_rate_level_tool"), (
        "Registry/canonical name must pass — workflow-driven calls "
        "use this name."
    )


def test_ois_rate_level_label_template_supports_both_names() -> None:
    """The chat-trace label template entry exists under BOTH names so
    the friendly label renders regardless of which entry point invoked
    the tool."""
    assert ev.make_tool_label(
        "calculate_ois_rate_level_tool",
        {"curve_family": "USD_SOFR_OIS", "tenor": "10Y"},
    ).startswith("Fetching OIS")
    assert ev.make_tool_label(
        "get_ois_rate_level_tool",
        {"curve_family": "USD_SOFR_OIS", "tenor": "10Y"},
    ).startswith("Fetching OIS")


# ---------------------------------------------------------------------------
# §C — extract_workspace_context wire shape
# ---------------------------------------------------------------------------


def test_extract_preserves_invocation_order() -> None:
    """Order of the input ``tool_calls`` list is preserved in the
    output.  Build renders cards in the same order the LLM invoked
    the tools; reordering would surprise users reading a multi-tool
    answer."""
    calls = [
        {"tool": "calculate_curve_spread_tool", "params": {"x": 1}, "domain": "rates"},
        {"tool": "get_yield_levels_tool", "params": {"x": 2}, "domain": "rates"},
        {"tool": "calculate_curve_spread_tool", "params": {"x": 3}, "domain": "rates"},
    ]
    ctx = ev.extract_workspace_context(calls)
    assert ctx is not None
    assert [t["params"]["x"] for t in ctx["tools"]] == [1, 2, 3]
    assert ctx["tool_count"] == 3


def test_extract_returns_none_for_empty_or_all_unsupported() -> None:
    assert ev.extract_workspace_context([]) is None
    assert ev.extract_workspace_context([
        {"tool": "definitely_not_a_tool", "params": {}},
    ]) is None


def test_extract_drops_non_workspace_tools_but_keeps_supported_ones() -> None:
    """Tools not in the workspace set are skipped, but the order of
    surviving entries is preserved."""
    calls = [
        {"tool": "definitely_not_a_tool", "params": {}, "domain": "rates"},
        {"tool": "get_yield_levels_tool", "params": {"a": 1}, "domain": "rates"},
        {"tool": "calculate_curve_spread_tool", "params": {"b": 2}, "domain": "rates"},
    ]
    ctx = ev.extract_workspace_context(calls)
    assert ctx is not None
    assert ctx["tool_count"] == 2
    assert ctx["tools"][0]["tool"] == "get_yield_levels_tool"
    assert ctx["tools"][1]["tool"] == "calculate_curve_spread_tool"


def test_extract_preserves_params_verbatim() -> None:
    """Params are passed through unchanged — including nested dicts /
    lists that the supervisor or workflow might emit.  Build's
    contextDecoder is responsible for the URL-encoding step; events.py
    must not lose information."""
    nested = {
        "curve_family": "UST",
        "regressor_specs": [
            {"curve_family": "UST", "tenor": "2Y"},
            {"curve_family": "DE_BUND", "tenor": "2Y"},
        ],
        "lookback_days": 504,
    }
    calls = [{"tool": "calculate_rolling_regression_tool", "params": nested}]
    ctx = ev.extract_workspace_context(calls)
    assert ctx is not None
    assert ctx["tools"][0]["params"] == nested


# ---------------------------------------------------------------------------
# §D — Optional status / error / duration_ms metadata propagation
# ---------------------------------------------------------------------------


def test_extract_propagates_error_as_status_error() -> None:
    """When the source trace has a non-null ``error`` string, the
    workspace entry gets ``status: 'error'`` + ``error: '<msg>'`` so
    Build can render an honest 'this tool failed' tile next to the
    working cards instead of dropping the entry."""
    calls = [
        {
            "tool": "calculate_curve_spread_tool",
            "params": {"curve_family": "UST"},
            "domain": "rates",
            "error": "TimescaleDB connection refused",
            "duration_ms": 250,
        },
    ]
    ctx = ev.extract_workspace_context(calls)
    assert ctx is not None
    item = ctx["tools"][0]
    assert item["status"] == "error"
    assert item["error"] == "TimescaleDB connection refused"
    assert item["duration_ms"] == 250


def test_extract_omits_optional_fields_when_absent() -> None:
    """Backward compat: pre-PR-B-α entries had only ``tool`` /
    ``params`` / ``domain``.  When the source trace has no ``error``
    / ``status`` / ``duration_ms``, the output entry must NOT carry
    null fields — old consumers shouldn't see new keys with null
    values."""
    calls = [
        {"tool": "calculate_curve_spread_tool", "params": {"x": 1}, "domain": "rates"},
    ]
    ctx = ev.extract_workspace_context(calls)
    assert ctx is not None
    item = ctx["tools"][0]
    # Must have the legacy three keys verbatim.
    assert set(item.keys()) == {"tool", "params", "domain"}


def test_extract_propagates_explicit_ok_status_when_present() -> None:
    """An explicit ``status: 'ok'`` from upstream (e.g. when the
    trace is re-extracted via ``_merge_workspace_contexts``) is
    passed through.  Keeps the merge path round-trip-safe."""
    calls = [
        {
            "tool": "get_yield_levels_tool",
            "params": {"curve_family": "UST"},
            "status": "ok",
        }
    ]
    ctx = ev.extract_workspace_context(calls)
    assert ctx is not None
    assert ctx["tools"][0].get("status") == "ok"


def test_merge_round_trip_preserves_error_metadata() -> None:
    """The supervisor-side ``_merge_workspace_contexts`` re-runs each
    child's already-extracted context through
    ``extract_workspace_context`` to consolidate.  Error metadata
    must survive that round-trip."""
    # First-pass: one child errored.
    first = ev.extract_workspace_context(
        [
            {
                "tool": "calculate_curve_spread_tool",
                "params": {"curve_family": "UST"},
                "domain": "rates",
                "error": "boom",
            }
        ]
    )
    assert first is not None
    assert first["tools"][0]["status"] == "error"

    # Second-pass merge: feed the entries back in.
    merged = ev.extract_workspace_context(first["tools"])
    assert merged is not None
    assert merged["tools"][0]["status"] == "error"
    assert merged["tools"][0]["error"] == "boom"


# ---------------------------------------------------------------------------
# §E — workspace_tools_snapshot stability for the frontend contract
# ---------------------------------------------------------------------------


def test_workspace_tools_snapshot_is_sorted() -> None:
    """The frontend's matching ``buildHandoffContract.test.ts`` snaps a
    sorted set.  Backend keeps the snapshot sorted so the diff is
    minimal whenever the contract updates."""
    snap = ev.workspace_tools_snapshot()
    assert snap == sorted(snap)


def test_workspace_tools_snapshot_covers_known_pain_points() -> None:
    """Spot-check the specific tools whose absence pre-PR-B-α caused
    the 'Open in Build' button to no-op on prompts 3/4/5 in the audit."""
    snap = set(ev.workspace_tools_snapshot())
    must_include = {
        "get_yield_levels_tool",
        "calculate_swap_spread_tool",
        "calculate_breakeven_inflation_tool",
        "calculate_zscore_custom_tool",
        "build_sovereign_yield_panel_tool",
        "compute_financing_rate_tool",
        "get_ois_rate_level_tool",
        "calculate_ois_rate_level_tool",
    }
    missing = must_include - snap
    assert missing == set(), (
        f"PR-B-α audit-flagged tools missing from the gate: "
        f"{sorted(missing)}."
    )

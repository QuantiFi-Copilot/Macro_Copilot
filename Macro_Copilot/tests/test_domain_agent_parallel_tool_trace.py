"""tests/test_domain_agent_parallel_tool_trace.py — PR-D coverage lock.

Locks the bug Codex's audit + the user's screenshots flagged:
``DomainAgentSession.run()`` correlates ``on_tool_start`` ↔
``on_tool_end`` events by per-invocation ``run_id`` (LangGraph
``astream_events`` v2's canonical correlator), not by shared mutable
state.

Pre-PR-D the orchestrator used three module-local variables —
``current_tool_name`` / ``current_tool_params`` / ``current_tool_start``
— to remember the in-flight tool's identity.  That assumed strict
serial pairing (``start-A → end-A → start-B → end-B``), which broke
the moment the LLM emitted more than one ``tool_use`` block in a
single response and LangGraph's ``ToolNode`` dispatched them
concurrently.  Event order became ``start-A, start-B, start-C,
end-A, end-B, end-C``; every later ``start`` clobbered the in-flight
state before the matching ``end`` could read it.  Result: ONE tool
trace entry got the LAST-started tool's params, the rest got
``{}``.  PR-B-β's "missing params" tile then fired on N-1 cards.

This file exercises the run-id correlation directly by driving a
real ``DomainAgentSession`` against a STUB ``_graph.astream_events``
async generator that yields a hand-built interleaved event sequence.
The downstream ``_extract_facts`` / ``_classify_status`` helpers
run on the synthetic data unchanged — they handle empty / minimal
inputs gracefully.
"""

from __future__ import annotations

import json
from typing import Any, Iterable
from unittest.mock import MagicMock

import pytest

from orchestrator.contracts import Domain
from orchestrator.domain_agent import DomainAgentSession


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


def _make_session_for_test() -> DomainAgentSession:
    """Build a minimal session shell suitable for driving ``run()``
    against a stubbed event stream.  Bypasses MCP / ChatAnthropic
    setup entirely — ``run()`` only touches ``self._graph``,
    ``self._is_open``, and ``self.domain``, so the heavy lifecycle
    methods (``open()`` / ``close()``) aren't needed for this test."""
    session = DomainAgentSession(
        domain=Domain.SOVEREIGN_BONDS,
        system_prompt="(stubbed)",
        mcp_servers={},
        model_name="claude-test-model",
    )
    session._is_open = True
    session._graph = MagicMock()
    return session


async def _yield_events(events: Iterable[dict]) -> Any:
    """Adapt a sync iterable of event dicts into the async generator
    shape ``astream_events`` returns."""
    for ev in events:
        yield ev


def _install_event_stream(session: DomainAgentSession, events: list[dict]):
    """Wire the session's ``_graph.astream_events`` to yield the
    given event list.  Each call returns a fresh async iterator so
    re-runs replay from the start."""
    session._graph.astream_events = lambda *_args, **_kwargs: _yield_events(events)


def _tool_start_event(*, name: str, run_id: str, params: dict) -> dict:
    return {
        "event": "on_tool_start",
        "name": name,
        "run_id": run_id,
        "data": {"input": params},
    }


def _tool_end_event(
    *, name: str, run_id: str, output: Any = None
) -> dict:
    if output is None:
        # MCP tools return a JSON string; mimic that contract.
        output = json.dumps({"ok": True, "tool": name})
    return {
        "event": "on_tool_end",
        "name": name,
        "run_id": run_id,
        "data": {"output": output},
    }


# ---------------------------------------------------------------------------
# §A — The bug PR-D fixes: parallel tool calls with interleaved events.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_tool_calls_preserve_per_invocation_params():
    """Three ``calculate_curve_spread_tool`` invocations with
    distinct (curve_family, short_tenor, long_tenor) params.  The
    event stream interleaves all three starts BEFORE any ends —
    exactly the LangGraph + Claude parallel-tool pattern from the
    audit screenshots.  Pre-PR-D this produced one trace entry with
    the last-started tool's params + two with ``{}``.  Post-PR-D
    every trace entry has the params from ITS OWN start event."""
    session = _make_session_for_test()

    events = [
        _tool_start_event(
            name="calculate_curve_spread_tool",
            run_id="rid-ust",
            params={
                "curve_family": "UST",
                "short_tenor": "2Y",
                "long_tenor": "10Y",
            },
        ),
        _tool_start_event(
            name="calculate_curve_spread_tool",
            run_id="rid-bund",
            params={
                "curve_family": "DE_BUND",
                "short_tenor": "2Y",
                "long_tenor": "10Y",
            },
        ),
        _tool_start_event(
            name="calculate_curve_spread_tool",
            run_id="rid-gilt",
            params={
                "curve_family": "UK_GILT",
                "short_tenor": "2Y",
                "long_tenor": "10Y",
            },
        ),
        # Ends arrive in a DIFFERENT order than the starts — exactly
        # what triggered the bug in production.
        _tool_end_event(name="calculate_curve_spread_tool", run_id="rid-ust"),
        _tool_end_event(name="calculate_curve_spread_tool", run_id="rid-gilt"),
        _tool_end_event(name="calculate_curve_spread_tool", run_id="rid-bund"),
    ]
    _install_event_stream(session, events)

    response = await session.run(user_message="test")

    assert len(response.tool_trace) == 3, (
        f"Expected 3 traces; got {len(response.tool_trace)}"
    )

    # Find each by run_id-induced ordering (ends arrived ust → gilt
    # → bund, so traces are appended in that order).
    by_curve = {
        t.params.get("curve_family"): t for t in response.tool_trace
    }
    assert set(by_curve.keys()) == {"UST", "DE_BUND", "UK_GILT"}, (
        f"All three curve_family params survived; got keys: "
        f"{sorted(k for k in by_curve.keys() if k is not None)}"
    )

    # Each trace's params dict must be the FULL dict from its own
    # start event (not partial, not empty).
    for expected in ("UST", "DE_BUND", "UK_GILT"):
        t = by_curve[expected]
        assert t.params == {
            "curve_family": expected,
            "short_tenor": "2Y",
            "long_tenor": "10Y",
        }, f"trace for {expected} has wrong params: {t.params!r}"
        assert t.tool == "calculate_curve_spread_tool"
        assert t.error is None


# ---------------------------------------------------------------------------
# §B — Mixed-tool parallel: 3 yield + 2 cross-market — the screenshot
# 2 case (prompt: "UST, Bund, Gilt 10Y yields + UST-Bund, UST-Gilt").
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mixed_parallel_yield_and_cross_market_params_correct():
    session = _make_session_for_test()
    events = [
        _tool_start_event(
            name="get_yield_levels_tool",
            run_id="y-ust",
            params={"curve_family": "UST", "tenor": "10Y"},
        ),
        _tool_start_event(
            name="get_yield_levels_tool",
            run_id="y-bund",
            params={"curve_family": "DE_BUND", "tenor": "10Y"},
        ),
        _tool_start_event(
            name="get_yield_levels_tool",
            run_id="y-gilt",
            params={"curve_family": "UK_GILT", "tenor": "10Y"},
        ),
        _tool_start_event(
            name="calculate_cross_market_spread_tool",
            run_id="cm-ust-bund",
            params={
                "curve_family_1": "UST",
                "curve_family_2": "DE_BUND",
                "tenor": "10Y",
            },
        ),
        _tool_start_event(
            name="calculate_cross_market_spread_tool",
            run_id="cm-ust-gilt",
            params={
                "curve_family_1": "UST",
                "curve_family_2": "UK_GILT",
                "tenor": "10Y",
            },
        ),
        # Reverse-order ends so every trace must be correlated by id.
        _tool_end_event(
            name="calculate_cross_market_spread_tool", run_id="cm-ust-gilt"
        ),
        _tool_end_event(
            name="calculate_cross_market_spread_tool", run_id="cm-ust-bund"
        ),
        _tool_end_event(name="get_yield_levels_tool", run_id="y-gilt"),
        _tool_end_event(name="get_yield_levels_tool", run_id="y-bund"),
        _tool_end_event(name="get_yield_levels_tool", run_id="y-ust"),
    ]
    _install_event_stream(session, events)

    response = await session.run(user_message="test")
    assert len(response.tool_trace) == 5

    yield_traces = [
        t for t in response.tool_trace if t.tool == "get_yield_levels_tool"
    ]
    cm_traces = [
        t
        for t in response.tool_trace
        if t.tool == "calculate_cross_market_spread_tool"
    ]
    assert len(yield_traces) == 3
    assert len(cm_traces) == 2

    yield_curves = sorted(t.params.get("curve_family") for t in yield_traces)
    assert yield_curves == ["DE_BUND", "UK_GILT", "UST"], (
        f"All three yield curves present in trace: {yield_curves}"
    )

    cm_pairs = sorted(
        (t.params.get("curve_family_1"), t.params.get("curve_family_2"))
        for t in cm_traces
    )
    assert cm_pairs == [("UST", "DE_BUND"), ("UST", "UK_GILT")], (
        f"Both cross-market pairs present: {cm_pairs}"
    )


# ---------------------------------------------------------------------------
# §C — Serial single-tool case: backward-compatibility regression lock.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serial_single_tool_still_works():
    """The 99% case — one tool, perfect serial start/end pair —
    must behave identically to pre-PR-D.  Locks against any
    over-rotation in the fix."""
    session = _make_session_for_test()
    events = [
        _tool_start_event(
            name="get_yield_levels_tool",
            run_id="solo",
            params={"curve_family": "UST", "tenor": "10Y"},
        ),
        _tool_end_event(
            name="get_yield_levels_tool",
            run_id="solo",
            output=json.dumps({"yield_pct": 4.28}),
        ),
    ]
    _install_event_stream(session, events)
    response = await session.run(user_message="test")
    assert len(response.tool_trace) == 1
    t = response.tool_trace[0]
    assert t.tool == "get_yield_levels_tool"
    assert t.params == {"curve_family": "UST", "tenor": "10Y"}
    assert t.error is None
    # duration_ms should be a non-negative int (computed from
    # monotonic between start and end).
    assert t.duration_ms is not None
    assert isinstance(t.duration_ms, int)
    assert t.duration_ms >= 0


# ---------------------------------------------------------------------------
# §D — Defensive paths: orphan events, missing run_id.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_tool_end_does_not_crash():
    """An ``on_tool_end`` without a matching prior ``on_tool_start``
    (e.g. dropped event, schema mismatch) records a trace entry with
    ``params={}`` and falls back to the event's ``name``.  Same
    fail-soft contract as pre-PR-D (``current_tool_name or name`` →
    falls back to ``name``)."""
    session = _make_session_for_test()
    events = [
        # Orphan end — no matching start in the stream.
        _tool_end_event(name="calculate_curve_spread_tool", run_id="orphan"),
    ]
    _install_event_stream(session, events)
    response = await session.run(user_message="test")
    assert len(response.tool_trace) == 1
    t = response.tool_trace[0]
    assert t.tool == "calculate_curve_spread_tool"
    assert t.params == {}
    # No matching start → no start_time → duration_ms is None.
    assert t.duration_ms is None


@pytest.mark.asyncio
async def test_missing_run_id_falls_back_to_per_name_correlation():
    """Defensive: some LangChain wrappers may emit events without
    ``run_id``.  We fall back to a per-name key so the serial case
    still works.  The parallel case can't be salvaged without
    run_id (the events are genuinely indistinguishable), but the
    common single-tool case shouldn't regress."""
    session = _make_session_for_test()
    events = [
        {
            "event": "on_tool_start",
            "name": "get_yield_levels_tool",
            # run_id intentionally absent / None
            "data": {"input": {"curve_family": "UST", "tenor": "10Y"}},
        },
        {
            "event": "on_tool_end",
            "name": "get_yield_levels_tool",
            "data": {"output": json.dumps({"yield_pct": 4.0})},
        },
    ]
    _install_event_stream(session, events)
    response = await session.run(user_message="test")
    assert len(response.tool_trace) == 1
    t = response.tool_trace[0]
    assert t.params == {"curve_family": "UST", "tenor": "10Y"}


# ---------------------------------------------------------------------------
# §E — Tool errors propagate per-invocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parallel_calls_with_one_erroring_propagate_error_to_right_trace():
    """When one of the parallel calls errors, the error string ends
    up on THAT call's trace entry — not on a sibling's.  Pre-PR-D
    the shared state would have attached the error to whichever
    trace happened to consume it first."""
    session = _make_session_for_test()
    events = [
        _tool_start_event(
            name="get_yield_levels_tool",
            run_id="ok",
            params={"curve_family": "UST", "tenor": "10Y"},
        ),
        _tool_start_event(
            name="get_yield_levels_tool",
            run_id="boom",
            params={"curve_family": "BAD_CURVE", "tenor": "10Y"},
        ),
        _tool_end_event(
            name="get_yield_levels_tool",
            run_id="boom",
            output=json.dumps({"error": "Unknown curve family BAD_CURVE"}),
        ),
        _tool_end_event(
            name="get_yield_levels_tool",
            run_id="ok",
            output=json.dumps({"yield_pct": 4.28}),
        ),
    ]
    _install_event_stream(session, events)
    response = await session.run(user_message="test")
    assert len(response.tool_trace) == 2

    # The "boom" trace (with BAD_CURVE params) carries the error;
    # the "ok" trace does not.
    bad = next(
        t for t in response.tool_trace if t.params.get("curve_family") == "BAD_CURVE"
    )
    good = next(
        t for t in response.tool_trace if t.params.get("curve_family") == "UST"
    )
    assert bad.error == "Unknown curve family BAD_CURVE"
    assert good.error is None

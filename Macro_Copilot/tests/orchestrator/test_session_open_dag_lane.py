"""tests/orchestrator/test_session_open_dag_lane.py — PR-10A F1.

Tests for ``CopilotSession.run_open_dag`` — the thin pre-router the
plan §PR-10 required.  PR-10 shipped OpenDagPipeline but did not
wire it into the session; PR-10A's F1 corrective adds the wiring.

The wiring is INVASIVE-PROOF: existing template-lane tests do not
exercise the new method, and the new method does not exercise any
template-lane internals.  Verified by:
  - the existing test suite still passing (zero regressions
    confirmed in PR-10A's full regression);
  - this file's `test_run_open_dag_does_not_call_template_path`
    test.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from pydantic import BaseModel

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    ExecutionLane,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag import (
    BoundLeaf,
    ComposerRefusal,
    Frequency,
    GOLDEN_TRANSFORM_ROLLING_ZSCORE,
    GateVerdict,
    OpenDagPipeline,
    ShapeSpec,
)


# ============================================================================
# 1. METHOD EXISTS + IS CALLABLE
# ============================================================================


def test_session_exposes_run_open_dag_method():
    """PR-10A Codex F1: CopilotSession MUST expose a thin pre-router
    method that the open-DAG lane is reachable through."""
    from orchestrator.session import CopilotSession
    assert hasattr(CopilotSession, "run_open_dag"), (
        "PR-10A F1: CopilotSession.run_open_dag method must exist"
    )
    import inspect
    assert inspect.iscoroutinefunction(CopilotSession.run_open_dag), (
        "run_open_dag must be async"
    )


def test_session_exposes_close_path_for_open_dag_components():
    """The wiring's lifecycle helpers exist (called by close())."""
    from orchestrator.session import CopilotSession
    assert hasattr(CopilotSession, "_ensure_open_dag_components_open")
    assert hasattr(CopilotSession, "_close_open_dag_components_quiet")


# ============================================================================
# 2. EAGER RAISE WHEN SESSION NOT OPEN
# ============================================================================


@pytest.mark.asyncio
async def test_run_open_dag_raises_when_not_open():
    """Calling run_open_dag without first opening the session raises
    RuntimeError — same contract as the existing template lane."""
    from orchestrator.session import CopilotSession
    session = CopilotSession(thread_id="test")
    # Session is NOT open.
    with pytest.raises(RuntimeError, match="not open"):
        await session.run_open_dag("any prompt")


# ============================================================================
# 3. PIPELINE WIRING SHAPE
# ============================================================================


@pytest.mark.asyncio
async def test_run_open_dag_constructs_pipeline_with_session_state():
    """When called, run_open_dag constructs an OpenDagPipeline using
    the session's already-cached supervisor + per-domain children
    + a lazily-constructed Composer / Gate / AnswerRenderer.

    This test patches the pipeline's behaviour (replacing
    OpenDagPipeline with a recording stub) and asserts the wiring
    passes the right pieces.
    """
    from orchestrator.session import CopilotSession
    from orchestrator import open_dag as od_pkg
    from orchestrator import session as session_mod

    # Build a session with sovereign_bonds + ois.
    session = CopilotSession(thread_id="test")

    # Set the session into a "fake-open" state manually so we don't
    # spawn real MCP subprocesses.
    session._is_open = True

    class _FakeSupervisor:
        async def route(self, prompt):
            return RouteDecision(
                action=RouteAction.SINGLE_DOMAIN,
                domains=[Domain.SOVEREIGN_BONDS],
                rationale="t",
                intent_tag=IntentTag.LOOKUP,
                decomposition=[EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                )],
            )

    session._supervisor = _FakeSupervisor()

    # Inject fake domain children that record fill_leaf calls.
    class _FakeChild:
        _is_open = True
        calls: list = []

        async def fill_leaf(self, *, leaf_id, request, timeout_s):
            self.calls.append(leaf_id)
            return BoundLeaf(
                leaf_id=leaf_id,
                domain=request.domain_hint,
                mcp_tool_name="fake_tool",
                resolver_tool_key="fake_tool",
                params={},
                output_field="time_series",
                declared_output_artifact_type=request.required_artifact_type,
                declared_units=None,
                declared_frequency=Frequency.DAILY,
                declared_semantic_role=request.semantic_role,
                declared_output_meaning=request.requested_output_meaning,
                fit_confidence=0.9,
            )

        async def close(self):
            pass

    # Replace each Domain child with the fake.
    session._children = {
        Domain.SOVEREIGN_BONDS: _FakeChild(),
        Domain.OIS: _FakeChild(),
    }
    import asyncio
    session._child_open_locks = {
        Domain.SOVEREIGN_BONDS: asyncio.Lock(),
        Domain.OIS: asyncio.Lock(),
    }

    # Pre-seed the open-DAG components with mocks so the lazy-open
    # path is a no-op.
    class _MockComposer:
        calls = 0

        async def compose(self, **kw):
            type(self).calls += 1
            return GOLDEN_TRANSFORM_ROLLING_ZSCORE

        def close(self): pass

    class _MockGate:
        async def check(self, **kw):
            return GateVerdict(status="PASS", reason="ok")

        def close(self): pass

    class _MockRenderer:
        async def render(self, **kw):
            return "RENDERED_BY_FAKE"

        def close(self): pass

    session._open_dag_composer = _MockComposer()
    session._open_dag_gate = _MockGate()
    session._open_dag_renderer = _MockRenderer()

    outcome = await session.run_open_dag("test prompt")

    # The composer was consulted.  Phase B (8933d9c) wired a
    # PIPELINE-level bounded self-correction loop: an ASSEMBLY_REFUSE
    # (here: the mock BoundLeaf's tool_name="fake_tool" can't bind via
    # the production resolver) triggers ONE reason-seeded re-compose, so
    # the composer fires twice for this persistently-unresolvable golden.
    # Assert at-least-once (the composer WAS consulted — the wiring
    # works) rather than exactly-once, mirroring the child assertion
    # below, so the test stays valid under the self-correction budget.
    assert _MockComposer.calls >= 1
    # The right child's fill_leaf was called (sovereign_bonds — the
    # GOLDEN_TRANSFORM_ROLLING_ZSCORE leaf is hardcoded sovereign_bonds).
    sov_child = session._children[Domain.SOVEREIGN_BONDS]
    # PR-10B Codex F4: with the bounded repair loop wired in, the
    # leaf rebinder may fire fill_leaf again during repair when the
    # assembler refuses (e.g. because the mock BoundLeaf's tool name
    # isn't in the real resolver).  Assert at-least-once rather than
    # exactly-once so the test remains valid post-repair-loop wiring.
    assert sov_child.calls and all(c == "leaf_input" for c in sov_child.calls), (
        f"sovereign_bonds child should have received leaf_input; "
        f"got {sov_child.calls}"
    )
    # The outcome is the typed PipelineOutcome.  The mocked
    # selectors emit a BoundLeaf with tool_name="fake_tool" which is
    # NOT in the real rates_primitive_resolver — the assembler
    # therefore refuses post-substitution.  That's correct behaviour:
    # the wiring succeeded (composer + child + assembler all fired),
    # just the FAKE primitive can't bind via the production
    # resolver.  Accept any structured outcome.
    assert isinstance(outcome.status, str)
    assert outcome.status in (
        "PASS", "GATE_REFUSE", "GATE_CLARIFY",
        "ASSEMBLY_REFUSE", "COMPOSER_REFUSE",
        "PIPELINE_ERROR",
    ), (
        f"unexpected outcome status: {outcome.status}"
    )


@pytest.mark.asyncio
async def test_run_open_dag_lazy_opens_components_only_once():
    """_ensure_open_dag_components_open is idempotent — repeated
    calls don't re-open."""
    from orchestrator.session import CopilotSession

    session = CopilotSession(thread_id="test")
    session._is_open = True

    # Pre-load with sentinel objects so the call is a no-op.
    class _Sentinel: pass
    session._open_dag_composer = _Sentinel()
    session._open_dag_gate = _Sentinel()
    session._open_dag_renderer = _Sentinel()

    before_composer = session._open_dag_composer
    await session._ensure_open_dag_components_open()
    after_composer = session._open_dag_composer
    # Same object — no re-construction.
    assert before_composer is after_composer


@pytest.mark.asyncio
async def test_close_drops_open_dag_component_state():
    """CopilotSession.close()'s F1 wiring routes through
    _close_open_dag_components_quiet, dropping the cached state."""
    from orchestrator.session import CopilotSession

    session = CopilotSession(thread_id="test")

    class _ClosableMock:
        closed = 0

        def close(self):
            type(self).closed += 1

    session._open_dag_composer = _ClosableMock()
    session._open_dag_gate = _ClosableMock()
    session._open_dag_renderer = _ClosableMock()

    await session._close_open_dag_components_quiet()

    assert session._open_dag_composer is None
    assert session._open_dag_gate is None
    assert session._open_dag_renderer is None
    # The .close() was invoked once per component.
    assert _ClosableMock.closed >= 3


# ============================================================================
# 4. TEMPLATE-LANE INDEPENDENCE
# ============================================================================


def test_run_open_dag_does_not_call_template_path():
    """Static check: ``run_open_dag`` source does NOT reference any
    template-lane internals (workflow templates, the supervisor's
    synthesis path, the children's ``run`` method).

    This is the structural anchor for acceptance #4: "Pre-router
    cleanly separates lanes; existing template lane unaffected."
    """
    import inspect
    from orchestrator.session import CopilotSession

    src = inspect.getsource(CopilotSession.run_open_dag)
    # Tokens that would indicate template-lane coupling.
    forbidden = [
        "synthesize_stream",  # supervisor's template-lane synthesis
        "child.run(",          # children's ReAct run
        "workflow_template",
        "run_workflow",
    ]
    for needle in forbidden:
        assert needle not in src, (
            f"PR-10A F1: run_open_dag references template-lane token "
            f"{needle!r} — that would break lane separation"
        )


# ============================================================================
# 5. OPTION A — execution_lane DISPATCH GATE (route-once-then-dispatch)
# ============================================================================
#
# The session routes ONCE per turn, then dispatches by
# ``RouteDecision.execution_lane``: ``open_dag`` (or null/unset) → the
# open-DAG composer lane (run_open_dag, fed the SAME decision); an explicit
# ``direct_fetch`` → the legacy supervisor/domain-agent path (which emits
# ``workspace_context`` for the primitive UI).  These drive ``_run_turn``
# on a bare (engine-less) session — the persistence preamble + workflow
# pre-gate are all no-ops — and spy on ``run_open_dag``.


def _bare_session_with_decision(decision):
    """A fake-open, engine-less session whose supervisor returns
    ``decision`` and whose ``run_open_dag`` is a recording spy."""
    from orchestrator.session import CopilotSession

    session = CopilotSession(thread_id="lane-test")
    session._is_open = True
    session._workflow_router = None  # skip the template pre-gate
    session._children = {}           # legacy path → missing-domains short-circuit

    class _FakeSup:
        async def route(self, prompt):
            return decision

    session._supervisor = _FakeSup()

    calls: list = []

    async def _spy_run_open_dag(prompt, *, route_decision=None, **kw):
        calls.append({"prompt": prompt, "route_decision": route_decision})
        return SimpleNamespace(status="GATE_REFUSE", markdown="refused")

    session.run_open_dag = _spy_run_open_dag  # shadow the bound method
    return session, calls


def _df_decision(lane):
    return RouteDecision(
        action=RouteAction.SINGLE_DOMAIN,
        domains=[Domain.SOVEREIGN_BONDS],
        rationale="r",
        intent_tag=IntentTag.LOOKUP,
        decomposition=[EconomicQuantity(
            name="x", nl_description="x", domain_hint=Domain.SOVEREIGN_BONDS,
        )],
        expected_answer_shape=["scalar"],
        execution_lane=lane,
    )


@pytest.mark.asyncio
async def test_lane_gate_open_dag_calls_run_open_dag_with_decision():
    """execution_lane=open_dag → the turn dispatches into run_open_dag,
    and the SAME precomputed RouteDecision is threaded in (route-once)."""
    decision = _df_decision(ExecutionLane.OPEN_DAG)
    session, calls = _bare_session_with_decision(decision)

    events: list = []

    async def emit(ev):
        events.append(ev)

    await session._run_turn("average US 2s10s over 5y", "T", emit)

    assert len(calls) == 1, "open_dag lane must call run_open_dag exactly once"
    assert calls[0]["route_decision"] is decision, (
        "route-once: the precomputed decision must be threaded into run_open_dag"
    )


@pytest.mark.asyncio
async def test_lane_gate_direct_fetch_skips_open_dag():
    """execution_lane=direct_fetch → run_open_dag is NOT called; the turn
    takes the legacy path and emits a route_decision carrying the lane."""
    decision = _df_decision(ExecutionLane.DIRECT_FETCH)
    session, calls = _bare_session_with_decision(decision)

    events: list = []

    async def emit(ev):
        events.append(ev)

    await session._run_turn("current US 10Y yield", "T", emit)

    assert calls == [], "direct_fetch must NOT enter the open-DAG lane"
    # The legacy path emits a route_decision event surfacing the lane.
    rd = [e for e in events if e.type == "route_decision"]
    assert rd, "legacy/direct_fetch path must emit a route_decision event"
    assert rd[0].data.get("execution_lane") == "direct_fetch"


@pytest.mark.asyncio
async def test_lane_gate_null_lane_defaults_to_open_dag():
    """A null/unset execution_lane is the safe back-compat catch-all →
    open-DAG lane (preserves pre-Option-A behaviour)."""
    decision = _df_decision(None)  # router omitted the lane
    session, calls = _bare_session_with_decision(decision)

    async def emit(ev):
        pass

    await session._run_turn("anything", "T", emit)

    assert len(calls) == 1, "null lane must default to the open-DAG lane"
    assert calls[0]["route_decision"] is decision

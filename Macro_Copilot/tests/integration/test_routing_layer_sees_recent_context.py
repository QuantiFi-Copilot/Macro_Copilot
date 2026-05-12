"""tests/integration/test_routing_layer_sees_recent_context.py

PR 14 diagnostic.  PR 13 added ``load_recent_turns`` + the routing
prefix + wired ``augmented_message`` into ``_run_turn``.  But the
user's screenshot showed the bug STILL happening after PR 13
landed, which means either:

  (a) the augmentation isn't actually reaching the workflow router
      / supervisor at runtime, OR
  (b) it is reaching them, and the routers are choosing CLARIFY
      anyway (a prompt-discipline problem, not a wiring problem).

This file is the ground-truth diagnostic: spin up a real
``CopilotSession`` against the test DB, stub the supervisor +
workflow router so we CAPTURE the exact ``user_message`` they
receive at turn 2, and assert that message contains turn 1's
content.

If this test passes → the wiring works; the bug is on the LLM
side (route decision quality, not context delivery).

If this test fails → my unit tests didn't cover the actual live
path and I need to find + fix the real wiring bug.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
import uuid
from pathlib import Path
from typing import Any, List

import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ============================================================================
# LangChain stubs — same pattern PR 8/9 use for resolver tests.
# CopilotSession transitively imports langchain_anthropic via
# domain_agent.py; we don't actually invoke any LangChain code in
# this test (stubs replace the LLM-calling pieces), but the import
# has to succeed.
# ============================================================================


def _install_langchain_stubs() -> None:
    # ``orchestrator/config.py`` imports ``dotenv`` unconditionally
    # at module load (existing convention, predates this PR).  The
    # state-layer CI job doesn't install ``python-dotenv`` because
    # nothing in the state-layer test surface needs it AT RUNTIME.
    # We stub the module so the import chain succeeds — load_dotenv
    # is a no-op in tests anyway.
    if "dotenv" not in sys.modules:
        fake_dotenv = types.ModuleType("dotenv")

        def _no_op_load_dotenv(*args, **kwargs):
            return False

        fake_dotenv.load_dotenv = _no_op_load_dotenv
        sys.modules["dotenv"] = fake_dotenv

    if "langchain_anthropic" not in sys.modules:
        fake_la = types.ModuleType("langchain_anthropic")

        class _FakeChat:
            def __init__(self, *args, **kwargs):
                pass

            def with_structured_output(self, *args, **kwargs):
                return self

            def bind_tools(self, *args, **kwargs):
                return self

            async def ainvoke(self, *args, **kwargs):
                return None

        fake_la.ChatAnthropic = _FakeChat
        sys.modules["langchain_anthropic"] = fake_la

    if "langchain_mcp_adapters" not in sys.modules:
        fake_mcp = types.ModuleType("langchain_mcp_adapters")
        fake_mcp_client = types.ModuleType("langchain_mcp_adapters.client")

        class _FakeMCPClient:
            def __init__(self, *args, **kwargs):
                pass

            async def get_tools(self):
                return []

            async def close(self):
                return None

        fake_mcp_client.MultiServerMCPClient = _FakeMCPClient
        fake_mcp.client = fake_mcp_client
        sys.modules["langchain_mcp_adapters"] = fake_mcp
        sys.modules["langchain_mcp_adapters.client"] = fake_mcp_client


_install_langchain_stubs()


# ============================================================================
# DB availability
# ============================================================================


def _build_url() -> str:
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def _db_reachable(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


_DB_URL = _build_url()
_DB_AVAILABLE = _db_reachable(_DB_URL)

pytestmark = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=f"Postgres not reachable at {_DB_URL!r}",
)


# ============================================================================
# Test
# ============================================================================


@pytest.fixture
def engine():
    from sqlalchemy import create_engine

    e = create_engine(_DB_URL)
    yield e
    e.dispose()


@pytest.fixture(autouse=True)
def wipe(engine):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.message_events"))
        conn.execute(text("DELETE FROM copilot_state.turns"))
        conn.execute(text("DELETE FROM copilot_state.sessions"))
    yield


async def _drive_two_turns_capture_router_inputs(engine):
    """Drive two real ``_run_turn`` calls with stubbed routers,
    capturing what user_message each router actually received.

    Returns a list of dicts: ``[{turn: int, supervisor_msg: str,
    workflow_msg: str}, ...]``.

    Stub strategy:
      - Replace ``self._supervisor`` with a fake whose ``route`` /
        ``synthesize_stream`` are coroutines that record the input.
      - Replace ``self._workflow_router`` similarly.
      - Replace ``self._resolver`` with None so the reference
        resolver doesn't try to call an LLM.
      - Replace ``self._children`` so domain dispatch is a no-op
        (the supervisor stub returns CLARIFY which short-circuits).
      - Replace ``self._make_checkpointer`` to avoid LangGraph.
    """
    from orchestrator.contracts import (
        RouteAction,
        RouteDecision,
    )
    from orchestrator.session import CopilotSession
    from orchestrator.state import create_session_if_needed
    from orchestrator.workflow_contracts import (
        WorkflowRouteAction,
        WorkflowRouteDecision,
    )

    sid = uuid.uuid4()
    with engine.begin() as conn:
        create_session_if_needed(sid, conn=conn, user_id="diagnostic")

    captured: List[dict] = []
    turn_num = {"i": 0}

    # Independent capture lists per router — simpler than the
    # interleave-dedup we had before.
    sup_calls: List[str] = []
    wf_calls: List[str] = []

    # ---- Stub supervisor ----
    class _StubSupervisor:
        async def route(self, user_message):
            print(f"[SUPERVISOR.route called] msg[:120]={user_message[:120]!r}")
            sup_calls.append(user_message)
            return RouteDecision(
                action=RouteAction.CLARIFY,
                domains=[],
                rationale="stub",
                clarification_question="diagnostic stub",
                adjustments=[],
            )

        async def synthesize_stream(self, *a, **kw):
            yield ""

    # ---- Stub workflow router ----
    class _StubWorkflowRouter:
        async def route(self, user_message):
            print(f"[WORKFLOW.route called] msg[:120]={user_message[:120]!r}")
            wf_calls.append(user_message)
            return WorkflowRouteDecision(
                action=WorkflowRouteAction.OUT_OF_SCOPE,
                template_id=None,
                slot_values={},
                rationale="stub: always falls through",
                clarification_question=None,
                adjustments=[],
            )

    # ---- Build the CopilotSession with persistent lifecycle ----
    session = CopilotSession(
        thread_id="diagnostic-thread",
        stateless=False,
        checkpointer_pool=None,
        session_id=sid,
        engine=engine,
    )
    # Bypass ``open()`` so we don't spawn supervisor / children /
    # workflow router for real.  Inject stubs directly.
    session._is_open = True
    session._supervisor = _StubSupervisor()
    session._workflow_router = _StubWorkflowRouter()
    session._resolver = None
    session._children = {}
    session._child_open_locks = {}

    # ---- Drive turn 1 ----
    async def _drain(message: str) -> None:
        async for _ in session.stream(message):
            pass
        # Mark turn complete by writing a fake assistant response.
        # The teeing_emit would normally capture token events; the
        # CLARIFY path emits one ``token`` event with the question,
        # which the framework captures.

    await _drain("What is the latest UST 2s10s spread")
    await _drain("what about the Bund one")

    await session.close()
    return {"sup_calls": sup_calls, "wf_calls": wf_calls}


@pytest.mark.asyncio
async def test_routing_layer_receives_recent_context(engine):
    """Drive two turns through the real ``_run_turn`` machinery
    and assert that on turn 2 BOTH the supervisor AND the workflow
    router receive a message containing turn 1's content."""
    result = await _drive_two_turns_capture_router_inputs(engine)
    sup_calls: List[str] = result["sup_calls"]
    wf_calls: List[str] = result["wf_calls"]

    print(f"\n=== sup_calls (n={len(sup_calls)}) ===")
    for i, m in enumerate(sup_calls):
        print(f"  [{i}] {m[:200]!r}")
    print(f"=== wf_calls (n={len(wf_calls)}) ===")
    for i, m in enumerate(wf_calls):
        print(f"  [{i}] {m[:200]!r}")

    # Expect: 2 supervisor calls (one per turn) AND 2 workflow
    # router calls (one per turn).  Any deviation surfaces the
    # wiring bug.
    assert len(wf_calls) == 2, (
        f"Workflow router should be called once per turn (= 2 times "
        f"across 2 turns); got {len(wf_calls)}.  Calls: {wf_calls}"
    )
    assert len(sup_calls) == 2, (
        f"Supervisor should be called once per turn (= 2 times "
        f"across 2 turns); got {len(sup_calls)}.  Calls: {sup_calls}"
    )

    # Turn 2's calls (the second of each).
    wf_t2 = wf_calls[1]
    sup_t2 = sup_calls[1]

    assert "what about the Bund one" in wf_t2, wf_t2
    assert "what about the Bund one" in sup_t2, sup_t2
    assert "RECENT CONVERSATION" in wf_t2, (
        "Workflow router did NOT receive the recent-conversation block on turn 2."
        f"\n\nActual wf_t2:\n{wf_t2!r}"
    )
    assert "What is the latest UST 2s10s spread" in wf_t2, wf_t2
    assert "RECENT CONVERSATION" in sup_t2, sup_t2
    assert "What is the latest UST 2s10s spread" in sup_t2, sup_t2

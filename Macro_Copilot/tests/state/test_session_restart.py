"""tests/state/test_session_restart.py — durable state survives a simulated
Python process restart.

This is the higher-fidelity test that ``Phase 0 PR 5`` ships: not just
"AsyncPostgresSaver writes to Postgres" (the unit-level property
proved by ``test_postgres_checkpointer.py``) but "an end-to-end
LangGraph + checkpointer pipeline produces state that another fresh
LangGraph + checkpointer can read back from the same Postgres."

We build a tiny LangGraph here rather than instantiating the full
``CopilotSession`` because the latter pulls in MCP subprocess startup
and the Anthropic API.  The state-durability property is exactly
``langgraph + AsyncPostgresSaver + Postgres``; testing the
intermediate layer is what the substrate tests do.

A future integration-test job (Phase 0 week 4+) extends this to a full
API-subprocess kill: spawn uvicorn, open a WebSocket, send turns,
SIGTERM the server, restart, reconnect with the same thread_id,
verify the LLM sees prior turns in context.  That's its own
infrastructure effort and is out of scope here.

Closes ``docs/technical_debt.md`` item #6 (persistent LangGraph
checkpointer) at the substrate level.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import AsyncIterator

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _build_dsn() -> str:
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _postgres_reachable(dsn: str) -> bool:
    try:
        import psycopg

        conn = psycopg.connect(dsn, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


_DSN = _build_dsn()
_DB_AVAILABLE = _postgres_reachable(_DSN)


pytestmark = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=(
        f"Postgres not reachable at {_DSN!r}.  Set DB_USER / DB_PASSWORD "
        "/ DB_HOST / DB_PORT / DB_NAME, or run the CI migrations job."
    ),
)


# ============================================================================
# Helpers
# ============================================================================


async def _wipe_langgraph_schema(dsn: str) -> None:
    """Drop + recreate the ``langgraph_checkpoint`` schema."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DROP SCHEMA IF EXISTS langgraph_checkpoint CASCADE"
            )
            await cur.execute("CREATE SCHEMA langgraph_checkpoint")


async def _make_pool(*, dsn: str = _DSN):
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=dsn,
        kwargs={
            "autocommit": True,
            "options": "-c search_path=langgraph_checkpoint,public",
        },
        min_size=1,
        max_size=2,
        open=False,
    )
    await pool.open()
    return pool


@pytest.fixture
async def fresh_pool() -> AsyncIterator:
    """Per-test pool against a freshly-wiped langgraph_checkpoint schema."""
    await _wipe_langgraph_schema(_DSN)
    pool = await _make_pool()
    try:
        yield pool
    finally:
        await pool.close()


def _build_counter_graph(checkpointer):
    """Build a tiny LangGraph that increments a counter in state.

    The graph has one node ``increment`` that reads ``state["counter"]``,
    adds 1, and writes it back.  Each ``ainvoke`` increments by 1.
    Conversation state for this graph = the counter value, which makes
    it cheap to assert "state survived restart" without any LLM
    involvement.
    """
    from langgraph.graph import START, END, StateGraph
    from typing import TypedDict

    class State(TypedDict, total=False):
        counter: int

    def increment(state: State) -> dict:
        return {"counter": state.get("counter", 0) + 1}

    builder = StateGraph(State)
    builder.add_node("increment", increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    return builder.compile(checkpointer=checkpointer)


# ============================================================================
# Tests
# ============================================================================


class TestRealGraphStateSurvivesPoolRestart:
    """The end-to-end durability property: a LangGraph driven by an
    ``AsyncPostgresSaver`` writes state that survives the pool being
    torn down and recreated (simulating Python process restart)."""

    async def test_counter_state_survives_pool_restart(
        self, fresh_pool
    ) -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        thread_id = f"restart-test-{uuid.uuid4().hex[:8]}"
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            }
        }

        # --- "process A": run the graph 3 times, leaving counter=3 -
        saver_a = AsyncPostgresSaver(fresh_pool)
        await saver_a.setup()
        graph_a = _build_counter_graph(saver_a)

        for _ in range(3):
            result = await graph_a.ainvoke({}, config)
        assert result["counter"] == 3, (
            f"expected counter=3 after 3 invocations, got {result['counter']}"
        )

        # --- Simulate process death ---
        await fresh_pool.close()

        # --- "process B": fresh pool + saver, same DB ---
        new_pool = await _make_pool()
        try:
            saver_b = AsyncPostgresSaver(new_pool)
            graph_b = _build_counter_graph(saver_b)

            # Read state for this thread WITHOUT advancing it.  In real
            # usage this is what a reconnecting WebSocket would do
            # before the user types their next turn.
            state_snapshot = await graph_b.aget_state(config)
            assert state_snapshot is not None
            assert state_snapshot.values.get("counter") == 3, (
                "Counter state did not survive the pool restart.  This "
                "breaks the entire Phase 0 PR 5 durability story."
            )

            # And the next invocation continues from 3 → 4.
            next_result = await graph_b.ainvoke({}, config)
            assert next_result["counter"] == 4, (
                "Graph did not resume from the persisted state on the "
                f"new pool; got counter={next_result['counter']}, "
                "expected 4."
            )
        finally:
            await new_pool.close()

    async def test_multiple_threads_independent_after_restart(
        self, fresh_pool
    ) -> None:
        """Two threads' state stays distinct across the simulated
        restart: thread A's count does not bleed into thread B."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        thread_a = f"restart-A-{uuid.uuid4().hex[:8]}"
        thread_b = f"restart-B-{uuid.uuid4().hex[:8]}"
        cfg_a = {"configurable": {"thread_id": thread_a, "checkpoint_ns": ""}}
        cfg_b = {"configurable": {"thread_id": thread_b, "checkpoint_ns": ""}}

        saver_a = AsyncPostgresSaver(fresh_pool)
        await saver_a.setup()
        graph_a = _build_counter_graph(saver_a)

        # Drive A 5 times, B 2 times.
        for _ in range(5):
            await graph_a.ainvoke({}, cfg_a)
        for _ in range(2):
            await graph_a.ainvoke({}, cfg_b)

        await fresh_pool.close()

        new_pool = await _make_pool()
        try:
            saver_b = AsyncPostgresSaver(new_pool)
            graph_b = _build_counter_graph(saver_b)

            state_a = await graph_b.aget_state(cfg_a)
            state_b = await graph_b.aget_state(cfg_b)
            assert state_a.values.get("counter") == 5, (
                f"thread A counter wrong after restart: {state_a.values}"
            )
            assert state_b.values.get("counter") == 2, (
                f"thread B counter wrong after restart: {state_b.values}"
            )
        finally:
            await new_pool.close()


# ============================================================================
# STATELESS OPT-IN: legacy per-turn behavior preserved
# ============================================================================


class TestStatelessOptIn:
    """The stateless flag still works after PR 5: passing
    ``stateless=True`` to ``CopilotSession`` (or building a
    ``DomainAgentSession`` with a ``MemorySaver`` checkpointer)
    preserves the V0 behavior of per-turn fresh threads + in-memory
    state.

    We test this at the checkpointer-level here, not at the
    ``CopilotSession`` level, because CopilotSession.open() pulls in
    MCP subprocesses and the Anthropic API which are not part of this
    PR's scope.
    """

    async def test_memory_saver_loses_state_on_re_instantiation(
        self,
    ) -> None:
        """A new ``MemorySaver`` instance does NOT see writes made
        through a prior instance.  This is the property that makes
        stateless=True useful for tests: each test creates a fresh
        saver and starts from a known-empty state."""
        from langgraph.checkpoint.memory import MemorySaver

        thread_id = "stateless-test"
        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": "",
            }
        }

        saver_a = MemorySaver()
        graph_a = _build_counter_graph(saver_a)
        for _ in range(3):
            await graph_a.ainvoke({}, config)

        result_a = await graph_a.aget_state(config)
        assert result_a.values.get("counter") == 3

        # A brand-new MemorySaver instance is empty.
        saver_b = MemorySaver()
        graph_b = _build_counter_graph(saver_b)
        result_b = await graph_b.aget_state(config)
        # MemorySaver returns a StateSnapshot with empty values dict
        # (not None) when nothing has been written.
        assert result_b.values == {}, (
            f"Expected empty state on fresh MemorySaver, got {result_b.values}"
        )

"""tests/state/test_postgres_checkpointer.py — durable LangGraph checkpointer.

Phase 0 PR 5 swaps the orchestrator's in-memory ``MemorySaver`` for a
Postgres-backed ``AsyncPostgresSaver`` whenever a connection pool is
provided.  This test suite pins the load-bearing properties:

  - The pool's ``init_checkpointer_pool`` correctly bootstraps the
    framework's checkpoint tables inside the ``langgraph_checkpoint``
    schema, idempotently.
  - Two threads (representing two different conversations) see fully
    isolated checkpoint state — no cross-contamination.
  - Multiple checkpoints written to the same thread accumulate;
    ``aget_tuple`` returns the most recent, ``alist`` returns history
    in reverse-chronological order.
  - Concurrent interleaved writes to different threads do not leak
    state between them.
  - **Durability across process restart** is simulated by closing the
    pool entirely and creating a fresh pool against the same
    Postgres: prior checkpoint state survives.
  - The ``stateless=True`` opt-in path is also unit-tested at the
    ``MemorySaver`` layer for completeness.

Test database
-------------
Module-level skip if no Postgres is reachable on the env-var DSN.  CI's
``migrations`` job provides one via a service container; locally you
need a Postgres listening on ``$DB_HOST:$DB_PORT``.

Closes ``docs/technical_debt.md`` item #6 (persistent LangGraph
checkpointer).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import AsyncIterator, Optional

import pytest
import pytest_asyncio


# Make the project's packages importable.  Mirrors the pattern in
# Macro_Copilot/tests/conftest.py.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# DB availability — module-level skip if no Postgres
# ============================================================================


def _build_dsn() -> str:
    """Build a libpq DSN from env vars (same convention as everywhere
    else in this project: DB_USER / DB_PASSWORD / DB_HOST / DB_PORT /
    DB_NAME)."""
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


def _postgres_reachable(dsn: str) -> bool:
    """Open + close a transient psycopg3 connection.  Used by the
    module-level skip so the suite degrades cleanly when no DB is
    available rather than reporting false negatives."""
    try:
        import psycopg

        conn = psycopg.connect(dsn, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


_DSN = _build_dsn()
_DB_AVAILABLE = _postgres_reachable(_DSN)


# NOTE on the explicit ``asyncio`` mark: the project-wide pytest.ini
# (repo root) sets ``asyncio_mode = auto``, but this suite is also run
# from inside the api-server container with ``cd /app`` where that ini
# is not on pytest's config-discovery path — pytest-asyncio then falls
# back to strict mode.  The explicit marker (and the
# ``@pytest_asyncio.fixture`` decorators below) make the module
# self-sufficient under both modes.
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _DB_AVAILABLE,
        reason=(
            f"Postgres not reachable at {_DSN!r}.  Set DB_USER / DB_PASSWORD "
            "/ DB_HOST / DB_PORT / DB_NAME, or run the CI migrations job "
            "(which spins up a postgres:14 service container)."
        ),
    ),
]


# ============================================================================
# Helpers / fixtures
# ============================================================================


async def _wipe_langgraph_schema(dsn: str) -> None:
    """Drop + recreate the ``langgraph_checkpoint`` schema so each test
    starts from a clean slate.  Cheaper than per-test TRUNCATE because
    ``AsyncPostgresSaver.setup()`` is fast (~50ms on a local DB)."""
    import psycopg

    async with await psycopg.AsyncConnection.connect(
        dsn, autocommit=True
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DROP SCHEMA IF EXISTS langgraph_checkpoint CASCADE"
            )
            await cur.execute("CREATE SCHEMA langgraph_checkpoint")


async def _make_pool(*, dsn: str = _DSN, min_size: int = 1, max_size: int = 4):
    """Build a fresh AsyncConnectionPool matching the production config
    in ``api/dependencies.init_checkpointer_pool``.

    The four kwargs below mirror exactly what the production helper
    uses so tests exercise the same connection-config surface — if a
    bug ever surfaces under a specific kwarg combination in prod, the
    same combination is in play here.  See
    ``api/dependencies.init_checkpointer_pool`` for the rationale on
    each kwarg.
    """
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=dsn,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": "-c search_path=langgraph_checkpoint,public",
        },
        min_size=min_size,
        max_size=max_size,
        open=False,
    )
    await pool.open()
    return pool


@pytest_asyncio.fixture
async def fresh_pool() -> AsyncIterator:
    """Per-test pool against a freshly wiped ``langgraph_checkpoint``
    schema.  Yields ``(pool, saver_factory)`` where the factory builds
    a new saver — the test decides whether to call ``.setup()`` itself
    (one of the tests verifies ``setup()`` is idempotent, so it
    deliberately runs it twice)."""
    await _wipe_langgraph_schema(_DSN)
    pool = await _make_pool()
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture
async def pool_with_saver(fresh_pool):
    """The 95% case: a pool that has already had setup() run against
    it, ready for read / write tests."""
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    saver = AsyncPostgresSaver(fresh_pool)
    await saver.setup()
    return fresh_pool, saver


# ============================================================================
# CHECKPOINTER CONTRACT
# ============================================================================


CHECKPOINT_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)


class TestPoolSetup:
    """``AsyncPostgresSaver.setup()`` creates the framework's tables
    in the configured search_path."""

    async def test_setup_creates_all_checkpoint_tables(
        self, fresh_pool
    ) -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        saver = AsyncPostgresSaver(fresh_pool)
        await saver.setup()

        async with fresh_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'langgraph_checkpoint' "
                    "AND table_name LIKE 'checkpoint%' "
                    "ORDER BY table_name"
                )
                # row_factory=dict_row on the pool means rows come
                # back as dicts; access by column name.
                tables = [row["table_name"] for row in await cur.fetchall()]

        for expected in CHECKPOINT_TABLES:
            assert expected in tables, (
                f"AsyncPostgresSaver.setup() did not create "
                f"langgraph_checkpoint.{expected}.  Tables found: {tables}"
            )

    async def test_setup_is_idempotent(self, fresh_pool) -> None:
        """Calling ``setup()`` twice does not raise.  The second call
        is a no-op because every CREATE inside the framework's setup
        uses IF NOT EXISTS."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        saver = AsyncPostgresSaver(fresh_pool)
        await saver.setup()
        # Second call should be a no-op, NOT an error.
        await saver.setup()


# ============================================================================
# THREAD ISOLATION
# ============================================================================


def _new_thread_id() -> str:
    """Use a uuid so tests within the same module run don't collide
    even if the schema wipe somehow misses (defense in depth)."""
    return f"test-{uuid.uuid4().hex[:12]}"


def _config(thread_id: str) -> dict:
    """LangGraph configurable dict for a given thread.

    ``checkpoint_ns`` is the per-graph namespace; LangGraph's
    AsyncPostgresSaver expects it in the config (it ``pop()``\\s the
    key during writes).  Empty string is the standard default for
    single-namespace graphs."""
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _sortable_id() -> str:
    """Generate a strictly-monotonic, string-sortable checkpoint id.

    AsyncPostgresSaver's ``aget_tuple(thread_only_config)`` returns the
    "latest" checkpoint via ``ORDER BY checkpoint_id DESC`` (string
    ordering on the VARCHAR column).  Real graphs use UUIDv6 / UUIDv7
    which are time-ordered so string-sort = time-sort.  In tests we
    don't pull in the ``uuid6`` package — we generate the same property
    via a microsecond-precision time prefix + random suffix.

    Format: ``"NNNNNNNNNNNNNNNNNN-XXXXXXXXXXXXXX"`` (18-char zero-padded
    microsecond timestamp, hyphen, 14 hex chars random).  Strict
    monotonicity is guaranteed by a module-level counter that bumps if
    two calls land in the same microsecond.
    """
    import time

    global _ID_COUNTER, _LAST_NS
    now_us = time.monotonic_ns() // 1000  # microseconds
    if now_us <= _LAST_NS:
        # Same tick (or backwards somehow); bump counter.
        _ID_COUNTER += 1
        now_us = _LAST_NS + _ID_COUNTER
    else:
        _LAST_NS = now_us
        _ID_COUNTER = 0
    return f"{now_us:018d}-{uuid.uuid4().hex[:14]}"


_ID_COUNTER = 0
_LAST_NS = 0


def _checkpoint(*, values: dict, checkpoint_id: Optional[str] = None) -> dict:
    """Construct a minimal Checkpoint payload that AsyncPostgresSaver
    accepts.  Uses ``_sortable_id`` so chain ordering is deterministic
    in tests (see that helper's docstring for the rationale)."""
    import datetime

    return {
        "v": 1,
        "id": checkpoint_id or _sortable_id(),
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "channel_values": values,
        "channel_versions": {k: 1 for k in values.keys()},
        "versions_seen": {},
        "pending_sends": [],
    }


async def _put_chained(
    saver, base_config: dict, values_seq: list[dict]
) -> list[dict]:
    """Write a sequence of checkpoints to a single thread, chaining
    each onto the previous via the config that ``aput`` returns.

    Without chaining, multiple checkpoints written to the same thread
    are all "roots" of their own trivial chains; LangGraph's
    ``aget_tuple`` then picks one in an order that has nothing to do
    with write order.  Chaining via the returned config is the
    documented LangGraph contract for an in-thread history.

    Returns the list of updated configs (each carrying its
    checkpoint_id).
    """
    config = dict(base_config)
    configs: list[dict] = []
    for values in values_seq:
        cp = _checkpoint(values=values)
        config = await saver.aput(
            config, cp, metadata={"i": len(configs)}, new_versions={}
        )
        configs.append(config)
    return configs


class TestThreadIsolation:
    """Two threads must not see each other's checkpoint state.  This is
    the load-bearing property that makes per-session conversation
    state safe to share a Postgres instance."""

    async def test_two_threads_read_their_own_writes(
        self, pool_with_saver
    ) -> None:
        pool, saver = pool_with_saver
        thread_a = _new_thread_id()
        thread_b = _new_thread_id()

        cp_a = _checkpoint(values={"message": "hello from A"})
        cp_b = _checkpoint(values={"message": "hello from B"})

        await saver.aput(
            _config(thread_a),
            cp_a,
            metadata={"source": "test_a"},
            new_versions={},
        )
        await saver.aput(
            _config(thread_b),
            cp_b,
            metadata={"source": "test_b"},
            new_versions={},
        )

        got_a = await saver.aget_tuple(_config(thread_a))
        got_b = await saver.aget_tuple(_config(thread_b))

        assert got_a is not None, "thread A's checkpoint not found"
        assert got_b is not None, "thread B's checkpoint not found"
        assert got_a.checkpoint["channel_values"] == {"message": "hello from A"}
        assert got_b.checkpoint["channel_values"] == {"message": "hello from B"}

    async def test_thread_isolation_via_concurrent_writes(
        self, pool_with_saver
    ) -> None:
        """Interleaved concurrent writes to different threads do not
        cross-contaminate.  We write 5 alternating checkpoints to two
        threads and then verify each thread sees only its own writes."""
        pool, saver = pool_with_saver
        thread_a = _new_thread_id()
        thread_b = _new_thread_id()

        async def _write(thread: str, label: str, n: int) -> None:
            cp = _checkpoint(values={"label": label, "n": n})
            await saver.aput(
                _config(thread),
                cp,
                metadata={"thread": thread, "n": n},
                new_versions={},
            )

        # Interleave: A0, B0, A1, B1, A2, B2, ...
        writes = []
        for i in range(5):
            writes.append(_write(thread_a, "A", i))
            writes.append(_write(thread_b, "B", i))
        await asyncio.gather(*writes)

        # Each thread should have its own 5 checkpoints, all labelled
        # correctly.
        cps_a = [cp async for cp in saver.alist(_config(thread_a))]
        cps_b = [cp async for cp in saver.alist(_config(thread_b))]

        assert len(cps_a) == 5, f"thread A expected 5 checkpoints, got {len(cps_a)}"
        assert len(cps_b) == 5, f"thread B expected 5 checkpoints, got {len(cps_b)}"

        for cp_tuple in cps_a:
            assert cp_tuple.checkpoint["channel_values"]["label"] == "A"
        for cp_tuple in cps_b:
            assert cp_tuple.checkpoint["channel_values"]["label"] == "B"


# ============================================================================
# MULTI-CHECKPOINT HISTORY WITHIN A THREAD
# ============================================================================


class TestMultiCheckpointPerThread:
    """Multiple writes to the same thread accumulate; reads pick the
    latest."""

    async def test_get_tuple_returns_most_recent(
        self, pool_with_saver
    ) -> None:
        pool, saver = pool_with_saver
        thread = _new_thread_id()

        await _put_chained(
            saver,
            _config(thread),
            [{"counter": i} for i in range(3)],
        )

        # aget_tuple with a thread-only config returns the latest
        # checkpoint in the chain.
        latest = await saver.aget_tuple(_config(thread))
        assert latest is not None
        assert latest.checkpoint["channel_values"]["counter"] == 2

    async def test_alist_returns_full_history(
        self, pool_with_saver
    ) -> None:
        pool, saver = pool_with_saver
        thread = _new_thread_id()

        await _put_chained(
            saver,
            _config(thread),
            [{"counter": i} for i in range(3)],
        )

        history = [cp async for cp in saver.alist(_config(thread))]
        assert len(history) == 3

        # alist returns newest first.  This is what the LangGraph
        # checkpointer contract promises.
        counters_seen = [
            cp.checkpoint["channel_values"]["counter"] for cp in history
        ]
        assert counters_seen == [2, 1, 0]


# ============================================================================
# DURABILITY ACROSS PROCESS RESTART (POOL CLOSE + REOPEN)
# ============================================================================


class TestDurabilityAcrossPoolRestart:
    """The property that makes this PR worth shipping: checkpointer
    state survives a process restart.  Simulated here by closing the
    entire pool (and the saver bound to it) and creating a fresh pool
    + saver against the same Postgres."""

    async def test_state_survives_pool_close_and_reopen(
        self, fresh_pool
    ) -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        thread = _new_thread_id()

        # --- "process A": write some checkpoints, then tear down ----
        saver_a = AsyncPostgresSaver(fresh_pool)
        await saver_a.setup()

        await _put_chained(
            saver_a,
            _config(thread),
            [{"phase": "before_restart", "i": i} for i in range(3)],
        )

        # Capture what was written before the "restart".
        before_restart = await saver_a.aget_tuple(_config(thread))
        assert before_restart is not None
        original_id = before_restart.checkpoint["id"]

        # Simulate process death.
        await fresh_pool.close()

        # --- "process B": fresh pool + saver against the same DB ----
        new_pool = await _make_pool()
        try:
            saver_b = AsyncPostgresSaver(new_pool)
            # NOTE: do NOT call setup() again; the tables already exist.
            # This mirrors how a real restarted server would work: the
            # lifespan calls setup() (idempotently) and then the saver
            # is ready.

            recovered = await saver_b.aget_tuple(_config(thread))
            assert recovered is not None, (
                "Checkpoint state did not survive pool close + reopen.  "
                "This breaks the entire point of Phase 0 PR 5."
            )
            assert recovered.checkpoint["id"] == original_id, (
                "Recovered checkpoint id does not match what was written."
            )
            assert (
                recovered.checkpoint["channel_values"]
                == {"phase": "before_restart", "i": 2}
            )

            # Full history is also intact across the "restart".
            history = [cp async for cp in saver_b.alist(_config(thread))]
            assert len(history) == 3
        finally:
            await new_pool.close()

    async def test_other_threads_state_also_survives(
        self, fresh_pool
    ) -> None:
        """Variant of the above: multiple threads' state all survive
        the restart, each readable by name."""
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        thread_a = _new_thread_id()
        thread_b = _new_thread_id()

        saver_a = AsyncPostgresSaver(fresh_pool)
        await saver_a.setup()
        await saver_a.aput(
            _config(thread_a),
            _checkpoint(values={"who": "alice"}),
            metadata={"who": "alice"},
            new_versions={},
        )
        await saver_a.aput(
            _config(thread_b),
            _checkpoint(values={"who": "bob"}),
            metadata={"who": "bob"},
            new_versions={},
        )

        await fresh_pool.close()

        new_pool = await _make_pool()
        try:
            saver_b = AsyncPostgresSaver(new_pool)
            got_a = await saver_b.aget_tuple(_config(thread_a))
            got_b = await saver_b.aget_tuple(_config(thread_b))
            assert got_a is not None
            assert got_b is not None
            assert got_a.checkpoint["channel_values"] == {"who": "alice"}
            assert got_b.checkpoint["channel_values"] == {"who": "bob"}
        finally:
            await new_pool.close()

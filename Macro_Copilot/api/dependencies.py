"""
dependencies.py — Shared API dependencies
==========================================

Provides the singletons managed by FastAPI's app lifespan, exposed
through dependency-injection getters:

  - ``get_engine()``               — SQLAlchemy engine (psycopg2;
    market-data ingestion + REST routes).
  - ``get_checkpointer_pool()``    — psycopg3 ``AsyncConnectionPool``
    for the LangGraph ``AsyncPostgresSaver`` (Phase 0 PR 5).

Both singletons are created once during app startup (see
``api/server.py``'s lifespan) and disposed on shutdown.  Tests that
need to spin up their own can call ``init_*`` / ``dispose_*``
directly.

This module also centralises configuration so environment variables
are read in exactly one place.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Generator, Optional, TYPE_CHECKING

from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    # psycopg-pool only imported lazily (at init_checkpointer_pool
    # time) so a CI job that doesn't need the checkpointer doesn't pay
    # for the import.  TYPE_CHECKING guards the type hint.
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger("api.dependencies")

# ---------------------------------------------------------------------------
# Path setup — ensure the project root is importable so we can reach
# database.database, rates_agent.tools.*, etc.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Load .env — same pattern as the orchestrator (config.py) so DB_HOST,
# DB_PORT, etc. are available to get_db_engine() without manual export.
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    _env_path = PROJECT_ROOT / ".env"
    if _env_path.exists():
        load_dotenv(_env_path)
except ImportError:
    pass  # python-dotenv is optional; env vars can be set externally

from database.database import get_db_engine  # noqa: E402


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class Settings:
    """Centralised config — reads from environment with sensible defaults."""

    # CORS: origins allowed to call the API (the Vite dev server)
    CORS_ORIGINS: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
    ]

    # Default sovereign curves for the Rates page cards
    RATES_CURVES: list[str] = ["UST", "DE_BUND", "UK_GILT", "JGB"]
    RATES_KEY_TENORS: list[str] = ["2Y", "5Y", "10Y", "30Y"]

    # Default cross-market spread pairs (curve_family_1, curve_family_2, tenor)
    CROSS_MARKET_PAIRS: list[tuple[str, str, str]] = [
        ("IT_BTP", "DE_BUND", "10Y"),
        ("FR_OAT", "DE_BUND", "10Y"),
        ("UST", "DE_BUND", "10Y"),
    ]

    # Default regime lookback periods
    REGIME_PERIODS: list[str] = ["1d", "5d"]


settings = Settings()


# ---------------------------------------------------------------------------
# Database engine — singleton managed by FastAPI lifespan
# ---------------------------------------------------------------------------
_engine: Engine | None = None


def init_engine() -> Engine:
    """Create the engine singleton.  Called once during app startup."""
    global _engine
    if _engine is None:
        _engine = get_db_engine()
    return _engine


def get_engine() -> Engine:
    """FastAPI dependency that returns the shared engine.

    Usage in route handlers::

        @router.get("/endpoint")
        def my_endpoint(engine: Engine = Depends(get_engine)):
            ...
    """
    if _engine is None:
        raise RuntimeError(
            "Database engine not initialised. "
            "Ensure init_engine() is called during app lifespan."
        )
    return _engine


# ---------------------------------------------------------------------------
# LangGraph checkpointer pool — Phase 0 PR 5
# ---------------------------------------------------------------------------
# The orchestrator's ``AsyncPostgresSaver`` reads / writes conversation
# state through this pool.  Lifecycle:
#
#   - ``init_checkpointer_pool()`` is called once at app startup from
#     ``api/server.py``'s lifespan.  It creates the
#     ``AsyncConnectionPool``, opens it, and runs
#     ``AsyncPostgresSaver(pool).setup()`` which creates the
#     framework's checkpoint tables inside the ``langgraph_checkpoint``
#     schema (the schema namespace itself is created by Alembic
#     migration ``0003_langgraph_checkpoint_schema``).
#
#   - Every WebSocket session that wants durability calls
#     ``get_checkpointer_pool()`` to obtain the shared pool.  Sessions
#     that pass ``stateless=True`` (e.g. unit tests) ignore this and
#     use an in-memory checkpointer instead.
#
#   - ``dispose_checkpointer_pool()`` is called on app shutdown to
#     close the pool cleanly.
#
# The pool is configured with ``autocommit=True`` because
# ``AsyncPostgresSaver.setup()`` uses ``CREATE INDEX CONCURRENTLY``
# which cannot run inside a transaction.  Normal checkpoint reads /
# writes work fine under autocommit because they are single
# statements; LangGraph handles its own retry / consistency at the
# application layer.
_checkpointer_pool: Optional["AsyncConnectionPool"] = None


async def init_checkpointer_pool(
    min_size: int = 1,
    max_size: int = 10,
) -> "AsyncConnectionPool":
    """Create + open the checkpointer pool, run AsyncPostgresSaver.setup().

    Idempotent: a second call returns the existing pool without
    re-running setup.

    The pool's connection ``search_path`` is set to
    ``langgraph_checkpoint,public`` so the framework's tables created
    by ``setup()`` land in our dedicated schema rather than ``public``.

    ``min_size=1`` / ``max_size=10`` are conservative defaults — most
    Phase 0 deployments will have a handful of concurrent WebSocket
    sessions.  Production deployments should tune these based on
    observed connection-acquire latency.
    """
    global _checkpointer_pool
    if _checkpointer_pool is not None:
        return _checkpointer_pool

    # Lazy import keeps the module load-light when the checkpointer
    # isn't needed (e.g. CI jobs that only run substrate tests).
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    from orchestrator.config import (
        LANGGRAPH_CHECKPOINT_SCHEMA,
        build_checkpointer_dsn,
    )

    dsn = build_checkpointer_dsn()
    logger.info(
        "Initialising checkpointer pool against %s (search_path=%s)",
        # Mask password in log; we only need host:port:db for diagnostics.
        dsn.split("@", 1)[-1] if "@" in dsn else dsn,
        LANGGRAPH_CHECKPOINT_SCHEMA,
    )

    pool = AsyncConnectionPool(
        conninfo=dsn,
        # The four kwargs below are required by AsyncPostgresSaver
        # when manually constructed against a pool (as opposed to
        # using ``AsyncPostgresSaver.from_conn_string``).  Each maps
        # directly to what the upstream library sets on connections
        # it manages itself:
        #
        #   autocommit=True
        #       Required by ``setup()`` because it issues
        #       ``CREATE INDEX CONCURRENTLY`` which forbids
        #       transaction blocks.  Regular checkpoint reads /
        #       writes also work fine under autocommit (LangGraph
        #       handles its own retry / consistency semantics at
        #       the application layer).
        #
        #   prepare_threshold=0
        #       Disables psycopg3's automatic statement-preparation
        #       caching.  With the default (5) and a connection pool,
        #       you can hit the canonical
        #       ``cannot send pipeline when not in pipeline mode``
        #       error when prepared-statement state on a pooled
        #       connection drifts across handoffs.  LangGraph's
        #       upstream ``from_conn_string`` sets this; we mirror
        #       it.  See langchain-ai/langgraph#3193.
        #
        #   row_factory=dict_row
        #       The checkpointer's queries access rows by name
        #       (``row["thread_id"]``).  Without ``dict_row``,
        #       psycopg3's default ``tuple_row`` would break those
        #       lookups silently or noisily depending on the path.
        #
        #   options=-c search_path=...
        #       Puts AsyncPostgresSaver's tables in our dedicated
        #       schema rather than polluting ``public``.
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={LANGGRAPH_CHECKPOINT_SCHEMA},public",
        },
        min_size=min_size,
        max_size=max_size,
        # Defer pool open() until we explicitly call it, so we can
        # decide whether to swallow open errors at startup vs at
        # first-use.  Open errors at startup mean "the API server
        # crashes loudly"; first-use errors mean "the first
        # WebSocket gets a clean error message".  We want the former.
        open=False,
    )
    await pool.open()

    # Run setup() once per process.  The framework's setup is
    # idempotent (CREATE TABLE IF NOT EXISTS for everything that
    # doesn't require CONCURRENTLY; CONCURRENTLY indexes use IF NOT
    # EXISTS too in psycopg3), so this is safe to call on every
    # startup including hot reloads.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    saver = AsyncPostgresSaver(pool)
    await saver.setup()
    logger.info("AsyncPostgresSaver.setup() completed")

    _checkpointer_pool = pool
    return pool


async def dispose_checkpointer_pool() -> None:
    """Close the checkpointer pool.  Called from app shutdown."""
    global _checkpointer_pool
    if _checkpointer_pool is None:
        return
    logger.info("Disposing checkpointer pool")
    try:
        await _checkpointer_pool.close()
    finally:
        _checkpointer_pool = None


def get_checkpointer_pool() -> Optional["AsyncConnectionPool"]:
    """Return the shared checkpointer pool, or None if not initialised.

    Returning None (rather than raising) is the intentional contract:
    sessions that want durability check ``pool is not None`` and fall
    back to an in-memory checkpointer if not.  This lets unit tests
    construct a ``CopilotSession`` without spinning up Postgres.

    Production WebSocket handlers should treat ``None`` as a startup
    misconfiguration and surface an error to the client; see
    ``api/routes/chat.py``.
    """
    return _checkpointer_pool

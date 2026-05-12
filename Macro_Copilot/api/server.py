"""
server.py — FastAPI Application Server
========================================

Entry point for the Macro Copilot API.  Serves two classes of endpoints:

1.  **REST endpoints** — power the Rates page cards and other dashboard
    views.  These call the Python tool functions directly (no LLM, no MCP),
    so they return in <100ms.

2.  **WebSocket endpoint** (future) — powers the Copilot chat drawer.
    Routes freeform user messages through the LangGraph orchestrator,
    which decides which MCP tools to invoke.

Architecture
------------
The API layer sits between the React frontend and the existing tool stack::

    React (Vite)  ──REST──▸  FastAPI  ──direct import──▸  tool functions
                  ──WS────▸  FastAPI  ──LangGraph──▸  MCP ──▸  tool functions

The REST path is for structured, pre-defined queries (page cards).
The WebSocket path is for freeform queries (copilot chat).

Running
-------
From the project root::

    uvicorn api.server:app --host 0.0.0.0 --port 8000 --reload

Or in Docker, add a service to docker-compose.yml.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.dependencies import (
    dispose_checkpointer_pool,
    dispose_object_storage,
    init_checkpointer_pool,
    init_engine,
    init_object_storage,
    settings,
)
from api.routes.rates import router as rates_router
from api.routes.workflows import router as workflows_router
from api.routes.library import router as library_router
from api.routes import chat as chat_routes

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api.server")


# ---------------------------------------------------------------------------
# Lifespan — initialise shared resources on startup, clean up on shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle.

    - **Startup**: create the SQLAlchemy engine pool AND the LangGraph
      checkpointer's psycopg3 connection pool.  ``AsyncPostgresSaver.setup()``
      runs during pool init to ensure the framework's tables exist in
      the ``langgraph_checkpoint`` schema (created by Alembic migration
      ``0003_langgraph_checkpoint_schema``).
    - **Shutdown**: dispose both pools.

    Two pools, two Postgres drivers (psycopg2 for SQLAlchemy / ingestion,
    psycopg3 for the async checkpointer), one Postgres.  This split is
    documented in ``docs/architecture/state_schema.md``.

    Checkpointer pool init failure logic
    ------------------------------------
    If ``init_checkpointer_pool()`` raises (e.g. Postgres is down or
    the ``langgraph_checkpoint`` schema does not exist because alembic
    has not been run), the API starts WITHOUT a checkpointer pool.
    WebSocket sessions will fall back to in-memory state (no durability
    across restart) and emit a clear warning at handshake time.  This
    is the right operational contract: an API that can serve REST
    traffic but has no chat-state durability is more useful than an
    API that refuses to start.
    """
    logger.info("Initialising database engine...")
    engine = init_engine()
    logger.info("Database engine ready.  Pool size=%s", engine.pool.size())

    try:
        await init_checkpointer_pool()
        logger.info("LangGraph checkpointer pool ready")
    except Exception as exc:
        # See docstring — degraded operation rather than refuse to start.
        logger.error(
            "Failed to initialise checkpointer pool; WebSocket sessions "
            "will run with in-memory state (lost on restart): %s",
            exc,
        )

    # Phase 0 PR 7: artifact-store object-storage backend.  Same
    # degraded-operation contract as the checkpointer pool —
    # initialisation failure logs an error and the API keeps
    # serving; routes that need the artifact store will fail loudly
    # at request time rather than at startup.
    try:
        init_object_storage()
        logger.info("Artifact-store object-storage backend ready")
    except Exception as exc:
        logger.error(
            "Failed to initialise object-storage backend; "
            "artifact-store routes will not function: %s",
            exc,
        )

    yield

    logger.info("Shutting down — disposing object-storage backend.")
    try:
        dispose_object_storage()
    except Exception:
        logger.exception("Error disposing object-storage backend (non-fatal)")
    logger.info("Shutting down — disposing checkpointer pool.")
    try:
        await dispose_checkpointer_pool()
    except Exception:
        logger.exception("Error disposing checkpointer pool (non-fatal)")
    logger.info("Shutting down — disposing database engine.")
    engine.dispose()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Macro Copilot API",
    version="0.1.0",
    description=(
        "REST + WebSocket API for the Macro Copilot frontend.  "
        "Serves deterministic rates analytics and LLM-orchestrated chat."
    ),
    lifespan=lifespan,
)

# CORS — allow the Vite dev server (and future production origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Mount route modules
# ---------------------------------------------------------------------------
app.include_router(
    rates_router,
    prefix="/api/v1/rates",
    tags=["Rates"],
)

# PR 10: workflow-template + tool catalogue REST surface.  Sits
# alongside the rates routes — same in-process compute path, distinct
# concern (workflows are DAG-level; rates routes are primitive-level).
# See ``api/routes/workflows/__init__.py`` for the full surface.
app.include_router(
    workflows_router,
    prefix="/api/v1",
    tags=["Workflows"],
)

# Library catalogue surface — reads `manifesto/03_tool_manifest/<agent>/*.yml`
# and serves the parsed catalogue as JSON.  The Library page is rendered
# directly from this response (no hardcoded tool entries in the UI).
app.include_router(
    library_router,
    prefix="/api/v1",
    tags=["Library"],
)

app.include_router(
    chat_routes.router,
    prefix="/api",
    tags=["Chat"],
)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/api/health", tags=["System"])
def health_check():
    """Simple liveness probe for Docker / load balancer health checks."""
    return {"status": "ok", "service": "macro-copilot-api"}

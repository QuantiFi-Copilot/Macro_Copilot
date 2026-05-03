"""
server.py — FastAPI Application Server
========================================

Entry point for the Macro Copilot API.  Serves two classes of endpoints:

1.  REST endpoints — power the Rates page cards and other dashboard
    views.  These call the Python tool functions directly (no LLM, no MCP),
    so they return in <100ms.

2.  WebSocket endpoint — powers the Copilot chat drawer.
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

from api.dependencies import init_engine, settings
from api.routes.rates import router as rates_router
from api.routes.fx import cards_router as fx_cards_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api.server")


# ---------------------------------------------------------------------------
# Optional chat routes
# ---------------------------------------------------------------------------
try:
    from api.routes import chat as chat_routes
except ModuleNotFoundError as exc:
    chat_routes = None
    logger.warning(
        "Chat routes disabled because optional dependency is missing: %s",
        exc,
    )


# ---------------------------------------------------------------------------
# Lifespan — initialise shared resources on startup, clean up on shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle.

    - Startup: create the SQLAlchemy engine pool.
    - Shutdown: dispose the engine pool.
    """
    logger.info("Initialising database engine...")
    engine = init_engine()
    logger.info("Database engine ready. Pool size=%s", engine.pool.size())
    yield
    logger.info("Shutting down — disposing database engine.")
    engine.dispose()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Macro Copilot API",
    version="0.1.0",
    description=(
        "REST + WebSocket API for the Macro Copilot frontend. "
        "Serves deterministic rates/FX analytics and LLM-orchestrated chat."
    ),
    lifespan=lifespan,
)

# CORS — allow the Vite dev server and future production origins
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

app.include_router(
    fx_cards_router,
    prefix="/api/v1/fx",
    tags=["FX"],
)

if chat_routes is not None:
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
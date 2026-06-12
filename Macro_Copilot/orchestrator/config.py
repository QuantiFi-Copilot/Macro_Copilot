"""
orchestrator/config.py — Centralised Configuration
====================================================

Every environment variable the orchestrator needs is loaded here from
the ``.env`` file and validated once at import time.  No other module
in ``orchestrator/`` should call ``os.getenv`` directly.

This mirrors the pattern already established in ``database/database.py``
for DB credentials, but adds explicit ``.env`` file loading via
``python-dotenv`` so credentials don't evaporate when you close a
terminal session.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from orchestrator.contracts import Domain

# ---------------------------------------------------------------------------
# Locate and load the .env file.
# Priority: project root → /app/.env (Docker mount) → shell env only.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent

_candidates = [
    PROJECT_ROOT / ".env",
    Path("/app/.env"),
]

for candidate in _candidates:
    if candidate.is_file():
        load_dotenv(candidate, override=False)
        break


# ===========================================================================
# LLM CONFIGURATION
# ===========================================================================

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
# Migrated 2026-06-12: claude-sonnet-4-20250514 is DEPRECATED (retires
# 2026-06-15).  claude-sonnet-4-6 validated head-to-head on the exact
# production prompts (offline rig, temp 0): 12/13 L1 lane/shape cells
# identical, selector date-anchor binding exact (1259/730), gate
# targets+guards held (G2 improved REFUSE→CLARIFY per the gate's own
# tie-break preference), answer cells grounded ("F = 0.93 (p ≈ 0.46)").
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))

# Orchestration-upgrade plan D7: the L3 Composer's model is a CONFIG KNOB,
# not a baked constant — so upgrading it (e.g. to a newer Opus that handles
# the disambiguation cases more robustly) is a one-line / one-env-var flip,
# and the deterministic verifier + self-correction loop mean correctness
# never DEPENDS on a specific version (the upgrade only raises the floor).
# Upgraded 2026-06-12 to claude-opus-4-8 after the composer A/B on the
# exact production prompts: 4-8 composed all four FM-11 wrong-refusal
# targets (h02/r01/s11/s10) with chains equal to or simpler than 4-6's,
# and held every guard (incl. the honest refusals).  4-7+/4-8 take NO
# sampling params — anthropic_chat_kwargs below handles the surface.
COMPOSER_MODEL: str = os.getenv("COMPOSER_MODEL", "claude-opus-4-8")


# Gate (L4.5) + Answer (L6) model knobs.  Hardcoded "claude-sonnet-4-5"
# pins pre-dated these; env-configurable so model A/B experiments run
# without code edits.  Defaults unchanged.
GATE_MODEL: str = os.getenv("GATE_MODEL", "claude-sonnet-4-6")
ANSWER_MODEL: str = os.getenv("ANSWER_MODEL", "claude-sonnet-4-6")


# Models where the Anthropic API REMOVED sampling params (temperature /
# top_p / top_k return 400): Opus 4.7+, Fable.  See the claude-api
# migration guide ("Sampling parameters removed").  Sonnet 4.x and
# Opus <=4.6 still accept temperature.
_NO_SAMPLING_PARAM_PREFIXES = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-fable",
)


def anthropic_chat_kwargs(
    *, model_name: str, temperature: float, max_tokens: int
) -> dict:
    """Build ChatAnthropic constructor kwargs honouring per-model API
    surfaces: models that removed sampling params get NO temperature
    key (sending one is a hard 400), everything else keeps the
    explicit temperature (production determinism convention).
    """
    kwargs: dict = {"model": model_name, "max_tokens": max_tokens}
    if not model_name.startswith(_NO_SAMPLING_PARAM_PREFIXES):
        kwargs["temperature"] = temperature
    return kwargs


# ===========================================================================
# MCP SERVER DEFINITIONS
# ===========================================================================
# Each entry maps a logical agent name to the stdio subprocess config
# that MultiServerMCPClient expects.  Adding a new agent = one new entry.
#
# We use sys.executable so the subprocess always runs in the same venv
# as the orchestrator — no PATH surprises.
#
# IMPORTANT: stdio subprocesses do NOT automatically inherit the parent's
# environment.  We explicitly pass through the DB credentials and any
# other env vars the MCP server needs.  This is the fix for the
# "localhost:5433 connection refused" error when running inside Docker
# (where DB_HOST=tsdb, DB_PORT=5432).

_PYTHON = sys.executable

_MCP_SUBPROCESS_ENV: dict = {
    "DB_HOST": os.getenv("DB_HOST", "localhost"),
    "DB_PORT": os.getenv("DB_PORT", "5433"),
    "DB_USER": os.getenv("DB_USER", "quantuser"),
    "DB_PASSWORD": os.getenv("DB_PASSWORD", "myStrongPass"),
    "DB_NAME": os.getenv("DB_NAME", "macrodata"),
}

MCP_SERVERS: dict = {
    "rates_agent": {
        "transport": "stdio",
        "command": _PYTHON,
        "args": ["-m", "rates_agent.sovereign_bonds.mcp_server"],
        "cwd": str(PROJECT_ROOT),
        "env": _MCP_SUBPROCESS_ENV,
    },
    "ois_agent": {
        "transport": "stdio",
        "command": _PYTHON,
        "args": ["-m", "rates_agent.ois.mcp_server"],
        "cwd": str(PROJECT_ROOT),
        "env": _MCP_SUBPROCESS_ENV,
    },
    "inflation_indexed_bonds_agent": {
        "transport": "stdio",
        "command": _PYTHON,
        "args": ["-m", "rates_agent.inflation_indexed_bonds.mcp_server"],
        "cwd": str(PROJECT_ROOT),
        "env": _MCP_SUBPROCESS_ENV,
    },
    "inflation_swaps_agent": {
        "transport": "stdio",
        "command": _PYTHON,
        "args": ["-m", "rates_agent.inflation_swaps.mcp_server"],
        "cwd": str(PROJECT_ROOT),
        "env": _MCP_SUBPROCESS_ENV,
    },
    "policy_futures_agent": {
        "transport": "stdio",
        "command": _PYTHON,
        "args": ["-m", "rates_agent.policy_futures.mcp_server"],
        "cwd": str(PROJECT_ROOT),
        "env": _MCP_SUBPROCESS_ENV,
    },
    "bond_futures_agent": {
        "transport": "stdio",
        "command": _PYTHON,
        "args": ["-m", "rates_agent.bond_futures.mcp_server"],
        "cwd": str(PROJECT_ROOT),
        "env": _MCP_SUBPROCESS_ENV,
    },
    # PR 9: workflow-template MCP server.  Distinct from the per-domain
    # primitive servers above — exposes DAG-shaped analyses
    # (event_study, regime_conditioned_relationship, ...) plus the
    # ``list_workflows`` / ``describe_workflow_template`` catalogue
    # tools.  Not yet wired into ``DOMAIN_MCP_SERVERS`` because
    # WorkflowRouter (orchestrator/workflow_router.py) is a separate
    # routing layer parallel to Supervisor; folding workflows into the
    # main session pipeline is a follow-up PR.  This entry exists so
    # the config map already documents the workflows server's stdio
    # subprocess shape for that future integration.
    "workflows_agent": {
        "transport": "stdio",
        "command": _PYTHON,
        "args": ["-m", "rates_agent.workflows.mcp_server"],
        "cwd": str(PROJECT_ROOT),
        "env": _MCP_SUBPROCESS_ENV,
    },
    # ── Future agents ──────────────────────────────────────────────
    # "fx_agent": {
    #     "transport": "stdio",
    #     "command": _PYTHON,
    #     "args": ["-m", "fx_agent.mcp_server"],
    #     "cwd": str(PROJECT_ROOT),
    #     "env": _MCP_SUBPROCESS_ENV,
    # },
}


# ===========================================================================
# DOMAIN → MCP SERVER MAPPING
# ===========================================================================
# The supervisor/child architecture requires each domain child to own its
# own MCP client instance — hard-isolation at the client level, not just
# at the prompt level.  A child session constructed for ``Domain.OIS`` is
# physically unable to see ``calculate_curve_spread_tool`` because that
# tool lives on a subprocess it never connected to.
#
# ``langchain-mcp-adapters`` does not expose a per-server tool filter, so
# the enforcement mechanism is: one MultiServerMCPClient per domain, each
# initialised with only that domain's server config.

# PR-10F gap #2: DOMAIN_MCP_SERVERS is now BUILT from the
# orchestrator.domain_registry's DOMAIN_SPECS at import time.  Each
# rates_agent/<domain>/__init__.py declares its own
# __mcp_server_module__ + __mcp_client_key__; this dict comprehension
# wires them into the per-domain stdio subprocess config.  Adding the
# Nth domain requires NO edit to this dict.
from orchestrator.domain_registry import DOMAIN_SPECS as _DOMAIN_SPECS


def _build_domain_mcp_server(module_path: str) -> dict:
    """Construct one stdio MCP-subprocess config from a module path."""
    return {
        "transport": "stdio",
        "command": _PYTHON,
        "args": ["-m", module_path],
        "cwd": str(PROJECT_ROOT),
        "env": _MCP_SUBPROCESS_ENV,
    }


DOMAIN_MCP_SERVERS: dict = {
    Domain(spec.domain_id): {
        spec.mcp_client_key: _build_domain_mcp_server(spec.mcp_server_module),
    }
    for spec in _DOMAIN_SPECS.values()
}


# ===========================================================================
# CHECKPOINTER (PostgresSaver) DSN
# ===========================================================================
# Phase 0 PR 5: the LangGraph checkpointer migrates from in-memory to
# Postgres-backed.  ``AsyncPostgresSaver`` runs on psycopg3
# (``psycopg[binary,pool]``) which coexists with the existing
# psycopg2-based SQLAlchemy ingestion path.  Two drivers, two connection
# layers, one Postgres.
#
# This helper builds the psycopg3-style DSN from the same ``DB_*`` env
# vars as ``database.database.get_db_engine`` and ``migrations/env.py``,
# so dev / staging / prod all use one consistent connection convention.


def build_checkpointer_dsn() -> str:
    """Return the psycopg3 DSN for the LangGraph checkpointer pool.

    Uses the same ``DB_USER`` / ``DB_PASSWORD`` / ``DB_HOST`` / ``DB_PORT``
    / ``DB_NAME`` env vars as the rest of the project (so a dev
    environment configured for the ingestion pipeline gets the
    checkpointer for free).

    Defaults match the docker-compose ``tsdb`` service and are
    **deliberately** unsafe outside the local Docker network — they
    must be overridden in CI / staging / prod.

    Returns a libpq-style URI (``postgresql://...``) without the
    ``+psycopg2`` driver hint that SQLAlchemy uses, because the
    checkpointer pool talks to psycopg3 directly, not through
    SQLAlchemy.
    """
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    # psycopg3 accepts URI-style DSNs.  Empty password is rendered as
    # ``user:@host`` which is valid for trust-auth dev setups.
    return f"postgresql://{user}:{password}@{host}:{port}/{db_name}"


# Schema name that LangGraph's checkpoint tables live in.  Set on the
# pool's connection options as ``search_path=<this>,public`` so
# ``AsyncPostgresSaver.setup()`` creates its tables in this namespace
# rather than ``public``.  Created by Alembic migration
# ``0003_langgraph_checkpoint_schema``.
LANGGRAPH_CHECKPOINT_SCHEMA: str = "langgraph_checkpoint"


# ===========================================================================
# VALIDATION
# ===========================================================================

def validate() -> None:
    """
    Call at startup to fail fast with a clear message rather than a
    cryptic traceback three layers deep.
    """
    errors: list[str] = []

    if not ANTHROPIC_API_KEY:
        errors.append(
            "ANTHROPIC_API_KEY is not set.  Add it to your .env file:\n"
            "    ANTHROPIC_API_KEY=sk-ant-..."
        )

    if errors:
        for e in errors:
            print(f"[CONFIG ERROR] {e}", file=sys.stderr)
        sys.exit(1)

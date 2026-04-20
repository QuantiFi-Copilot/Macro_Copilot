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
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-20250514")
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "4096"))


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

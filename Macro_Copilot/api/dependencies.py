"""
dependencies.py — Shared API dependencies
==========================================

Provides the SQLAlchemy engine singleton via FastAPI's dependency injection
system.  The engine is created once during app lifespan and shared across
all request handlers.

This module also centralises configuration so environment variables are
read in exactly one place.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

from sqlalchemy.engine import Engine

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

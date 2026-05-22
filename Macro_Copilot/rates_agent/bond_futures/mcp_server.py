"""
mcp_server.py — MCP Server for the Bond Futures Sub-Agent
=========================================================

Exposes the bond-futures domain tools to the orchestrator via stdio
MCP. Each tool has flat scalar parameters; Pydantic validation happens
inside. Lazy engine singleton. Logging to stderr.

V1 SCAFFOLDING — no tools registered yet. The OpenClaw
primitive-automation factory registers tools here as it builds each
catalogued primitive (see ``Macro_Copilot/automation/primitive_automation/
primitive_catalog.yaml`` and the ``REPO_REFERENCE_MAP.md``).

V1 ships MONITORS ONLY (per ADR 0011)
-------------------------------------
The desk-recognised RV stack on bond futures (CTD identification,
gross / net basis, implied repo rate, DV01-weighted inter-commodity
spreads, cross-country DV01 + FX-adjusted spreads) is PHASE-4 work,
gated on D-repo + D-deliverable data ingestion that has not landed
yet. This V1 ships:

1. ``get_futures_price_level_tool``           — front-month bond-futures
   price + Δ + 252d range / z-score.
2. ``get_futures_volume_oi_tool``             — daily volume / OI /
   Δ-OI / OI z-score (front-back OI migration is the positioning
   signal).
3. ``scan_bond_futures_extremes_tool``        — morning bond-futures
   sweep by absolute z-score across the universe.

Each tool's methodology card MUST disclose: "this is CTD-of-rolling-
generic price, not a clean tenor-anchored yield; for CTD-implied
yield see the Phase-4 stack" (P5 honest disclosure).

The TY1 / UXY1 (10Y) and US1 / WN1 (30Y) ambiguity is resolved at
fetch time via the ``contract_code`` disambiguator (TD#11 — already
in the playbook + the shared fetcher).

Conventions for this domain
---------------------------
- Quoted in PRICE. The price IS the headline read for monitors; do
  NOT back-of-the-envelope a yield from it.
- ``contract_code`` (TY1 / UXY1 / US1 / WN1 / RX1 / JB1 / ...) is the
  canonical instrument identifier; ``curve_family`` (UST_FUT, DE_FUT,
  ...) groups them, and ``contract_code`` disambiguates within a
  ``(curve_family, tenor)`` pair (TD#11).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from shared.config import load_tool_config  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.bond_futures.mcp_server")

_engine = None


def _get_engine():
    """Return (and cache) a SQLAlchemy engine using env-var config."""
    global _engine
    if _engine is None:
        logger.info("Initializing database engine...")
        _engine = get_db_engine()
        logger.info("Database engine ready.")
    return _engine


mcp = FastMCP(
    name="bond-futures-agent",
    instructions=(
        "You are the Bond Futures Sub-Agent for a macro hedge-fund "
        "desk. You have access to tools that perform deterministic "
        "calculations over a TimescaleDB database of daily sovereign-"
        "bond futures price + volume + open-interest quotes (TY1, "
        "UXY1, US1, WN1, RX1, JB1, etc. across UST_FUT / DE_FUT / "
        "UK_FUT / JP_FUT and analogues). V1 SHIPS MONITORS ONLY — "
        "front-month price level, volume / open-interest, and a "
        "morning extreme scan. Use these tools to answer questions "
        "about price levels, Δ-OI / volume / OI z-scores, and "
        "morning sweeps across the bond-futures universe. Never "
        "attempt the math yourself — always call a tool and relay its "
        "output. When relaying price, ALWAYS preserve the methodology "
        "disclosure: 'this is rolling-generic price; the CTD-implied "
        "yield is not yet a primitive in this build' (P5). Do NOT "
        "back-of-the-envelope a yield from the price; the CTD path "
        "requires deliverable-basket + conversion-factor metadata "
        "that is Phase-4 work gated on D-repo + D-deliverable. Do "
        "NOT route policy / STIR futures (SFR / ER / SFI) here — "
        "those belong to the policy_futures agent. Do NOT route cash "
        "sovereign yield questions here — those belong to the "
        "sovereign_bonds agent. If the user asks about CTD-implied "
        "yields, basis, or DV01-weighted RV, respond with "
        "out_of_scope and explain those primitives are pending the "
        "deliverable basket + conversion factor + repo data "
        "ingestion."
    ),
)


# ===========================================================================
# TOOL REGISTRATIONS
# ===========================================================================
# Tools are registered here as the OpenClaw primitive-automation factory
# builds each catalogued primitive. Mirror the per-tool wrapper pattern
# from ``rates_agent/ois/mcp_server.py``:
#
#   1. Import CONFIG_PATH + Input schema + compute function from
#      ``rates_agent.bond_futures.tools.<tool_name>``.
#   2. Add a ``@mcp.tool()`` wrapper that validates input via the
#      Pydantic schema, loads the bundled config via
#      ``load_tool_config(CONFIG_PATH)``, calls the compute function
#      with ``config=`` passed explicitly, and returns the JSON
#      response with LLM-non-friendly fields stripped.
#   3. Add a corresponding entry to
#      ``rates_agent/bond_futures/tools/schemas/__init__.py`` so the
#      schemas hub stays a stable import surface.


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Bond Futures Sub-Agent MCP server (stdio transport)..."
    )
    mcp.run(transport="stdio")

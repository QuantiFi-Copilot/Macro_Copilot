"""
mcp_server.py — MCP Server for the Policy Futures (STIR strip) Sub-Agent
========================================================================

Exposes the policy-futures domain tools to the orchestrator via stdio
MCP. Each tool has flat scalar parameters; Pydantic validation happens
inside. Lazy engine singleton. Logging to stderr.

V1 SCAFFOLDING — no tools registered yet. The OpenClaw
primitive-automation factory registers tools here as it builds each
catalogued primitive (see ``Macro_Copilot/automation/primitive_automation/
primitive_catalog.yaml`` and the ``REPO_REFERENCE_MAP.md`` for the
mirror pattern from ``rates_agent/ois/mcp_server.py``).

Tool surface (planned, per the primitive catalog)
-------------------------------------------------
1. ``get_futures_price_level_tool``           — front-contract price +
   implied rate (= 100 − price) + Δ + 252d z.
2. ``build_futures_strip_snapshot_tool``      — all 8 strip positions
   side-by-side (price + implied rate + Δ + z + OI).
3. ``get_volume_open_interest_snapshot_tool`` — daily volume / OI /
   Δ-OI / OI z-score across the strip.
4. ``calculate_futures_calendar_spread_tool`` — same-curve implied-rate
   spread between two strip positions (e.g. SFR2 − SFR1).
5. ``calculate_futures_butterfly_simple_tool`` — three-point implied-rate
   butterfly, fixed 50-50 weighting.
6. ``calculate_futures_cross_market_spread_tool`` — matched-strip
   cross-CB implied-rate differential, with the benchmark-family
   mismatch caveat on the methodology card.
7. ``calculate_futures_pack_average_simple_tool`` — average implied
   rate across whites (positions 1-4) / reds (5-8). Ships with a
   ``PR11`` ``NotImplementedError`` refusal for
   ``curve_family = EUR_SHORT_RATE_FUT`` until ``policy_futures.yml``
   annotates ``delivery_month_type: quarterly | serial`` (Euribor strip
   mixes serial and quarterly contracts).
8. ``scan_policy_futures_extremes_tool`` — morning STIR sweep across
   the universe by absolute z-score.

Each tool description tells the LLM:
  (a) what the tool does
  (b) which user questions it should handle
  (c) which policy-futures curve_family values are valid
  (d) explicit "do NOT use for X" fences so the LLM doesn't route
      sovereign / OIS / bond-futures questions here

Conventions for this domain
---------------------------
- Quoted in PRICE; the desk-recognised read is the IMPLIED RATE
  (= 100 − price).
- Strip-position-keyed (``SFR1`` = front; ``SFR2..SFR8`` = quarterly
  forwards). NOT tenor-keyed — uses the strip-aware fetch helper in
  ``shared/analytics/rates_fetch.py``.
- SOFR / SONIA reference a compounded RFR (3-month look-back);
  Euribor (``EUR_SHORT_RATE_FUT``) references unsecured 3M term-Euribor
  — a structurally different rate object. Cross-CB spreads ALWAYS
  ship with the benchmark-family-mismatch disclosure on the methodology
  card (P5).
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
logger = logging.getLogger("rates_agent.policy_futures.mcp_server")

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
    name="policy-futures-agent",
    instructions=(
        "You are the Policy Futures (STIR strip) Sub-Agent for a macro "
        "hedge-fund desk. You have access to tools that perform "
        "deterministic calculations over a TimescaleDB database of "
        "daily SOFR / Euribor / SONIA short-term-interest-rate futures "
        "price + volume + open-interest quotes. The strip is "
        "strip-position-keyed (SFR1 = front; SFR2..SFR8 = quarterly "
        "forwards) and quoted in PRICE; the desk-recognised read is "
        "the IMPLIED RATE = 100 − price. Use these tools to answer "
        "questions about the STIR strip's implied-rate levels, calendar "
        "spreads, simple butterflies, pack averages (whites / reds), "
        "cross-CB STIR spreads, volume / open-interest, and morning "
        "extreme scans. Never attempt the math yourself — always call "
        "a tool and relay its output. Do NOT route cash sovereign "
        "bonds, OIS swaps, or bond futures here — those belong to the "
        "sovereign_bonds, ois, and bond_futures agents respectively. "
        "When relaying STIR output, ALWAYS use the phrase 'implied "
        "rate' (NOT 'yield' and NOT 'par rate'); on cross-CB spreads, "
        "ALWAYS preserve the benchmark-family-mismatch disclosure "
        "(SOFR / SONIA = compounded RFR; Euribor = unsecured 3M term-"
        "Euribor — structurally different rate objects). The "
        "futures_pack_average_simple tool refuses EUR_SHORT_RATE_FUT "
        "with a controlled error envelope until the playbook annotates "
        "delivery_month_type (Euribor strip mixes serial and quarterly "
        "contracts); SOFR + SONIA pack averages build cleanly."
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
#      ``rates_agent.policy_futures.tools.<tool_name>``.
#   2. Add a ``@mcp.tool()`` wrapper that validates input via the
#      Pydantic schema, loads the bundled config via
#      ``load_tool_config(CONFIG_PATH)``, calls the compute function
#      with ``config=`` passed explicitly, and returns the JSON
#      response with LLM-non-friendly fields (full ``time_series``,
#      ``panel`` payloads, underscore-prefixed internals) stripped.
#   3. Add a corresponding entry to
#      ``rates_agent/policy_futures/tools/schemas/__init__.py`` so the
#      schemas hub stays a stable import surface.


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Policy Futures Sub-Agent MCP server (stdio transport)..."
    )
    mcp.run(transport="stdio")

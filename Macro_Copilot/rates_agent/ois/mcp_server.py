"""
mcp_server.py — MCP Server for the OIS Sub-Agent
=================================================

Second sub-agent in the Rates domain.  Mirrors the sovereign_bonds
server structure but exposes OIS-specific tools with OIS-appropriate
parameter descriptions and defaults (e.g. ``PX_LAST`` rather than
``YLD_YTM_MID``).

Design: flat scalar parameters on the tool function, Pydantic validation
inside.  Lazy engine singleton.  stdio transport.  Logging to stderr only.
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
from rates_agent.ois.tools.schemas import OISCurveSpreadInput  # noqa: E402
from rates_agent.ois.tools.curve_spread import calculate_ois_curve_spread  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.ois.mcp_server")

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
    name="ois-agent",
    instructions=(
        "You are the OIS Sub-Agent for a macro hedge-fund desk.  You have "
        "access to tools that perform deterministic calculations over a "
        "TimescaleDB database of daily OIS (Overnight Index Swap) par-rate "
        "quotes.  Use these tools to answer questions about OIS curve "
        "spreads, slope, and relative-value z-scores on SOFR, ESTR, "
        "SONIA, TONA, AONIA, and CORRA curves.  Never attempt the math "
        "yourself — always call a tool and relay its output."
    ),
)


# ===========================================================================
# TOOL 1: calculate_ois_curve_spread
# ===========================================================================
@mcp.tool()
def calculate_ois_curve_spread_tool(
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "PX_LAST",
) -> str:
    """Calculate the basis-point spread between two tenor points on the
    SAME OIS curve (e.g. SOFR 2s10s, ESTR 1s5s, SONIA 5s30s), plus its
    1-year rolling z-score.

    Use this tool when the user asks about:
    - OIS curve steepness / flatness  (e.g. "How steep is the SOFR 2s10s?")
    - Swap-curve spread levels         (e.g. "Where is ESTR 1s5s trading?")
    - Policy-curve RV signals          (e.g. "Is SONIA 2s10s rich or cheap?")

    Do NOT use this tool for:
    - Sovereign bond curve spreads (e.g. UST 2s10s, Bund 5s30s)
      → use calculate_curve_spread_tool instead.
    - Cross-currency OIS comparisons (e.g. SOFR vs ESTR 2Y)
      → the cross-market OIS tool will handle that (not yet available).

    Parameters
    ----------
    curve_family : str
        OIS curve identifier exactly as stored in instrument_master.
        Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', 'GBP_SONIA_OIS',
        'JPY_OIS', 'AUD_OIS', 'CAD_OIS'.
    short_tenor : str
        The short leg.  OIS curves have fine short-end granularity:
        '1W', '1M', '2M', '3M', '6M', '9M', '1Y', '2Y', '3Y'.
    long_tenor : str
        The long leg, e.g. '2Y', '5Y', '10Y', '20Y', '30Y'.
        Must be different from short_tenor.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Observation field (default 'PX_LAST' = mid par swap rate).
        Other valid values: 'PX_BID', 'PX_ASK'.
    """
    try:
        params = OISCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps({"error": f"Database connection failed: {exc}"}, default=str)

    try:
        result = calculate_ois_curve_spread(engine=engine, params=params)
    except Exception as exc:
        logger.exception(
            "Unhandled error in calculate_ois_curve_spread for %s %s/%s",
            params.curve_family, params.short_tenor, params.long_tenor,
        )
        return json.dumps(
            {
                "error": (
                    f"Calculation failed for OIS {params.curve_family} "
                    f"{params.short_tenor}/{params.long_tenor}: {exc}"
                )
            },
            default=str,
        )

    logger.info(
        "Tool call complete: OIS %s %s/%s → %s",
        params.curve_family, params.short_tenor, params.long_tenor,
        "error" if "error" in result else "OK",
    )

    if "error" in result:
        return json.dumps(result, default=str)

    llm_response = {"current_metrics": result.get("current_metrics", {})}
    ts_rows = len(result.get("time_series", []))
    if ts_rows:
        logger.info(
            "Withheld %d time_series rows from LLM context (frontend-only data).",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info("Starting OIS Sub-Agent MCP server (stdio transport)...")
    mcp.run(transport="stdio")

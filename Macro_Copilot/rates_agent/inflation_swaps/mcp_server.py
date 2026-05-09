"""
mcp_server.py — MCP Server for the Inflation-Swaps Sub-Agent
==============================================================

Exposes the inflation-swap-domain tools to the orchestrator via stdio
MCP.  Each tool has flat scalar parameters; Pydantic validation
happens inside.  Lazy engine singleton.  Logging to stderr.

Tool surface
------------
1. calculate_inflation_swap_rate_level_tool — single-pillar
   zero-coupon inflation swap (ZCIS) rate level, period changes,
   252-day z-score, trailing 1Y high/low/percentile, plus the
   load-bearing reference metadata
   (``inflation_index_family`` / ``index_lag`` / ``interpolation`` /
   ``underlying_index``) for honest cross-curve interpretation.

Subsequent inflation-swap primitives (curve spread, forward, cross-
market spread, swap-breakeven basis) will land here as separate
primitive builds.

Each tool description tells the LLM:
  (a) what the tool does
  (b) which user questions it should handle
  (c) which inflation-swap curve_family values are valid
  (d) explicit "do NOT use for X" fences so the LLM doesn't route
      linker-bond, nominal-sovereign, or OIS questions here
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
from rates_agent.inflation_swaps.tools.schemas import (  # noqa: E402
    InflationSwapRateLevelInput,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (  # noqa: E402
    CONFIG_PATH as INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH,
    calculate_inflation_swap_rate_level,
)
from shared.config import load_tool_config  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.inflation_swaps.mcp_server")

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
    name="inflation-swaps-agent",
    instructions=(
        "You are the Inflation-Swaps Sub-Agent for a macro hedge-fund "
        "desk.  You have access to tools that perform deterministic "
        "calculations over a TimescaleDB database of daily zero-coupon "
        "inflation swap (ZCIS) quotes for USD_ZCIS (CPI-U), EUR_ZCIS "
        "(HICP ex-tobacco), and GBP_ZCIS (RPI).  Use these tools to "
        "answer questions about ZCIS rate levels, ZCIS curve spreads, "
        "forwards, and cross-market spreads (when those primitives "
        "ship).  Never attempt the math yourself — always call a tool "
        "and relay its output.  Do not route linker-bond breakeven, "
        "nominal sovereign yield, or OIS rate questions here — those "
        "belong to the inflation_indexed_bonds, sovereign_bonds, and "
        "ois agents respectively.  When relaying ZCIS output, always "
        "preserve the methodology disclosure and the reference "
        "metadata: ZCIS rates are the par-rate the swap pays for "
        "inflation compensation against the headline index over the "
        "swap tenor; the index family / lag / interpolation differ "
        "across USD_ZCIS / EUR_ZCIS / GBP_ZCIS, so cross-curve "
        "comparisons are NOT pure expected-inflation differentials."
    ),
)


# ===========================================================================
# TOOL 1: calculate_inflation_swap_rate_level
# ===========================================================================
@mcp.tool()
def calculate_inflation_swap_rate_level_tool(
    curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current zero-coupon inflation swap (ZCIS) rate level
    for a single point on a ZCIS curve, plus period changes, 1-year
    z-score, and deterministic historical context (high, low,
    percentile).  Returns the load-bearing reference metadata
    (``inflation_index_family``, ``index_lag``, ``interpolation``,
    ``underlying_index``) on the wire so the desk can interpret the
    level honestly.

    Use this tool when the user asks about:
    - ZCIS rate levels             (e.g. "Where's USD 5Y ZCIS?")
    - ZCIS rate moves              (e.g. "How much has GBP 10Y ZCIS
      moved this week?")
    - ZCIS rate extremes           (e.g. "Is EUR 10Y ZCIS at a 1-year
      low?")
    - ZCIS-implied inflation-compensation reads (the swap-side
      companion to bond-implied breakeven; reports the traded par
      ZCIS rate, NOT an expected-inflation surface).

    Do NOT use this tool for:
    - Sovereign-linker bond-implied breakevens — call the
      inflation_indexed_bonds agent's
      ``calculate_breakeven_inflation_simple_tool`` (linker bond
      vs nominal sovereign) instead.
    - Nominal sovereign bond yields — call the sovereign_bonds
      agent's ``get_yield_levels_tool``.
    - OIS swap rates — call the ois agent's
      ``get_ois_rate_level_tool``.
    - Linker real yields — call the inflation_indexed_bonds agent's
      ``get_real_yield_level_tool``.

    Parameters
    ----------
    curve_family : str
        Inflation-swap curve identifier exactly as stored in
        instrument_master.  Examples: 'USD_ZCIS' (US CPI-U
        zero-coupon inflation swaps), 'EUR_ZCIS' (euro-area HICP
        ex-tobacco ZCIS), 'GBP_ZCIS' (UK RPI ZCIS).  See
        ``rates_agent/playbooks/inflation_swaps.yml`` for the
        ingested universe.
    tenor : str
        Tenor point on the ZCIS curve.  Available tenors are
        curve-family specific; the current ingested grid is
        1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y on each of
        USD_ZCIS / EUR_ZCIS / GBP_ZCIS.
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365).
    field_name : str, optional
        Bloomberg field mnemonic for the ZCIS rate.  Leave as the
        default empty string ""  to use the bundled
        ``default_zcis_rate_field`` convention from
        inflation_swap_rate_level/config.yaml (currently 'PX_MID' —
        the canonical mid quoted ZCIS rate Bloomberg publishes for
        inflation swaps).  Pass an explicit field name to override
        per call.  Mirrors the empty-string sentinel pattern used by
        sovereign / OIS / linker tools so the YAML default actually
        flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's ``default_field_name``.
    # Same shadowing pattern fixed for sovereign curve_move_classifier
    # in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = InflationSwapRateLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_inflation_swap_rate_level_tool] "
            "input validation failed: %s",
            exc,
        )
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception(
            "[calculate_inflation_swap_rate_level_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the inflation_swap_rate_level tool's bundled config
    # explicitly so the dependency is observable here.
    # load_tool_config caches by path, so this is a free lookup
    # after the first call within the MCP subprocess's lifetime.
    # Mirrors sibling level-tool wrappers exactly.
    try:
        isrl_config = load_tool_config(
            INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH,
        )
        result = calculate_inflation_swap_rate_level(
            engine=engine, params=params, config=isrl_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_inflation_swap_rate_level_tool] unhandled "
            "error for %s %s",
            params.curve_family, params.tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_inflation_swap_rate_level_tool failed "
                    f"for {params.curve_family} {params.tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_inflation_swap_rate_level_tool] tool call "
        "complete: %s %s → %s",
        params.curve_family, params.tenor, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST
    # path returns the full payload).  Same convention as sibling
    # rate-level tools — the LLM doesn't need every historical row to
    # answer "where's USD 5Y ZCIS?".
    llm_response: dict = {
        k: v for k, v in result.items() if k != "time_series"
    }
    ts_rows = len(result.get("time_series", {}).get("rows", []) or [])
    if ts_rows:
        logger.info(
            "[calculate_inflation_swap_rate_level_tool] withheld "
            "%d time_series rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Inflation-Swaps Agent MCP server (stdio transport)...",
    )
    mcp.run(transport="stdio")

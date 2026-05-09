"""
mcp_server.py — MCP Server for the Inflation-Indexed-Bonds Sub-Agent
=====================================================================

Exposes the linker-domain tools to the orchestrator via stdio MCP.
Each tool has flat scalar parameters; Pydantic validation happens
inside.  Lazy engine singleton.  Logging to stderr.

Tool surface (build_order=4 milestone)
--------------------------------------
1. get_real_yield_level_tool — single-point linker real-yield level,
   period changes, 252-day z-score, trailing 1Y high/low/percentile.

This is the FIRST tool in the inflation_indexed_bonds domain.
Subsequent linker primitives (breakeven_inflation_simple,
forward_breakeven_simple, real_yield_curve_spread, scanner_linkers,
etc., per
``manifesto/01_instruments/rates_agent/04_inflation_indexed_bonds.md``
Section 9 "Bucket 1A") will land here as separate primitive builds.

Each tool description tells the LLM:
  (a) what the tool does
  (b) which user questions it should handle
  (c) which linker curve_family values are valid
  (d) explicit "do NOT use for X" fences so the LLM doesn't route
      nominal sovereign questions here
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
from rates_agent.inflation_indexed_bonds.tools.schemas import (  # noqa: E402
    RealYieldLevelInput,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (  # noqa: E402
    CONFIG_PATH as REAL_YIELD_LEVEL_CONFIG_PATH,
    get_real_yield_level,
)
from shared.config import load_tool_config  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.inflation_indexed_bonds.mcp_server")

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
    name="inflation-indexed-bonds-agent",
    instructions=(
        "You are the Inflation-Indexed-Bonds Sub-Agent for a macro "
        "hedge-fund desk.  You have access to tools that perform "
        "deterministic calculations over a TimescaleDB database of "
        "daily sovereign-linker (TIPS / inflation-linked Gilts / OATi-"
        "OATei / Canadian RRB) real-yield quotes.  Use these tools to "
        "answer questions about real-yield levels and (in future "
        "releases of this agent) breakeven inflation, real-yield curve "
        "spreads, and inflation-compensation extremes.  Never attempt "
        "the math yourself — always call a tool and relay its output.  "
        "Do not route nominal sovereign yield questions here — those "
        "belong to the sovereign-bond agent's get_yield_levels_tool."
    ),
)


# ===========================================================================
# TOOL 1: get_real_yield_level
# ===========================================================================
@mcp.tool()
def get_real_yield_level_tool(
    curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current real-yield level for a single point on a
    sovereign-linker curve, plus period changes, 1-year z-score, and
    deterministic historical context (high, low, percentile).

    Use this tool when the user asks about:
    - Real-yield levels         (e.g. "Where's TIPS 10Y real yield?")
    - Real-yield moves           (e.g. "How much has UK 10Y real yield
      moved this week?")
    - Real-yield extremes         (e.g. "Is OATei 10Y real yield at a
      1-year low?")
    - Decomposing a breakeven move (call this for the linker leg, then
      the sovereign-bond agent for the nominal leg)

    Do NOT use this tool for:
    - Nominal sovereign bond yields (UST, Bund, Gilt, etc.) — use the
      sovereign-bond agent's get_yield_levels_tool instead.
    - Breakevens or inflation swaps (those are separate primitives that
      will land in subsequent builds in this domain).

    Parameters
    ----------
    curve_family : str
        Linker curve identifier exactly as stored in instrument_master.
        Examples: 'USD_TIPS', 'GBP_LINKER', 'EUR_FR_LINKER', 'CAD_RRB'
        (see ``rates_agent/playbooks/inflation_indexed_bonds.yml`` for
        the ingested universe).
    tenor : str
        Tenor point on the linker curve.  Available tenors are
        curve-family specific: USD_TIPS exposes 5Y/10Y/20Y/30Y;
        GBP_LINKER exposes 1Y..50Y; EUR_FR_LINKER exposes
        2Y/5Y/7Y/10Y/15Y; CAD_RRB exposes 5Y..30Y.
    lookback_days : int, optional
        Calendar days of history for the observation_count window
        (default 365).
    field_name : str, optional
        Bloomberg field mnemonic.  Leave as the default empty string
        ""  to use the bundled ``default_field_name`` convention from
        real_yield_level/config.yaml (currently 'YLD_YTM_MID' — the
        linker real-yield-to-maturity mnemonic).  Pass an explicit
        field name to override per call.  Mirrors the empty-string
        sentinel pattern used by sovereign get_yield_levels_tool /
        curve_move_classifier so the YAML default actually flows
        through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's ``default_field_name``.
    # Without this, the LLM omitting field_name would always hit a
    # hardcoded default regardless of what the YAML says — same
    # shadowing pattern fixed for sovereign curve_move_classifier in
    # commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = RealYieldLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[get_real_yield_level_tool] input validation failed: %s", exc,
        )
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception(
            "[get_real_yield_level_tool] failed to connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the real_yield_level tool's bundled config explicitly so
    # the dependency is observable here.  load_tool_config caches by
    # path, so this is a free lookup after the first call within the
    # MCP subprocess's lifetime.  Mirrors sovereign / OIS level-tool
    # wrappers exactly.
    try:
        ryl_config = load_tool_config(REAL_YIELD_LEVEL_CONFIG_PATH)
        result = get_real_yield_level(
            engine=engine, params=params, config=ryl_config,
        )
    except Exception as exc:
        logger.exception(
            "[get_real_yield_level_tool] unhandled error for %s %s",
            params.curve_family, params.tenor,
        )
        return json.dumps(
            {"error": f"get_real_yield_level_tool failed for "
             f"{params.curve_family} {params.tenor}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[get_real_yield_level_tool] tool call complete: %s %s → %s",
        params.curve_family, params.tenor, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST
    # path returns the full payload).  Same convention as sovereign
    # get_yield_levels_tool / OIS calculate_ois_rate_level_tool — the
    # LLM doesn't need every historical row to answer "where's TIPS 10Y
    # real yield?".
    llm_response: dict = {
        k: v for k, v in result.items() if k != "time_series"
    }
    ts_rows = len(result.get("time_series", {}).get("rows", []) or [])
    if ts_rows:
        logger.info(
            "[get_real_yield_level_tool] withheld %d time_series rows "
            "from LLM context.", ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Inflation-Indexed-Bonds Agent MCP server (stdio transport)...",
    )
    mcp.run(transport="stdio")

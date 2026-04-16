"""
mcp_server.py — MCP Server for the Rates Agent
================================================

This is the "Bridge" layer in our Iron Triangle architecture.  It stands
between the LLM orchestrator (LangGraph) and the deterministic Python math
tools.  The server exposes each tool as a callable MCP endpoint with a
machine-readable JSON schema so the LLM knows *exactly* what parameters to
extract from the user — and can never deviate.

Design decisions
----------------
1.  **Flat scalar parameters on the tool function, Pydantic validation
    inside.**  MCP generates the tool's JSON schema by inspecting the
    function signature.  If we accepted a single ``CurveSpreadInput``
    object the LLM would see one opaque ``object`` parameter and lose
    per-field descriptions.  Instead we mirror every field as a top-level
    keyword argument (with identical names, types, defaults, and
    descriptions), then construct and validate ``CurveSpreadInput``
    internally.  The LLM sees a clean, flat schema; our code still gets
    full Pydantic validation and the ``short_tenor != long_tenor`` guard.

2.  **Lazy engine singleton.**  ``get_db_engine()`` creates a SQLAlchemy
    connection-pool.  We defer that cost until the first actual tool call
    rather than at import time, and reuse the same pool for the lifetime
    of the server process.  This matches how ``ingest_parquet.py`` and
    ``test_tool_direct.py`` already use the engine in our codebase.

3.  **stdio transport.**  The orchestrator will start this server as a
    subprocess and communicate over stdin/stdout.  This is the simplest,
    most secure local transport — no open network ports, no auth tokens.
    When we later move to a remote deployment we can switch to SSE or
    Streamable HTTP with a one-line change.

4.  **Logging to stderr only.**  stdout is the MCP transport channel.
    Anything printed to stdout that isn't a valid JSON-RPC message will
    corrupt the protocol.  All diagnostic output goes to stderr via the
    ``logging`` module.

5.  **Tool returns a JSON string.**  MCP tool responses are text content
    delivered to the LLM.  We ``json.dumps`` the structured dict coming
    out of ``calculate_curve_spread`` so the orchestrator receives
    parseable JSON every time — including on error paths.

Running the server
------------------
From the project root (or inside the ``rates-agent-dev`` container)::

    python -m rates_agent.mcp_server

The process will block on stdin, waiting for JSON-RPC messages from an
MCP client (e.g. the LangGraph orchestrator, Claude Desktop, or the MCP
Inspector for manual testing).

Testing with the MCP Inspector::

    npx @modelcontextprotocol/inspector python -m rates_agent.mcp_server
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Path setup — ensure project root is importable regardless of cwd.
# This mirrors the pattern used in tests/test_tool_direct.py.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from rates_agent.tools.schemas import CurveSpreadInput, YieldLevelInput  # noqa: E402
from rates_agent.tools.curve_spread import calculate_curve_spread  # noqa: E402
from rates_agent.tools.yield_levels import get_yield_levels  # noqa: E402

# ---------------------------------------------------------------------------
# Logging — stderr only (stdout is the MCP transport)
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.mcp_server")

# ---------------------------------------------------------------------------
# Database engine — lazy singleton
# ---------------------------------------------------------------------------
_engine = None


def _get_engine():
    """Return (and cache) a SQLAlchemy engine using env-var config."""
    global _engine
    if _engine is None:
        logger.info("Initializing database engine...")
        _engine = get_db_engine()
        logger.info("Database engine ready.")
    return _engine


# ===========================================================================
# MCP SERVER INSTANCE
# ===========================================================================
mcp = FastMCP(
    name="rates-agent",
    instructions=(
        "You are the Rates Agent for a macro hedge-fund desk.  You have "
        "access to tools that perform deterministic calculations over a "
        "TimescaleDB database of daily sovereign-bond yields.  Use the "
        "tools to answer questions about yield-curve spreads, slope, and "
        "relative-value z-scores.  Never attempt to do the math yourself "
        "— always call a tool and relay its output to the user."
    ),
)


# ===========================================================================
# TOOL: calculate_curve_spread
# ===========================================================================
@mcp.tool()
def calculate_curve_spread_tool(
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "YLD_YTM_MID",
) -> str:
    """Calculate the basis-point spread between two tenor points on a
    sovereign yield curve, plus its 1-year rolling z-score.

    Use this tool when the user asks about:
    - Curve steepness / flatness  (e.g. "How steep is the UST 2s10s?")
    - Spread levels or moves      (e.g. "What did Bund 5s30s do today?")
    - Relative-value signals       (e.g. "Is the Gilt 2s10s rich or cheap
      vs its 1-year history?")

    Parameters
    ----------
    curve_family : str
        Curve identifier exactly as stored in instrument_master.
        Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT',
        'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'.
    short_tenor : str
        The short leg, e.g. '1Y', '2Y', '3Y', '5Y'.
    long_tenor : str
        The long leg, e.g. '5Y', '7Y', '10Y', '20Y', '30Y'.
        Must be different from short_tenor.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
        The z-score always uses a fixed 252-trading-day rolling window
        regardless of this value.
    field_name : str, optional
        The Bloomberg field mnemonic stored in market_data_daily
        (default 'YLD_YTM_MID').  Change only if querying a non-standard
        observation field.

    Returns
    -------
    str
        JSON object with a ``current_metrics`` key containing: latest
        spread (bps), daily change, z-score, and individual leg yields.
        On failure, returns a JSON object with an ``error`` key
        containing a human-readable explanation.
    """
    # ------------------------------------------------------------------
    # 1. Validate inputs via our Pydantic schema
    # ------------------------------------------------------------------
    try:
        params = CurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    # ------------------------------------------------------------------
    # 2. Acquire the database engine
    # ------------------------------------------------------------------
    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # ------------------------------------------------------------------
    # 3. Execute the deterministic math tool
    # ------------------------------------------------------------------
    try:
        result = calculate_curve_spread(engine=engine, params=params)
    except Exception as exc:
        logger.exception(
            "Unhandled error in calculate_curve_spread for %s %s/%s",
            params.curve_family,
            params.short_tenor,
            params.long_tenor,
        )
        return json.dumps(
            {
                "error": (
                    f"Calculation failed for {params.curve_family} "
                    f"{params.short_tenor}/{params.long_tenor}: {exc}"
                )
            },
            default=str,
        )

    # ------------------------------------------------------------------
    # 4. Separate LLM response from frontend data
    # ------------------------------------------------------------------
    # Design principle: the LLM is a narrator, not an analyst.  It
    # receives ONLY pre-computed metrics (current_metrics) and writes
    # a narrative from those numbers.  The raw time_series is computed
    # by curve_spread.py (needed internally for the z-score), but is
    # NOT sent to the LLM — it exists for the future charting frontend.
    #
    # This prevents the LLM from eyeballing 365 data points and
    # hallucinating trend conclusions that aren't deterministically
    # computed.  If we want trend analysis ("the curve has been
    # flattening"), we build a separate deterministic tool for that.

    logger.info(
        "Tool call complete: %s %s/%s → %s",
        params.curve_family,
        params.short_tenor,
        params.long_tenor,
        "error" if "error" in result else "OK",
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series — only current_metrics goes to the LLM.
    llm_response = {"current_metrics": result.get("current_metrics", {})}

    ts_rows = len(result.get("time_series", []))
    if ts_rows:
        logger.info(
            "Withheld %d time_series rows from LLM context (frontend-only data).",
            ts_rows,
        )

    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL: get_yield_levels
# ===========================================================================
@mcp.tool()
def get_yield_levels_tool(
    curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "YLD_YTM_MID",
) -> str:
    """Get the current yield level for a single point on a sovereign curve,
    plus period changes, z-score, and deterministic historical context.

    Use this tool when the user asks about:
    - Absolute yield levels    (e.g. "Where's the US 10Y?")
    - Yield moves over time    (e.g. "How much have 2Y Gilts sold off?")
    - Yield extremes           (e.g. "Is JGB 10Y at a 1-year high?")
    - Decomposing a spread move (call this for each leg individually)

    Parameters
    ----------
    curve_family : str
        Curve identifier exactly as stored in instrument_master.
        Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT',
        'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'.
    tenor : str
        The tenor point, e.g. '1Y', '2Y', '3Y', '5Y', '7Y', '10Y',
        '20Y', '30Y'.
    lookback_days : int, optional
        Calendar days of history for calculations (default 365).
        The z-score always uses a fixed 252-trading-day rolling window.
    field_name : str, optional
        The Bloomberg field mnemonic stored in market_data_daily
        (default 'YLD_YTM_MID').

    Returns
    -------
    str
        JSON object with a ``current_metrics`` key containing: current
        yield (%), daily/weekly/monthly change (bps), z-score, 252-day
        high/low (%), and percentile rank.
        On failure, returns a JSON object with an ``error`` key.
    """
    # ------------------------------------------------------------------
    # 1. Validate inputs via Pydantic schema
    # ------------------------------------------------------------------
    try:
        params = YieldLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    # ------------------------------------------------------------------
    # 2. Acquire the database engine
    # ------------------------------------------------------------------
    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # ------------------------------------------------------------------
    # 3. Execute the deterministic math tool
    # ------------------------------------------------------------------
    try:
        result = get_yield_levels(engine=engine, params=params)
    except Exception as exc:
        logger.exception(
            "Unhandled error in get_yield_levels for %s %s",
            params.curve_family,
            params.tenor,
        )
        return json.dumps(
            {
                "error": (
                    f"Calculation failed for {params.curve_family} "
                    f"{params.tenor}: {exc}"
                )
            },
            default=str,
        )

    # ------------------------------------------------------------------
    # 4. Return only current_metrics to the LLM
    # ------------------------------------------------------------------
    logger.info(
        "Tool call complete: %s %s → %s",
        params.curve_family,
        params.tenor,
        "error" if "error" in result else "OK",
    )

    if "error" in result:
        return json.dumps(result, default=str)

    return json.dumps({"current_metrics": result.get("current_metrics", {})}, default=str)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info("Starting Rates Agent MCP server (stdio transport)...")
    mcp.run(transport="stdio")

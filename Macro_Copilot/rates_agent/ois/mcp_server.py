"""
mcp_server.py — MCP Server for the OIS Sub-Agent
=================================================

Exposes the OIS-domain tools to the orchestrator via stdio MCP.  Each
tool has flat scalar parameters; Pydantic validation happens inside.
Lazy engine singleton.  Logging to stderr.

Tool surface
------------
1. calculate_ois_rate_level_tool           — SOFR 2Y right now, z-score, range
2. calculate_ois_curve_spread_tool         — SOFR 2s10s, ESTR 1s5s, etc.
3. calculate_ois_forward_rate_tool         — 1Y1Y, 5Y5Y, or date-window forwards
4. calculate_ois_meeting_pricing_tool      — FOMC/ECB/... meeting-by-meeting pricing
5. calculate_ois_cross_market_spread_tool  — SOFR-ESTR, ESTR-SONIA, etc.
6. scan_ois_extremes_tool                  — z-score screener across OIS universe

Each tool description tells the LLM:
  (a) what the tool does
  (b) which user questions it should handle
  (c) which OIS curve_family values are valid
  (d) explicit "do NOT use for X" fences so the LLM doesn't route
      sovereign questions here

The supervisor routes OIS queries to this child by MCP server
(not by tool name), but tool-name prefixing still matters for
unambiguous tool selection inside the child's agent loop.
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
from rates_agent.ois.tools.schemas import (  # noqa: E402
    OISCrossMarketSpreadInput,
    OISCurveSpreadInput,
    OISForwardRateInput,
    OISMeetingPricingInput,
    OISRateLevelInput,
    OISScannerInput,
)
from rates_agent.ois.tools.cross_market_spread import (  # noqa: E402
    calculate_ois_cross_market_spread,
)
from rates_agent.ois.tools.curve_spread import calculate_ois_curve_spread  # noqa: E402
from rates_agent.ois.tools.forward_rate import calculate_ois_forward_rate  # noqa: E402
from rates_agent.ois.tools.meeting_pricing import (  # noqa: E402
    calculate_ois_meeting_pricing,
)
from rates_agent.ois.tools.rate_level import get_ois_rate_level  # noqa: E402
from rates_agent.ois.tools.scanner import scan_ois_extremes  # noqa: E402

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
        "You are the OIS Sub-Agent for a macro hedge-fund desk.  You "
        "have access to tools that perform deterministic calculations "
        "over a TimescaleDB database of daily OIS (Overnight Index "
        "Swap) par-rate quotes.  Use these tools to answer questions "
        "about OIS rate levels, curve spreads, forward rates, "
        "central-bank meeting pricing, cross-currency OIS spreads, "
        "and z-score extremes on SOFR, ESTR, SONIA, TONA, AONIA, and "
        "CORRA curves.  Never attempt the math yourself — always call "
        "a tool and relay its output."
    ),
)


# ---------------------------------------------------------------------------
# SHARED ERROR-HANDLING WRAPPER
# ---------------------------------------------------------------------------
# Every tool below follows the identical try/except cascade:
#   1. Validate input via Pydantic (ValidationError → 422-shaped error)
#   2. Get engine (connection error → 503-shaped error)
#   3. Call tool function (unhandled exception → 500-shaped error)
#   4. If result has "error" key, pass through; else strip time_series
#      before returning to the LLM (frontend receives full output via
#      REST endpoints — separate code path).

def _run_tool(
    tool_name: str,
    schema_cls,
    tool_fn,
    kwargs: dict,
    *,
    strip_time_series: bool = True,
) -> str:
    """Uniform input-validation + engine-acquisition + exception handling."""
    try:
        params = schema_cls(**kwargs)
    except ValidationError as exc:
        logger.warning("[%s] input validation failed: %s", tool_name, exc)
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("[%s] failed to connect to TimescaleDB", tool_name)
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    try:
        result = tool_fn(engine, params)
    except Exception as exc:
        logger.exception("[%s] unhandled error", tool_name)
        return json.dumps(
            {"error": f"{tool_name} failed: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info("[%s] tool call complete → %s", tool_name, status)

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST path
    # returns the full payload).  Some tools don't emit time_series
    # (rate_level, meeting_pricing, scanner) — the strip is a no-op there.
    if strip_time_series:
        llm_response: dict = {
            k: v for k, v in result.items() if k != "time_series"
        }
        ts_rows = len(result.get("time_series", []) or [])
        if ts_rows:
            logger.info(
                "[%s] withheld %d time_series rows from LLM context.",
                tool_name, ts_rows,
            )
    else:
        llm_response = result

    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 1: calculate_ois_rate_level
# ===========================================================================
@mcp.tool()
def calculate_ois_rate_level_tool(
    curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "PX_LAST",
) -> str:
    """Get the current par swap rate level for a single point on an OIS
    curve, plus period changes, 1-year z-score, and deterministic
    historical context (high, low, percentile).

    Use this tool when the user asks about:
    - Absolute OIS rate levels    (e.g. "Where's SOFR 2Y?")
    - OIS rate moves over time    (e.g. "How much has ESTR 1Y moved this week?")
    - OIS rate extremes           (e.g. "Is SONIA 5Y at a 1-year high?")
    - Decomposing an OIS spread   (call this per leg individually)

    Do NOT use this tool for:
    - Sovereign bond yields (UST, Bund, Gilt, JGB, etc.)
      → use get_yield_levels_tool instead.

    Parameters
    ----------
    curve_family : str
        OIS curve identifier exactly as stored in instrument_master.
        Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', 'GBP_SONIA_OIS',
        'JPY_OIS', 'AUD_OIS', 'CAD_OIS'.
    tenor : str
        Tenor point.  OIS curves have fine short-end granularity:
        '1W', '1M', '2M', '3M', '6M', '9M', '1Y', '2Y', '3Y', '5Y',
        '10Y', '20Y', '30Y'.
    lookback_days : int, optional
        Calendar days of history for observation counting (default 365).
    field_name : str, optional
        Observation field (default 'PX_LAST' = mid par swap rate).
    """
    return _run_tool(
        tool_name="calculate_ois_rate_level_tool",
        schema_cls=OISRateLevelInput,
        tool_fn=get_ois_rate_level,
        kwargs=dict(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )


# ===========================================================================
# TOOL 2: calculate_ois_curve_spread
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
    - OIS curve steepness / flatness  (e.g. "How steep is SOFR 2s10s?")
    - Swap-curve spread levels         (e.g. "Where is ESTR 1s5s trading?")
    - Policy-curve RV signals          (e.g. "Is SONIA 2s10s rich or cheap?")

    Do NOT use this tool for:
    - Sovereign bond curve spreads (UST 2s10s, Bund 5s30s)
      → use calculate_curve_spread_tool instead.
    - Cross-currency OIS comparisons (SOFR vs ESTR)
      → use calculate_ois_cross_market_spread_tool.

    Parameters
    ----------
    curve_family : str
        OIS curve identifier.  Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS',
        'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', 'CAD_OIS'.
    short_tenor : str
        Short leg.  Examples: '1W', '1M', '3M', '6M', '1Y', '2Y'.
    long_tenor : str
        Long leg.  Examples: '2Y', '5Y', '10Y', '20Y', '30Y'.
        Must differ from short_tenor.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Observation field (default 'PX_LAST').
    """
    return _run_tool(
        tool_name="calculate_ois_curve_spread_tool",
        schema_cls=OISCurveSpreadInput,
        tool_fn=calculate_ois_curve_spread,
        kwargs=dict(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )


# ===========================================================================
# TOOL 3: calculate_ois_forward_rate
# ===========================================================================
@mcp.tool()
def calculate_ois_forward_rate_tool(
    curve_family: str,
    start_tenor: str = "",
    end_tenor: str = "",
    start_date: str = "",
    end_date: str = "",
    lookback_days: int = 365,
    field_name: str = "PX_LAST",
) -> str:
    """Calculate the implied forward rate between two points on an OIS
    curve (e.g. 1Y1Y SOFR, 5Y5Y ESTR, 2Y1Y SONIA), plus its 1-year
    rolling z-score.

    Two input modes:
    1. Tenor-based (common): set start_tenor + end_tenor.
       - For '1Y1Y' → start_tenor='1Y', end_tenor='2Y'
       - For '5Y5Y' → start_tenor='5Y', end_tenor='10Y'
       - For '2Y1Y' → start_tenor='2Y', end_tenor='3Y'
    2. Date-based: set start_date + end_date (ISO YYYY-MM-DD).
       - Useful for custom windows like "forward between Dec 2026 and
         Jun 2027".

    Supply exactly ONE of the two modes.

    Use this tool when the user asks about:
    - Canonical forwards    (e.g. "What's 1Y1Y SOFR?", "5Y5Y ESTR?")
    - Policy-path forwards  (e.g. "What's priced 2Y out on SONIA?")
    - Custom date windows   (e.g. "Forward between Jun and Dec 2027")

    Do NOT use this tool for:
    - Central-bank meeting pricing ("cuts priced for the June FOMC")
      → use calculate_ois_meeting_pricing_tool instead.

    Parameters
    ----------
    curve_family : str
        OIS curve family (e.g. 'USD_SOFR_OIS', 'EUR_ESTR_OIS').
    start_tenor : str, optional
        Start of forward window as a tenor.  Leave as empty string "" if
        using date-based mode.
    end_tenor : str, optional
        End of forward window as a tenor.  Leave as empty string "" if
        using date-based mode.
    start_date : str, optional
        Start of forward window as ISO YYYY-MM-DD.  Leave as "" if using
        tenor-based mode.
    end_date : str, optional
        End of forward window as ISO YYYY-MM-DD.  Leave as "" if using
        tenor-based mode.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Observation field (default 'PX_LAST').
    """
    # MCP serializes scalars only — map "" → None before Pydantic validates.
    kwargs = dict(
        curve_family=curve_family,
        start_tenor=start_tenor or None,
        end_tenor=end_tenor or None,
        start_date=start_date or None,
        end_date=end_date or None,
        lookback_days=lookback_days,
        field_name=field_name,
    )
    return _run_tool(
        tool_name="calculate_ois_forward_rate_tool",
        schema_cls=OISForwardRateInput,
        tool_fn=calculate_ois_forward_rate,
        kwargs=kwargs,
    )


# ===========================================================================
# TOOL 4: calculate_ois_meeting_pricing
# ===========================================================================
@mcp.tool()
def calculate_ois_meeting_pricing_tool(
    central_bank: str,
    meeting_reference: str = "next:4",
    curve_family: str = "",
    field_name: str = "PX_LAST",
) -> str:
    """Compute OIS-implied central-bank policy rates on a
    meeting-by-meeting basis.  THE flagship OIS tool — this is what a
    macro PM reads every morning.

    For each requested meeting, returns:
    - implied policy rate during that meeting's window (percent)
    - cumulative bps of move priced from today through that meeting
    - bps of move priced AT that specific meeting
    - implied probability of a 25bp move at that meeting

    Use this tool when the user asks about:
    - Cuts/hikes priced          (e.g. "How many cuts for June FOMC?")
    - Terminal rate              (e.g. "Where's the SOFR terminal rate?")
    - Year-end pricing           (e.g. "What's SOFR priced by year-end?")
    - Specific meeting pricing   (e.g. "What's priced for the Dec ECB?")
    - Meeting-to-meeting moves   (e.g. "Show me the next 6 FOMC meetings")

    Do NOT use this tool for:
    - Non-meeting-aligned forward rates (1Y1Y, 5Y5Y)
      → use calculate_ois_forward_rate_tool instead.

    Parameters
    ----------
    central_bank : str
        Canonical values: 'FED' (also 'FOMC'), 'ECB', 'BOE', 'BOJ',
        'RBA', 'BOC'.  Case-insensitive.
    meeting_reference : str, optional
        Which meeting(s) to price.  'next' = the single next meeting;
        'next:N' = the next N meetings (e.g. 'next:6'); '+N' = the Nth
        meeting from today; 'YYYY-MM-DD' = the meeting on or nearest-
        after that date; 'all' = every upcoming meeting in the
        calendar.  Default 'next:4'.
    curve_family : str, optional
        Override the OIS curve.  Leave as "" to auto-select by central
        bank: FED→USD_SOFR_OIS, ECB→EUR_ESTR_OIS, BOE→GBP_SONIA_OIS,
        BOJ→JPY_OIS, RBA→AUD_OIS, BOC→CAD_OIS.
    field_name : str, optional
        Observation field (default 'PX_LAST').
    """
    kwargs = dict(
        central_bank=central_bank,
        meeting_reference=meeting_reference,
        curve_family=curve_family or None,
        field_name=field_name,
    )
    return _run_tool(
        tool_name="calculate_ois_meeting_pricing_tool",
        schema_cls=OISMeetingPricingInput,
        tool_fn=calculate_ois_meeting_pricing,
        kwargs=kwargs,
        strip_time_series=False,  # this tool doesn't produce a time series
    )


# ===========================================================================
# TOOL 5: calculate_ois_cross_market_spread
# ===========================================================================
@mcp.tool()
def calculate_ois_cross_market_spread_tool(
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "PX_LAST",
) -> str:
    """Calculate the par-swap-rate differential between the SAME tenor
    on TWO DIFFERENT OIS curves (e.g. 2Y SOFR − 2Y ESTR, the G4 policy
    differential).

    Spread definition: curve_family_1 − curve_family_2 (in bps).
    Convention for SOFR-ESTR: curve_family_1='USD_SOFR_OIS',
    curve_family_2='EUR_ESTR_OIS'.

    Use this tool when the user asks about:
    - Cross-currency OIS spreads (e.g. "SOFR-ESTR 2Y?", "SOFR-SONIA?")
    - Policy differentials        (e.g. "US-UK front-end differential?")
    - FX-vs-rates signals         (e.g. "Is SOFR-ESTR widening driving EURUSD?")

    Do NOT use this tool for:
    - Same-curve tenor spreads (SOFR 2s10s)
      → use calculate_ois_curve_spread_tool instead.
    - Sovereign cross-market spreads (UST vs Bund)
      → use calculate_cross_market_spread_tool.

    Parameters
    ----------
    curve_family_1 : str
        First (numerator) OIS curve.  Examples: 'USD_SOFR_OIS',
        'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', 'CAD_OIS'.
    curve_family_2 : str
        Second (denominator) OIS curve.  Must differ from curve_family_1.
    tenor : str
        Tenor point, e.g. '3M', '1Y', '2Y', '5Y', '10Y'.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Observation field (default 'PX_LAST').
    """
    return _run_tool(
        tool_name="calculate_ois_cross_market_spread_tool",
        schema_cls=OISCrossMarketSpreadInput,
        tool_fn=calculate_ois_cross_market_spread,
        kwargs=dict(
            curve_family_1=curve_family_1,
            curve_family_2=curve_family_2,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        ),
    )


# ===========================================================================
# TOOL 6: scan_ois_extremes
# ===========================================================================
@mcp.tool()
def scan_ois_extremes_tool(
    curve_families: str = "",
    top_n: int = 10,
    min_abs_z_score: float = 1.5,
    field_name: str = "PX_LAST",
) -> str:
    """Scan ALL OIS swap instruments for z-score extremes and return
    the most statistically stretched observations, ranked by absolute
    z-score.

    Morning-sweep tool for the OIS side: "anything unusual in swaps
    space?"

    Use this tool when the user asks about:
    - Global OIS extremes     (e.g. "What's stretched in OIS?")
    - Morning OIS briefing    (e.g. "Flag unusual OIS moves")
    - Policy-pricing shifts   (e.g. "Any OIS at 2-sigma today?")
    - Anomaly detection       (e.g. "OIS z-scores above 2?")

    Do NOT use this tool for:
    - Sovereign-bond extremes
      → use scan_extremes_tool instead.

    Parameters
    ----------
    curve_families : str, optional
        Comma-separated list of OIS curves to scope the scan.  Empty
        string means scan all.  Examples: 'USD_SOFR_OIS' or
        'USD_SOFR_OIS,EUR_ESTR_OIS'.
    top_n : int, optional
        Number of top-ranked extremes to return (default 10, max 50).
    min_abs_z_score : float, optional
        Minimum |z-score| threshold (default 1.5).  Set to 0 to see
        everything.
    field_name : str, optional
        Observation field (default 'PX_LAST').
    """
    # Parse the comma-separated curve_families string into a list.
    parsed_families = None
    if curve_families and curve_families.strip():
        parsed_families = [
            cf.strip() for cf in curve_families.split(",") if cf.strip()
        ]

    return _run_tool(
        tool_name="scan_ois_extremes_tool",
        schema_cls=OISScannerInput,
        tool_fn=scan_ois_extremes,
        kwargs=dict(
            curve_families=parsed_families,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
            field_name=field_name,
        ),
        strip_time_series=False,  # scanner returns a ranked list, no ts
    )


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info("Starting OIS Sub-Agent MCP server (stdio transport)...")
    mcp.run(transport="stdio")

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
4. calculate_ois_cross_market_spread_tool  — SOFR-ESTR, ESTR-SONIA, etc.
5. scan_ois_extremes_tool                  — z-score screener across OIS universe

Meeting-pricing was removed: the prior implementation approximated
central-bank meeting moves by linearly interpolating par OIS rates,
which produces a ramp where the market prices a step function.
Outputs drifted visibly from Bloomberg WIRP, which would immediately
damage PM trust in the rest of the copilot.  It will be rebuilt later
on top of ingested WIRP data.

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
    OISRateLevelInput,
    OISScannerInput,
)
from rates_agent.ois.tools.cross_market_spread import (  # noqa: E402
    CONFIG_PATH as OIS_CROSS_MARKET_SPREAD_CONFIG_PATH,
    calculate_ois_cross_market_spread,
)
from rates_agent.ois.tools.curve_spread import (  # noqa: E402
    CONFIG_PATH as OIS_CURVE_SPREAD_CONFIG_PATH,
    calculate_ois_curve_spread,
)
from rates_agent.ois.tools.forward_rate import calculate_ois_forward_rate  # noqa: E402
from rates_agent.ois.tools.rate_level import (  # noqa: E402
    CONFIG_PATH as OIS_RATE_LEVEL_CONFIG_PATH,
    get_ois_rate_level,
)
from rates_agent.ois.tools.scanner import scan_ois_extremes  # noqa: E402
from shared.config import load_tool_config  # noqa: E402

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
        "cross-currency OIS spreads, and z-score extremes on SOFR, "
        "ESTR, SONIA, TONA, AONIA, and CORRA curves.  Never attempt "
        "the math yourself — always call a tool and relay its output.  "
        "Central-bank meeting-pricing questions (e.g. 'how many cuts "
        "are priced for the June FOMC?') are not yet supported by this "
        "agent — the previous approximation produced numbers that "
        "visibly disagreed with Bloomberg WIRP.  If the user asks, "
        "explain that meeting-pricing will return in a future release "
        "once Bloomberg probability data is ingested."
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
    field_name: str = "",
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
        Bloomberg field mnemonic.  Leave as the default empty string
        ""  to use the bundled ``default_swap_rate_field`` convention
        from rate_level/config.yaml (currently 'PX_LAST').  Pass an
        explicit field name to override per call.  Mirrors the
        empty-string sentinel pattern used by sovereign
        get_yield_levels_tool / curve_move_classifier so the YAML
        default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_swap_rate_field``.  Without this, the LLM omitting
    # field_name would always hit a hardcoded default regardless of
    # what the YAML says — same shadowing pattern fixed for sovereign
    # curve_move_classifier in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = OISRateLevelInput(
            curve_family=curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning("[calculate_ois_rate_level_tool] input validation failed: %s", exc)
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("[calculate_ois_rate_level_tool] failed to connect to TimescaleDB")
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the rate_level tool's bundled config explicitly so the
    # config dependency is observable here.  load_tool_config caches
    # by path, so this is a free lookup after the first call within
    # the MCP subprocess's lifetime.  Mirrors sovereign
    # get_yield_levels_tool exactly.
    try:
        rl_config = load_tool_config(OIS_RATE_LEVEL_CONFIG_PATH)
        result = get_ois_rate_level(
            engine=engine, params=params, config=rl_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_ois_rate_level_tool] unhandled error for %s %s",
            params.curve_family, params.tenor,
        )
        return json.dumps(
            {"error": f"calculate_ois_rate_level_tool failed for "
             f"{params.curve_family} {params.tenor}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_ois_rate_level_tool] tool call complete: %s %s → %s",
        params.curve_family, params.tenor, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip time_series before returning to the LLM (frontend REST
    # path returns the full payload).  Same convention as sovereign
    # get_yield_levels_tool's wrapper — the LLM doesn't need every
    # historical row to answer "where's SOFR 2Y?".
    llm_response: dict = {
        k: v for k, v in result.items() if k != "time_series"
    }
    ts_rows = len(result.get("time_series", {}).get("rows", []) or [])
    if ts_rows:
        logger.info(
            "[calculate_ois_rate_level_tool] withheld %d time_series rows from LLM context.",
            ts_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 2: calculate_ois_curve_spread
# ===========================================================================
@mcp.tool()
def calculate_ois_curve_spread_tool(
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
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
        Bloomberg field mnemonic.  Leave as the default empty string
        ""  to use the bundled ``default_swap_rate_field`` convention
        from curve_spread/config.yaml (currently 'PX_LAST').  Pass an
        explicit field name to override per call.  Mirrors the
        empty-string sentinel pattern used by sovereign
        get_yield_levels_tool / curve_move_classifier so the YAML
        default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_swap_rate_field``.  Without this, the LLM omitting
    # field_name would always hit a hardcoded default regardless of
    # what the YAML says — same shadowing pattern fixed for sovereign
    # curve_move_classifier in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = OISCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning("[calculate_ois_curve_spread_tool] input validation failed: %s", exc)
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"},
            default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("[calculate_ois_curve_spread_tool] failed to connect to TimescaleDB")
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the curve_spread tool's bundled config explicitly so the
    # config dependency is observable here.  load_tool_config caches
    # by path, so this is a free lookup after the first call within
    # the MCP subprocess's lifetime.  Mirrors sovereign
    # get_yield_levels_tool + OIS calculate_ois_rate_level_tool.
    try:
        cs_config = load_tool_config(OIS_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_ois_curve_spread(
            engine=engine, params=params, config=cs_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_ois_curve_spread_tool] unhandled error for %s %s/%s",
            params.curve_family, params.short_tenor, params.long_tenor,
        )
        return json.dumps(
            {"error": f"calculate_ois_curve_spread_tool failed for "
             f"{params.curve_family} "
             f"{params.short_tenor}/{params.long_tenor}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_ois_curve_spread_tool] tool call complete: %s %s/%s → %s",
        params.curve_family, params.short_tenor, params.long_tenor, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip both the bespoke time_series list AND the canonical
    # TimeSeries payloads from the LLM-facing response.  Frontend /
    # future REST surfaces get the full payload via the dict
    # result; the LLM doesn't need every historical row to answer
    # "where's SOFR 2s10s?".
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in ("time_series", "time_series_spread", "time_series_zscore")
    }
    bespoke_rows = len(result.get("time_series") or [])
    if bespoke_rows:
        logger.info(
            "[calculate_ois_curve_spread_tool] withheld %d time_series rows from LLM context.",
            bespoke_rows,
        )
    return json.dumps(llm_response, default=str)


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
    - Central-bank meeting pricing ("cuts priced for the June FOMC") —
      that capability is not yet available in this agent.  Explain to
      the user that meeting-pricing will return once Bloomberg WIRP
      data is ingested, rather than producing a forward-rate proxy.

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
# TOOL 4: calculate_ois_cross_market_spread
# ===========================================================================
@mcp.tool()
def calculate_ois_cross_market_spread_tool(
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
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
        Bloomberg field mnemonic.  Leave as the default empty string
        ""  to use the bundled ``default_swap_rate_field`` convention
        from cross_market_spread/config.yaml (currently 'PX_LAST').
        Pass an explicit field name to override per call.  Mirrors the
        empty-string sentinel pattern used by sovereign
        get_yield_levels_tool / curve_move_classifier + OIS
        rate_level / curve_spread.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_swap_rate_field``.  Without this, the LLM omitting
    # field_name would always hit a hardcoded default regardless of
    # what the YAML says — same shadowing pattern fixed for sovereign
    # curve_move_classifier in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = OISCrossMarketSpreadInput(
            curve_family_1=curve_family_1,
            curve_family_2=curve_family_2,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_ois_cross_market_spread_tool] input validation failed: %s",
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
            "[calculate_ois_cross_market_spread_tool] failed to connect to TimescaleDB"
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the cross_market_spread tool's bundled config explicitly so
    # the config dependency is observable here.  load_tool_config
    # caches by path, so this is a free lookup after the first call
    # within the MCP subprocess's lifetime.  Mirrors sovereign
    # cross_market_spread + OIS rate_level / curve_spread.
    try:
        cms_config = load_tool_config(OIS_CROSS_MARKET_SPREAD_CONFIG_PATH)
        result = calculate_ois_cross_market_spread(
            engine=engine, params=params, config=cms_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_ois_cross_market_spread_tool] unhandled error for "
            "%s vs %s at %s",
            params.curve_family_1, params.curve_family_2, params.tenor,
        )
        return json.dumps(
            {"error": f"calculate_ois_cross_market_spread_tool failed for "
             f"{params.curve_family_1} vs {params.curve_family_2} at "
             f"{params.tenor}: {exc}"},
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_ois_cross_market_spread_tool] tool call complete: "
        "%s vs %s at %s → %s",
        params.curve_family_1, params.curve_family_2, params.tenor, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip the bespoke time_series list AND both canonical TimeSeries
    # payloads from the LLM-facing response.  Frontend / future REST
    # surfaces consume the full dict directly via the tool result.
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in ("time_series", "time_series_spread", "time_series_zscore")
    }
    bespoke_rows = len(result.get("time_series") or [])
    if bespoke_rows:
        logger.info(
            "[calculate_ois_cross_market_spread_tool] withheld %d "
            "time_series rows from LLM context.",
            bespoke_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 5: scan_ois_extremes
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

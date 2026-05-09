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

2. calculate_inflation_swap_curve_spread_tool — same-curve ZCIS
   tenor spread (e.g. USD_ZCIS 5s10s, EUR_ZCIS 5s30s) computed by
   composing the level primitive twice and applying the per-trade-
   date difference (long - short) * 100 in bps.  Surfaces the same
   reference-metadata fields (shared by both legs by the same-
   curve invariant).

3. calculate_inflation_swap_forward_tool — same-curve ZCIS forward
   rate (e.g. USD_ZCIS 5Y5Y, EUR_ZCIS 5Y5Y, GBP_ZCIS 2Y3Y)
   computed by composing the level primitive twice and applying
   the dual-compounding geometric forward formula on the two
   endpoint ZCIS rates.  Surfaces the same reference-metadata
   fields shared by both legs by the same-curve invariant.

Subsequent inflation-swap primitives (cross-market spread,
swap-breakeven basis) will land here as separate primitive
builds.

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
    InflationSwapCurveSpreadInput,
    InflationSwapForwardInput,
    InflationSwapRateLevelInput,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (  # noqa: E402
    CONFIG_PATH as INFLATION_SWAP_RATE_LEVEL_CONFIG_PATH,
    calculate_inflation_swap_rate_level,
)
from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread import (  # noqa: E402
    CONFIG_PATH as INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH,
    calculate_inflation_swap_curve_spread,
)
from rates_agent.inflation_swaps.tools.inflation_swap_forward import (  # noqa: E402
    CONFIG_PATH as INFLATION_SWAP_FORWARD_CONFIG_PATH,
    calculate_inflation_swap_forward,
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
# TOOL 2: calculate_inflation_swap_curve_spread
# ===========================================================================
@mcp.tool()
def calculate_inflation_swap_curve_spread_tool(
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the same-curve zero-coupon inflation swap (ZCIS) tenor
    spread between two pillars of the same ZCIS curve family
    (e.g. USD_ZCIS 5s10s, EUR_ZCIS 5s30s, GBP_ZCIS 2s10s), plus
    daily/weekly/monthly bps changes, 1-year z-score, and trailing
    1Y high/low/percentile in bps.  Surfaces the load-bearing
    reference metadata (``inflation_index_family``, ``index_lag``,
    ``interpolation``, ``underlying_index``) shared by both legs
    so the desk can interpret the spread honestly.

    Use this tool when the user asks about:
    - ZCIS curve shape           (e.g. "Is USD ZCIS 5s10s
      flattening?")
    - ZCIS tenor spreads         (e.g. "Where's EUR 5s30s ZCIS?")
    - ZCIS curve-spread moves    (e.g. "How much has GBP 2s10s
      ZCIS curve moved this week?")
    - ZCIS curve-spread extremes (e.g. "Is USD 5s10s ZCIS at a
      1-year high?")

    Do NOT use this tool for:
    - Cross-curve ZCIS spreads (e.g. USD ZCIS 5Y vs EUR ZCIS 5Y) —
      that's a separate primitive
      (``calculate_cross_market_inflation_swap_spread_tool``,
      build_order 12) currently deferred.
    - Sovereign-linker bond-implied breakeven curve spreads — call
      the inflation_indexed_bonds agent's
      ``calculate_breakeven_curve_spread_tool`` (linker bonds vs
      nominal sovereigns) instead.
    - Nominal sovereign curve spreads — call the sovereign_bonds
      agent's ``calculate_curve_spread_tool``.
    - OIS curve spreads — call the ois agent's
      ``calculate_ois_curve_spread_tool``.

    Parameters
    ----------
    curve_family : str
        Inflation-swap curve identifier exactly as stored in
        instrument_master.  Examples: 'USD_ZCIS', 'EUR_ZCIS',
        'GBP_ZCIS'.  See
        ``rates_agent/playbooks/inflation_swaps.yml``.  Same-curve
        invariant: a single ``curve_family`` is shared by both
        endpoints; cross-curve combinations are a separate
        primitive.
    short_tenor : str
        Short tenor pillar (e.g. '2Y' for 2s10s, '5Y' for 5s10s
        or 5s30s).  Must be a supported ZCIS pillar on this
        curve_family — current ingested grid is 1Y / 2Y / 3Y / 5Y
        / 10Y / 20Y / 30Y on each of USD_ZCIS / EUR_ZCIS /
        GBP_ZCIS.
    long_tenor : str
        Long tenor pillar (e.g. '10Y' for 2s10s / 5s10s, '30Y'
        for 5s30s).  Must be strictly longer than ``short_tenor``.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Bloomberg field mnemonic for the ZCIS rate.  Leave as the
        default empty string ""  to use the bundled
        ``default_zcis_rate_field`` convention from
        inflation_swap_curve_spread/config.yaml (currently
        'PX_MID').  Mirrors the empty-string sentinel pattern
        used by sovereign / OIS / linker / ZCIS level tools so
        the YAML default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_zcis_rate_field``.  Same shadowing pattern fixed for
    # sovereign curve_move_classifier in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = InflationSwapCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_inflation_swap_curve_spread_tool] input "
            "validation failed: %s",
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
            "[calculate_inflation_swap_curve_spread_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the inflation_swap_curve_spread tool's bundled config
    # explicitly so the dependency is observable here.  The cross-
    # config lint enforces the value-agreement on the shared
    # convention names with the inner level primitive's bundled
    # config, so threading this same ToolConfig into the inner
    # calls keeps methodology consistent end-to-end.
    try:
        iscs_config = load_tool_config(
            INFLATION_SWAP_CURVE_SPREAD_CONFIG_PATH,
        )
        result = calculate_inflation_swap_curve_spread(
            engine=engine, params=params, config=iscs_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_inflation_swap_curve_spread_tool] unhandled "
            "error for %s %s/%s",
            params.curve_family, params.short_tenor, params.long_tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_inflation_swap_curve_spread_tool "
                    f"failed for {params.curve_family} "
                    f"{params.short_tenor}/{params.long_tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_inflation_swap_curve_spread_tool] tool call "
        "complete: %s %s/%s → %s",
        params.curve_family, params.short_tenor, params.long_tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip both bespoke and canonical TimeSeries payloads before
    # returning to the LLM (frontend REST path returns the full
    # payload).  Same convention as sibling rate-level / curve-
    # spread tools.
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in ("time_series", "time_series_spread", "time_series_zscore")
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    spread_rows = len(
        result.get("time_series_spread", {}).get("rows", []) or []
    )
    zscore_rows = len(
        result.get("time_series_zscore", {}).get("rows", []) or []
    )
    if bespoke_rows or spread_rows or zscore_rows:
        logger.info(
            "[calculate_inflation_swap_curve_spread_tool] withheld "
            "bespoke=%d spread=%d zscore=%d rows from LLM context.",
            bespoke_rows, spread_rows, zscore_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 3: calculate_inflation_swap_forward
# ===========================================================================
@mcp.tool()
def calculate_inflation_swap_forward_tool(
    curve_family: str,
    start_tenor: str,
    end_tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the same-curve zero-coupon inflation swap (ZCIS) forward
    rate between two pillars on the same ZCIS curve family
    (e.g. USD_ZCIS 5Y5Y, EUR_ZCIS 5Y5Y, GBP_ZCIS 2Y3Y), plus
    daily/weekly/monthly bps changes, 1-year z-score, and trailing
    1Y high/low/percentile in bps.  Surfaces the load-bearing
    reference metadata (``inflation_index_family``, ``index_lag``,
    ``interpolation``, ``underlying_index``) shared by both legs
    so the desk can interpret the forward honestly.

    Use this tool when the user asks about:
    - ZCIS forwards              (e.g. "Where's USD 5Y5Y ZCIS?")
    - Forward inflation pricing  (e.g. "How has EUR 5Y5Y ZCIS
      moved this month?")
    - Forward inflation extremes (e.g. "Is GBP 2Y3Y ZCIS at a
      1-year high?")

    Do NOT use this tool for:
    - Cross-curve ZCIS forwards (e.g. USD ZCIS 5Y vs EUR ZCIS 10Y) —
      out of scope for V1.
    - Sovereign-linker bond-implied forward breakevens — call the
      inflation_indexed_bonds agent's
      ``calculate_forward_breakeven_simple_tool`` (linker bonds vs
      nominal sovereigns) instead.
    - Nominal sovereign forward rates — those are not yet a
      primitive in this repo.
    - OIS forward rates — call the ois agent's
      ``calculate_ois_forward_rate_tool``.

    Parameters
    ----------
    curve_family : str
        Inflation-swap curve identifier exactly as stored in
        instrument_master.  Examples: 'USD_ZCIS', 'EUR_ZCIS',
        'GBP_ZCIS'.  See
        ``rates_agent/playbooks/inflation_swaps.yml``.  Same-curve
        invariant: a single ``curve_family`` is shared by both
        endpoints; cross-curve forwards are not in scope for V1.
    start_tenor : str
        Start tenor of the forward window (e.g. '5Y' for 5Y5Y,
        '5Y' for 5Y10Y, '2Y' for 2Y3Y).  Must be a supported ZCIS
        pillar on this curve_family — current ingested grid is
        1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y on each of USD_ZCIS /
        EUR_ZCIS / GBP_ZCIS.
    end_tenor : str
        End tenor of the forward window (e.g. '10Y' for 5Y5Y,
        '15Y' for 5Y10Y, '5Y' for 2Y3Y).  Must be strictly longer
        than ``start_tenor``.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Bloomberg field mnemonic for the ZCIS rate.  Leave as the
        default empty string ""  to use the bundled
        ``default_zcis_rate_field`` convention from
        inflation_swap_forward/config.yaml (currently 'PX_MID').
        Mirrors the empty-string sentinel pattern used by
        sovereign / OIS / linker / ZCIS level / ZCIS curve-spread
        tools so the YAML default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_zcis_rate_field``.  Same shadowing pattern fixed for
    # sovereign curve_move_classifier in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = InflationSwapForwardInput(
            curve_family=curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_inflation_swap_forward_tool] input "
            "validation failed: %s",
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
            "[calculate_inflation_swap_forward_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the inflation_swap_forward tool's bundled config
    # explicitly so the dependency is observable here.  The cross-
    # config lint enforces value-agreement on the shared convention
    # names with the inner level primitive's bundled config, so
    # threading this same ToolConfig into the inner calls keeps
    # methodology consistent end-to-end.
    try:
        isf_config = load_tool_config(
            INFLATION_SWAP_FORWARD_CONFIG_PATH,
        )
        result = calculate_inflation_swap_forward(
            engine=engine, params=params, config=isf_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_inflation_swap_forward_tool] unhandled "
            "error for %s %s%s",
            params.curve_family, params.start_tenor, params.end_tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_inflation_swap_forward_tool failed "
                    f"for {params.curve_family} "
                    f"{params.start_tenor}{params.end_tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_inflation_swap_forward_tool] tool call "
        "complete: %s %s%s → %s",
        params.curve_family, params.start_tenor, params.end_tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip both bespoke and canonical TimeSeries payloads before
    # returning to the LLM (frontend REST path returns the full
    # payload).  Same convention as sibling rate-level / curve-
    # spread tools.
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in ("time_series", "time_series_forward", "time_series_zscore")
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    forward_rows = len(
        result.get("time_series_forward", {}).get("rows", []) or []
    )
    zscore_rows = len(
        result.get("time_series_zscore", {}).get("rows", []) or []
    )
    if bespoke_rows or forward_rows or zscore_rows:
        logger.info(
            "[calculate_inflation_swap_forward_tool] withheld "
            "bespoke=%d forward=%d zscore=%d rows from LLM context.",
            bespoke_rows, forward_rows, zscore_rows,
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

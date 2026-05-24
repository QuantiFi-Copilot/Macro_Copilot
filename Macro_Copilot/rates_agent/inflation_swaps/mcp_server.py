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

4. calculate_cross_market_inflation_swap_spread_tool — same-tenor
   cross-market ZCIS spread (e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y)
   computed by composing the level primitive twice and applying
   the per-trade-date difference (leg_a - leg_b) in PERCENT, with
   the BPS form derived as spread_pct * 100.  Surfaces PER-LEG
   reference metadata so the index-family caveat (USD_ZCIS /
   EUR_ZCIS / GBP_ZCIS reference different inflation indices) is
   visible on the wire.

5. calculate_swap_breakeven_basis_simple_tool — same-tenor,
   same-currency swap-breakeven basis (e.g. USD_ZCIS 10Y minus
   UST/USD_TIPS 10Y breakeven) computed by composing the ZCIS
   rate-level primitive AND the linker bond-implied breakeven
   primitive, then taking the per-trade-date difference (zcis -
   breakeven) in PERCENT, with the BPS form derived as
   basis_pct * 100.  Sign convention is wire-locked at
   ``zcis_minus_breakeven`` per the catalog's
   methodology_guardrails.  NOT a clean liquidity-premium read —
   also reflects index-lag, linker on-the-run, and structural
   ZCIS basis effects, surfaced via the wire-disclosed
   methodology_label.

6. calculate_inflation_swap_butterfly_tool — same-curve ZCIS
   butterfly (3-point ZCIS curve curvature, e.g. USD_ZCIS
   5s10s30s, EUR_ZCIS 2s5s10s, GBP_ZCIS 2s10s30s) computed by
   composing the ZCIS rate-level primitive THREE times and
   applying the FIXED simple-butterfly weighting
   ``(belly - 0.5*(short + long)) * 100`` in BPS.  Raw inflation-
   swap-rate space — NO basis subtraction, NO IRP adjustment, NO
   fitted curve.  Surfaces the same reference-metadata fields
   shared by all three legs by the same-curve invariant.

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
    CrossMarketInflationSwapSpreadInput,
    InflationSwapButterflyInput,
    InflationSwapCurveSpreadInput,
    InflationSwapForwardInput,
    InflationSwapRateLevelInput,
    SwapBreakevenBasisSimpleInput,
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
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (  # noqa: E402
    CONFIG_PATH as CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
    calculate_cross_market_inflation_swap_spread,
)
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (  # noqa: E402
    CONFIG_PATH as SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH,
    calculate_swap_breakeven_basis_simple,
)
from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (  # noqa: E402
    CONFIG_PATH as INFLATION_SWAP_BUTTERFLY_CONFIG_PATH,
    calculate_inflation_swap_butterfly,
)
from rates_agent.inflation_swaps.tools.cpi_surprise import (  # noqa: E402
    CONFIG_PATH as CPI_SURPRISE_CONFIG_PATH,
    CpiSurpriseInput,
    calculate_cpi_surprise,
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
        "forwards, cross-market spreads, butterflies, and the swap-vs-"
        "bond breakeven basis.  Never attempt the math yourself — "
        "always call a tool "
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
# TOOL 4: calculate_cross_market_inflation_swap_spread
# ===========================================================================
@mcp.tool()
def calculate_cross_market_inflation_swap_spread_tool(
    leg_a_curve_family: str,
    leg_b_curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the same-tenor cross-market zero-coupon inflation swap
    (ZCIS) spread between two ZCIS curve families at the same
    pillar (e.g. USD_ZCIS 5Y minus EUR_ZCIS 5Y, USD_ZCIS 10Y minus
    GBP_ZCIS 10Y, EUR_ZCIS 5Y minus GBP_ZCIS 5Y), plus
    daily/weekly/monthly bps changes, 1-year z-score, and trailing
    1Y high/low/percentile in bps.  Surfaces PER-LEG reference
    metadata (``leg_a_inflation_index_family`` /
    ``leg_b_inflation_index_family`` / ``leg_a_index_lag`` /
    ``leg_b_index_lag`` / ``leg_a_interpolation`` /
    ``leg_b_interpolation`` / ``leg_a_underlying_index`` /
    ``leg_b_underlying_index``) so the index-family caveat is
    visible on the wire.

    INDEX-FAMILY CAVEAT (load-bearing):
    USD_ZCIS, EUR_ZCIS, and GBP_ZCIS reference DIFFERENT inflation
    indices (US CPI-U / Eurozone HICP-xT / UK RPI), so this spread
    captures BOTH inflation-expectation differentials AND
    structural index-family differences; it is NOT a clean
    expected-inflation divergence.

    Use this tool when the user asks about:
    - Cross-market ZCIS spreads   (e.g. "Where's USD-EUR 5Y ZCIS?")
    - Cross-market ZCIS divergence
                                  (e.g. "How wide is the US/UK
                                  10Y inflation-swap differential?")
    - Cross-market ZCIS extremes  (e.g. "Is USD-EUR 5Y ZCIS at a
                                  1-year wide?")

    Do NOT use this tool for:
    - Same-curve ZCIS curve spreads (e.g. USD_ZCIS 5s10s) — that's
      ``calculate_inflation_swap_curve_spread_tool``.
    - Sovereign-linker bond-implied breakeven cross-country spreads
      — call the inflation_indexed_bonds agent's
      ``calculate_cross_country_breakeven_spread_simple_tool``.
    - Nominal sovereign cross-market spreads — call the
      sovereign_bonds agent's ``calculate_cross_market_spread_tool``.
    - OIS cross-market spreads — call the ois agent's
      ``calculate_ois_cross_market_spread_tool``.

    Sign convention: spread = leg_a - leg_b.  The LEFT leg
    (``leg_a_curve_family``) is the numerator and the RIGHT leg
    (``leg_b_curve_family``) is the denominator, so the spread
    sign is predictable from the input ordering.

    Parameters
    ----------
    leg_a_curve_family : str
        Left (numerator) inflation-swap curve family.  Examples:
        'USD_ZCIS', 'EUR_ZCIS', 'GBP_ZCIS'.  Must differ from
        ``leg_b_curve_family`` — same-curve spreads belong to
        ``calculate_inflation_swap_curve_spread_tool``.
    leg_b_curve_family : str
        Right (denominator) inflation-swap curve family.  Spread
        direction: leg_a - leg_b.
    tenor : str
        Single tenor pillar shared by both legs (e.g. '5Y',
        '10Y').  Must be a supported pillar on BOTH legs — current
        ingested grid is 1Y / 2Y / 3Y / 5Y / 10Y / 20Y / 30Y on
        each of USD_ZCIS / EUR_ZCIS / GBP_ZCIS.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Bloomberg field mnemonic for the ZCIS rate.  Leave as the
        default empty string ""  to use the bundled
        ``default_zcis_rate_field`` convention from
        cross_market_inflation_swap_spread/config.yaml (currently
        'PX_MID').  Threaded into BOTH inner level calls so the two
        legs are read off the same Bloomberg field by construction.
        Mirrors the empty-string sentinel pattern used by sovereign /
        OIS / linker / ZCIS level / ZCIS curve-spread / ZCIS forward
        tools so the YAML default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_zcis_rate_field``.  Same shadowing pattern fixed for
    # sovereign curve_move_classifier in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = CrossMarketInflationSwapSpreadInput(
            leg_a_curve_family=leg_a_curve_family,
            leg_b_curve_family=leg_b_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_cross_market_inflation_swap_spread_tool] "
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
            "[calculate_cross_market_inflation_swap_spread_tool] "
            "failed to connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the cross_market_inflation_swap_spread tool's bundled
    # config explicitly so the dependency is observable here.  The
    # cross-config lint enforces value-agreement on the shared
    # convention names with the inner level primitive's bundled
    # config, so threading this same ToolConfig into the inner
    # calls keeps methodology consistent end-to-end.
    try:
        cmiss_config = load_tool_config(
            CROSS_MARKET_INFLATION_SWAP_SPREAD_CONFIG_PATH,
        )
        result = calculate_cross_market_inflation_swap_spread(
            engine=engine, params=params, config=cmiss_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_cross_market_inflation_swap_spread_tool] "
            "unhandled error for %s-%s %s",
            params.leg_a_curve_family, params.leg_b_curve_family,
            params.tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_cross_market_inflation_swap_spread_tool "
                    f"failed for {params.leg_a_curve_family}-"
                    f"{params.leg_b_curve_family} {params.tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_cross_market_inflation_swap_spread_tool] tool "
        "call complete: %s-%s %s → %s",
        params.leg_a_curve_family, params.leg_b_curve_family,
        params.tenor, status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip both bespoke and canonical TimeSeries payloads before
    # returning to the LLM (frontend REST path returns the full
    # payload).  Same convention as sibling rate-level / curve-
    # spread / forward tools.
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
            "[calculate_cross_market_inflation_swap_spread_tool] "
            "withheld bespoke=%d spread=%d zscore=%d rows from LLM "
            "context.",
            bespoke_rows, spread_rows, zscore_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 5: calculate_swap_breakeven_basis_simple
# ===========================================================================
@mcp.tool()
def calculate_swap_breakeven_basis_simple_tool(
    zcis_curve_family: str,
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the same-tenor, same-currency swap-breakeven basis: a
    zero-coupon inflation swap (ZCIS) rate minus the corresponding
    generic linker bond-implied breakeven inflation rate at the
    same pillar (e.g. USD_ZCIS 10Y minus UST/USD_TIPS 10Y
    breakeven, EUR_ZCIS 5Y minus FR_OAT/EUR_FR_LINKER 5Y
    breakeven, GBP_ZCIS 10Y minus UK_GILT/GBP_LINKER 10Y breakeven),
    plus daily/weekly/monthly bps changes, 1-year z-score, and
    trailing 1Y high/low/percentile in bps.  Surfaces the ZCIS
    leg's load-bearing reference metadata
    (``zcis_inflation_index_family``, ``zcis_index_lag``,
    ``zcis_interpolation``, ``zcis_underlying_index``) and a
    derived index-family summary (``index_families_match`` plus a
    human-readable ``index_family_caveat``) so the basis caveat is
    visible on the wire.

    Concept honesty (load-bearing): the swap-breakeven basis is
    NOT a clean liquidity-premium read.  It also reflects:
      - index-lag differences between ZCIS conventions and the
        linker bond's realised CPI accrual,
      - linker bond on-the-run / liquidity premium effects in the
        nominal-vs-real decomposition,
      - structural ZCIS vs linker-breakeven basis present even in
        benign markets.

    Sign convention: basis = zcis - breakeven.  Wire-locked at
    ``zcis_minus_breakeven`` per the catalog's
    methodology_guardrails; the compute layer raises
    NotImplementedError on any other sign convention.

    Use this tool when the user asks about:
    - Swap-breakeven basis        (e.g. "Where's USD 10Y
      swap-breakeven basis?")
    - Swap-vs-linker-breakeven divergence
                                   (e.g. "How wide is the EUR
                                   5Y swap-breakeven basis?")
    - Swap-breakeven basis extremes
                                   (e.g. "Is GBP 10Y swap-breakeven
                                   basis at a 1-year wide?")
    - Inflation-swap vs linker-bond relative-value reads.

    Do NOT use this tool for:
    - Same-curve ZCIS curve spreads (e.g. USD_ZCIS 5s10s) — that's
      ``calculate_inflation_swap_curve_spread_tool``.
    - Cross-market ZCIS spreads (e.g. USD_ZCIS 5Y vs EUR_ZCIS 5Y)
      — that's ``calculate_cross_market_inflation_swap_spread_tool``.
    - Sovereign-linker bond-implied breakevens directly — call the
      inflation_indexed_bonds agent's
      ``calculate_breakeven_inflation_simple_tool``.
    - ZCIS rate levels — call
      ``calculate_inflation_swap_rate_level_tool``.

    Parameters
    ----------
    zcis_curve_family : str
        Inflation-swap curve family (e.g. 'USD_ZCIS', 'EUR_ZCIS',
        'GBP_ZCIS').  Filtered with
        instrument_type='inflation_swap' AND
        pricing_type='zero_coupon_breakeven' on the inner DB read.
    nominal_curve_family : str
        Nominal sovereign curve family feeding the breakeven leg
        (e.g. 'UST', 'FR_OAT', 'UK_GILT').  Filtered with
        instrument_type='sovereign_benchmark' inside the composed
        breakeven primitive.
    linker_curve_family : str
        Sovereign linker curve family feeding the breakeven leg
        (e.g. 'USD_TIPS', 'EUR_FR_LINKER', 'GBP_LINKER').  Must
        differ from ``nominal_curve_family``.  The same-country
        invariant on the breakeven leg (nominal/linker share
        country + currency) is enforced inside the breakeven
        primitive and inherited transitively here.
    tenor : str
        Single tenor pillar shared by the ZCIS leg and both
        breakeven legs (e.g. '5Y', '10Y').  Available tenors are
        country-specific intersections of the ZCIS grid (1Y / 2Y
        / 3Y / 5Y / 10Y / 20Y / 30Y on each ZCIS curve) and the
        linker / nominal grids.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Bloomberg field mnemonic threaded into BOTH inner calls.
        Leave as the default empty string ""  to use each inner
        primitive's own bundled YAML default — the ZCIS leg uses
        ``default_zcis_rate_field`` (PX_MID), the breakeven leg
        uses ``default_field_name`` (YLD_YTM_MID).  Pass an
        explicit field name to override per call; the same value
        is threaded into both inner calls.  Mirrors the
        empty-string sentinel pattern used by sibling rates tools.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against each inner primitive's bundled
    # YAML default.  Same shadowing pattern fixed for sovereign
    # curve_move_classifier in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = SwapBreakevenBasisSimpleInput(
            zcis_curve_family=zcis_curve_family,
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_swap_breakeven_basis_simple_tool] "
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
            "[calculate_swap_breakeven_basis_simple_tool] "
            "failed to connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the swap_breakeven_basis_simple tool's bundled config
    # explicitly so the dependency is observable here.  The cross-
    # config lint enforces value-agreement on the shared convention
    # names with the inner ZCIS level primitive's bundled config,
    # so threading this same ToolConfig into the inner ZCIS call
    # keeps methodology consistent end-to-end on the ZCIS leg.  The
    # breakeven inner call uses its own bundled ToolConfig (loaded
    # inside compute()) so the YLD_YTM_MID default applies to the
    # nominal / linker bond reads.
    try:
        sbbs_config = load_tool_config(
            SWAP_BREAKEVEN_BASIS_SIMPLE_CONFIG_PATH,
        )
        result = calculate_swap_breakeven_basis_simple(
            engine=engine, params=params, config=sbbs_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_swap_breakeven_basis_simple_tool] "
            "unhandled error for %s - %s/%s %s",
            params.zcis_curve_family,
            params.nominal_curve_family,
            params.linker_curve_family,
            params.tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_swap_breakeven_basis_simple_tool "
                    f"failed for {params.zcis_curve_family} - "
                    f"{params.nominal_curve_family}/"
                    f"{params.linker_curve_family} "
                    f"{params.tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_swap_breakeven_basis_simple_tool] tool call "
        "complete: %s - %s/%s %s → %s",
        params.zcis_curve_family,
        params.nominal_curve_family,
        params.linker_curve_family,
        params.tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip both bespoke and canonical TimeSeries payloads before
    # returning to the LLM (frontend REST path returns the full
    # payload).  Same convention as sibling rate-level / curve-
    # spread / forward / cross-market tools.
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in ("time_series", "time_series_basis", "time_series_zscore")
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    basis_rows = len(
        result.get("time_series_basis", {}).get("rows", []) or []
    )
    zscore_rows = len(
        result.get("time_series_zscore", {}).get("rows", []) or []
    )
    if bespoke_rows or basis_rows or zscore_rows:
        logger.info(
            "[calculate_swap_breakeven_basis_simple_tool] withheld "
            "bespoke=%d basis=%d zscore=%d rows from LLM context.",
            bespoke_rows, basis_rows, zscore_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 6: calculate_inflation_swap_butterfly
# ===========================================================================
@mcp.tool()
def calculate_inflation_swap_butterfly_tool(
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the same-curve zero-coupon inflation swap (ZCIS)
    butterfly (3-point ZCIS curve curvature) between three pillars
    of the same ZCIS curve family (e.g. USD_ZCIS 5s10s30s,
    EUR_ZCIS 2s5s10s, GBP_ZCIS 2s10s30s), plus daily/weekly/monthly
    bps changes, 1-year z-score, and trailing 1Y high/low/percentile
    in bps.  Surfaces the load-bearing reference metadata
    (``inflation_index_family``, ``index_lag``, ``interpolation``,
    ``underlying_index``) shared by all three legs by the same-curve
    invariant so the desk can interpret the butterfly honestly.

    Butterfly formula (FIXED simple-butterfly weighting, weight
    tuple ``(-0.5, +1.0, -0.5)`` on ``(short, belly, long)`` in
    raw inflation-swap-rate space, then * 100 to convert to BPS):

      butterfly_bps = (belly_zcis_pct
                       - 0.5 * (short_zcis_pct + long_zcis_pct)) * 100

    Sign convention: POSITIVE = belly cheap (belly ZCIS rate HIGH
    relative to half-weighted wings); NEGATIVE = belly rich.
    Matches sovereign / real-yield / breakeven butterflies.

    Raw inflation-swap-rate space — NO subtraction of a model-
    derived basis (e.g. swap-vs-linker breakeven basis), NO
    inflation-risk-premium adjustment, NO routing through a fitted
    curve object.

    Use this tool when the user asks about:
    - ZCIS curve curvature           (e.g. "Where's USD_ZCIS
      5s10s30s curvature?")
    - ZCIS 3-point butterfly trades  (e.g. "How much has GBP
      2s10s30s ZCIS butterfly moved this month?")
    - ZCIS curvature extremes        (e.g. "Is EUR 2s5s10s ZCIS
      butterfly at a 1-year wide?")

    Do NOT use this tool for:
    - ZCIS curve spreads (e.g. USD_ZCIS 5s10s) — that's
      ``calculate_inflation_swap_curve_spread_tool``.
    - ZCIS forwards (e.g. USD_ZCIS 5Y5Y) — that's
      ``calculate_inflation_swap_forward_tool``.
    - Cross-market ZCIS spreads — that's
      ``calculate_cross_market_inflation_swap_spread_tool``.
    - Linker bond-implied breakeven butterflies — call the
      inflation_indexed_bonds agent's
      ``calculate_breakeven_butterfly_tool``.
    - Real-yield butterflies — call the inflation_indexed_bonds
      agent's ``calculate_real_yield_butterfly_tool``.
    - Nominal sovereign butterflies — call the sovereign_bonds
      agent's ``calculate_butterfly_tool``.

    Parameters
    ----------
    curve_family : str
        Inflation-swap curve identifier exactly as stored in
        instrument_master.  Examples: 'USD_ZCIS', 'EUR_ZCIS',
        'GBP_ZCIS'.  See
        ``rates_agent/playbooks/inflation_swaps.yml``.  Same-curve
        invariant: a single ``curve_family`` is shared by all three
        endpoints.
    short_tenor : str
        Short wing tenor (e.g. '2Y' for 2s5s10s, '5Y' for 5s10s30s).
        Must be a supported ZCIS pillar on this curve_family —
        current ingested grid is 1Y / 2Y / 3Y / 5Y / 10Y / 20Y /
        30Y on each of USD_ZCIS / EUR_ZCIS / GBP_ZCIS.
    belly_tenor : str
        Belly (body) tenor (e.g. '5Y' for 2s5s10s, '10Y' for
        5s10s30s).  Must be strictly between ``short_tenor`` and
        ``long_tenor`` in year fraction.
    long_tenor : str
        Long wing tenor (e.g. '10Y' for 2s5s10s, '30Y' for
        5s10s30s).  Must be strictly longer than ``belly_tenor``.
    lookback_days : int, optional
        Calendar days of displayed history (default 365).
    field_name : str, optional
        Bloomberg field mnemonic for the ZCIS rate.  Leave as the
        default empty string ""  to use the bundled
        ``default_zcis_rate_field`` convention from
        inflation_swap_butterfly/config.yaml (currently 'PX_MID').
        Mirrors the empty-string sentinel pattern used by sovereign
        / OIS / linker / ZCIS level / ZCIS curve-spread / ZCIS
        forward tools so the YAML default actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_zcis_rate_field``.  Same shadowing pattern fixed
    # for sovereign curve_move_classifier in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = InflationSwapButterflyInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            belly_tenor=belly_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_inflation_swap_butterfly_tool] input "
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
            "[calculate_inflation_swap_butterfly_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the inflation_swap_butterfly tool's bundled config
    # explicitly so the dependency is observable here.  The cross-
    # config lint enforces value-agreement on the shared convention
    # names with the inner level primitive's bundled config, so
    # threading this same ToolConfig into the inner calls keeps
    # methodology consistent end-to-end.
    try:
        isb_config = load_tool_config(
            INFLATION_SWAP_BUTTERFLY_CONFIG_PATH,
        )
        result = calculate_inflation_swap_butterfly(
            engine=engine, params=params, config=isb_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_inflation_swap_butterfly_tool] unhandled "
            "error for %s %s/%s/%s",
            params.curve_family,
            params.short_tenor,
            params.belly_tenor,
            params.long_tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_inflation_swap_butterfly_tool failed "
                    f"for {params.curve_family} "
                    f"{params.short_tenor}/{params.belly_tenor}/"
                    f"{params.long_tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_inflation_swap_butterfly_tool] tool call "
        "complete: %s %s/%s/%s → %s",
        params.curve_family,
        params.short_tenor,
        params.belly_tenor,
        params.long_tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip bespoke and canonical TimeSeries payloads before
    # returning to the LLM (frontend REST path returns the full
    # payload).  Same convention as sibling rate-level / curve-
    # spread / forward / cross-market / basis tools.
    llm_response: dict = {
        k: v for k, v in result.items()
        if k not in (
            "time_series", "time_series_butterfly", "time_series_zscore",
        )
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    butterfly_rows = len(
        result.get("time_series_butterfly", {}).get("rows", []) or []
    )
    zscore_rows = len(
        result.get("time_series_zscore", {}).get("rows", []) or []
    )
    if bespoke_rows or butterfly_rows or zscore_rows:
        logger.info(
            "[calculate_inflation_swap_butterfly_tool] withheld "
            "bespoke=%d butterfly=%d zscore=%d rows from LLM "
            "context.",
            bespoke_rows, butterfly_rows, zscore_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 7: calculate_cpi_surprise
# ===========================================================================
@mcp.tool()
def calculate_cpi_surprise_tool(
    country: str,
    lookback_releases: int = 24,
) -> str:
    """Compute the per-release CPI surprise series + rolling release-
    window z-score for one country's headline CPI YoY print.

    Surprise = ``actual − consensus_median`` per release (in
    percentage points of YoY CPI) — the exact identity ADR 0008 §2
    designates as the primitive layer's P12-disclosed computation
    (event_calendar.surprise is intentionally NULL by ingestion).

    Use this tool when the user asks about:
    - CPI surprises ("what was the latest US CPI surprise?")
    - Headline-print history around CPI ("how big were the last 6
      EU HICP surprises?")
    - CPI-surprise z-score / standardisation ("is this CPI print
      surprisingly high vs the last 2 years?")
    - Pre-/post-event read-throughs (a release-spaced complement to
      market-data primitives)

    Do NOT use this tool for:
    - NFP / payrolls surprises — that's the forthcoming
      ``calculate_nfp_surprise_tool`` (primitive 4 of the easy-win
      batch), keyed on event_type=nfp.
    - Revisions of prior CPI surprises — see methodology.planned_extensions.
    - ZCIS / inflation-swap pricing — use the inflation-swap tools.
    - Linker breakeven — call the inflation_indexed_bonds agent.

    Parameters
    ----------
    country : str
        Country / region code.  Supported: 'US', 'UK', 'JP', 'EU'.
        US/UK/JP resolve to event_type='cpi_yoy'; EU resolves to
        event_type='hicp_yoy' (the eurozone HICP equivalent of CPI YoY).
        See cpi_surprise/config.yaml's ``cpi_event_type_for_<country>``
        conventions for the full mapping.
    lookback_releases : int, optional
        Number of realised releases of trailing history to display
        (default 24 ≈ 2 years at monthly CPI cadence).  The rolling
        z-score window is always fixed at ``release_z_window``
        realised releases (24 by default), independent of this
        parameter — same display-vs-z-window separation as
        curve_spread / yield_levels' day-based primitives.
    """
    try:
        params = CpiSurpriseInput(
            country=country,
            lookback_releases=lookback_releases,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps(
            {"error": f"Invalid parameters: {exc.errors()}"}, default=str,
        )

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps(
            {"error": f"Database connection failed: {exc}"}, default=str,
        )

    # Pass the bundled config explicitly so the config dependency is
    # observable at the wiring layer (PR7 + DESIGN_PRINCIPLES §8).
    try:
        cfg = load_tool_config(CPI_SURPRISE_CONFIG_PATH)
        result = calculate_cpi_surprise(
            engine=engine, params=params, config=cfg,
        )
    except Exception as exc:
        logger.exception(
            "Unhandled error in calculate_cpi_surprise for %s",
            params.country,
        )
        return json.dumps(
            {"error": f"CPI surprise calculation failed for "
                      f"{params.country}: {exc}"},
            default=str,
        )

    logger.info("Tool call complete: calculate_cpi_surprise %s → %s",
                params.country,
                "error" if "error" in result else "OK")

    return json.dumps(result, default=str)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info(
        "Starting Inflation-Swaps Agent MCP server (stdio transport)...",
    )
    mcp.run(transport="stdio")

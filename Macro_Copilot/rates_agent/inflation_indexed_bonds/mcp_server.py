"""
mcp_server.py — MCP Server for the Inflation-Indexed-Bonds Sub-Agent
=====================================================================

Exposes the linker-domain tools to the orchestrator via stdio MCP.
Each tool has flat scalar parameters; Pydantic validation happens
inside.  Lazy engine singleton.  Logging to stderr.

Tool surface
------------
1. get_real_yield_level_tool — single-point linker real-yield level,
   period changes, 252-day z-score, trailing 1Y high/low/percentile.
2. calculate_breakeven_inflation_simple_tool — generic bond-implied
   breakeven inflation (nominal-minus-real yield differential at the
   same tenor); inflation compensation, NOT a clean expected-inflation
   read.
3. calculate_forward_breakeven_simple_tool — forward bond-implied
   breakeven inflation between two same-country curve points
   (e.g. 5Y5Y, 5Y10Y, 2Y3Y) via the year-weighted linear formula on
   the two endpoint spot breakevens; forward inflation compensation,
   NOT a clean forward expected-inflation read.

Subsequent linker primitives (real_yield_curve_spread,
scanner_linkers, etc., per
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
    BreakevenInflationSimpleInput,
    ForwardBreakevenSimpleInput,
    RealYieldLevelInput,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (  # noqa: E402
    CONFIG_PATH as BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (  # noqa: E402
    CONFIG_PATH as FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH,
    calculate_forward_breakeven_simple,
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
        "answer questions about real-yield levels, bond-implied "
        "breakeven inflation (the nominal-minus-real yield "
        "differential, a.k.a. inflation compensation), AND forward "
        "bond-implied breakevens between two same-country curve "
        "points (e.g. 5Y5Y, 5Y10Y).  Future releases will add "
        "real-yield curve spreads and inflation-compensation "
        "scanners.  Never "
        "attempt the math yourself — always call a tool and relay its "
        "output.  Do not route nominal sovereign yield questions here "
        "— those belong to the sovereign-bond agent's "
        "get_yield_levels_tool.  When relaying breakeven output, "
        "always preserve the methodology disclosure: bond-implied "
        "breakeven is inflation compensation, not a clean expected-"
        "inflation read (it carries an inflation risk premium and a "
        "liquidity premium between the nominal and linker bond)."
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
# TOOL 2: calculate_breakeven_inflation_simple
# ===========================================================================
@mcp.tool()
def calculate_breakeven_inflation_simple_tool(
    nominal_curve_family: str,
    linker_curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current generic bond-implied breakeven inflation between
    a nominal sovereign curve and the corresponding sovereign linker
    curve at the same tenor, plus period changes, 1-year z-score, and
    deterministic historical context (high, low, percentile in bps).

        breakeven_pct = nominal_yield_pct - real_yield_pct
        breakeven_bps = breakeven_pct * 100

    The output is INFLATION COMPENSATION, NOT a clean expected-inflation
    read — the differential carries an inflation risk premium and a
    relative liquidity premium between the nominal sovereign and the
    linker bond.  The tool's output includes an explicit
    ``methodology_label`` field carrying that disclosure; preserve it
    when summarising the result to the user.

    Use this tool when the user asks about:
    - Breakeven inflation levels  (e.g. "Where's US 10Y breakeven?")
    - Breakeven moves              (e.g. "How much has UK 10Y breakeven
      moved this week?")
    - Breakeven extremes           (e.g. "Is US 5Y breakeven at a 1-year
      high?")
    - Decomposing inflation compensation (this tool returns BOTH the
      breakeven and the two underlying yields used to form it).

    Do NOT use this tool for:
    - Pure expected-inflation reads — bond-implied breakevens are
      compensation, not expectations.  An IRP- and liquidity-adjusted
      variant is documented in the tool's
      ``methodology.planned_extensions`` and will ship as a separate
      primitive when the required metadata lands.
    - Inflation-swap-implied breakevens (different instrument, not yet
      ingested).
    - Real-yield-only or nominal-yield-only questions — call the
      respective single-leg level tools (``get_real_yield_level_tool``
      here, ``get_yield_levels_tool`` on the sovereign-bond agent).

    Parameters
    ----------
    nominal_curve_family : str
        Nominal sovereign curve identifier exactly as stored in
        instrument_master.  Examples: 'UST' (US), 'UK_GILT' (UK),
        'FR_OAT' (France), 'CANADA_GOVT' (Canada).  This leg is
        filtered honestly with instrument_type='sovereign_benchmark'
        on the DB read; passing a linker curve here returns a
        controlled error envelope (the tool will not silently
        substitute linker data).
    linker_curve_family : str
        Sovereign linker curve identifier exactly as stored in
        instrument_master.  Examples: 'USD_TIPS' (US), 'GBP_LINKER'
        (UK), 'EUR_FR_LINKER' (France), 'CAD_RRB' (Canada).  This leg
        is filtered honestly with instrument_type='inflation_linker'
        on the DB read; passing a nominal sovereign curve here returns
        a controlled error envelope.
    tenor : str
        Tenor point on both curves.  Available tenor intersections are
        country-specific (e.g. UST + USD_TIPS overlap at
        5Y/10Y/20Y/30Y; UK_GILT + GBP_LINKER overlap across a wide
        range from 1Y to 50Y).
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does NOT
        control the rolling z-score window or the trailing range
        window.
    field_name : str, optional
        Bloomberg field mnemonic for BOTH legs.  Leave as the default
        empty string ""  to use the bundled ``default_field_name``
        convention from breakeven_inflation_simple/config.yaml
        (currently 'YLD_YTM_MID').  Pass an explicit field name to
        override per call.  Mirrors the empty-string sentinel pattern
        used by the other rates tools so the YAML default actually
        flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's ``default_field_name``.
    field_name_arg = field_name if field_name else None
    try:
        params = BreakevenInflationSimpleInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_breakeven_inflation_simple_tool] input validation failed: %s",
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
            "[calculate_breakeven_inflation_simple_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the breakeven_inflation_simple tool's bundled config
    # explicitly so the dependency is observable here.
    # load_tool_config caches by path, so this is a free lookup after
    # the first call within the MCP subprocess's lifetime.  Mirrors
    # the get_real_yield_level_tool wrapper exactly.
    try:
        bei_config = load_tool_config(BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH)
        result = calculate_breakeven_inflation_simple(
            engine=engine, params=params, config=bei_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_breakeven_inflation_simple_tool] unhandled "
            "error for %s vs %s @ %s",
            params.nominal_curve_family,
            params.linker_curve_family,
            params.tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_breakeven_inflation_simple_tool failed "
                    f"for {params.nominal_curve_family} vs "
                    f"{params.linker_curve_family} @ "
                    f"{params.tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_breakeven_inflation_simple_tool] tool call "
        "complete: %s vs %s @ %s → %s",
        params.nominal_curve_family,
        params.linker_curve_family,
        params.tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip the per-row time series fields before returning to the LLM
    # (frontend REST path returns the full payload).  The LLM doesn't
    # need every historical row to answer "where's US 10Y breakeven?".
    # Same convention as the other rates tools.
    stripped_keys = {"time_series", "time_series_breakeven", "time_series_zscore"}
    llm_response: dict = {
        k: v for k, v in result.items() if k not in stripped_keys
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    canonical_rows = len(
        result.get("time_series_breakeven", {}).get("rows", []) or []
    )
    if bespoke_rows or canonical_rows:
        logger.info(
            "[calculate_breakeven_inflation_simple_tool] withheld "
            "%d bespoke + %d canonical breakeven rows + %d zscore "
            "rows from LLM context.",
            bespoke_rows,
            canonical_rows,
            len(result.get("time_series_zscore", {}).get("rows", []) or []),
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 3: calculate_forward_breakeven_simple
# ===========================================================================
@mcp.tool()
def calculate_forward_breakeven_simple_tool(
    nominal_curve_family: str,
    linker_curve_family: str,
    start_tenor: str,
    end_tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current forward bond-implied breakeven inflation between
    two same-country curve points (e.g. 5Y5Y, 5Y10Y, 2Y3Y), plus
    period changes, 1-year z-score, and deterministic historical
    context (high, low, percentile in bps).

        forward_breakeven_bps =
            (BE_long_bps * T_long - BE_short_bps * T_short)
            / (T_long - T_short)

    where BE_short / BE_long are the spot bond-implied breakevens at
    start_tenor / end_tenor (each computed by the same desk-recognised
    spot-breakeven primitive) and T_short / T_long are the calendar
    year fractions of the two endpoint tenors.

    The output is FORWARD INFLATION COMPENSATION, NOT a clean forward
    expected-inflation read — each endpoint breakeven carries an
    inflation risk premium and a relative liquidity premium between
    the nominal sovereign and the linker, and the year-weighted
    forward inherits both.  The tool's output includes an explicit
    ``methodology_label`` field carrying that disclosure plus the
    literal year-weighted formula; preserve it when summarising the
    result to the user.

    Use this tool when the user asks about:
    - Forward breakeven inflation levels (e.g. "Where's the US 5Y5Y
      breakeven?")
    - Forward breakeven moves            (e.g. "How much has the UK
      5Y5Y breakeven moved this week?")
    - Forward breakeven extremes          (e.g. "Is the FR 5Y10Y
      breakeven at a 1-year high?")
    - Decomposing a forward breakeven    (this tool returns BOTH the
      forward AND the two endpoint breakevens + year fractions used
      to form it).

    Do NOT use this tool for:
    - Spot bond-implied breakevens — call
      ``calculate_breakeven_inflation_simple_tool``.
    - Pure forward expected-inflation reads — bond-implied forward
      breakevens are forward compensation, not expectations.
    - Forward inflation-swap-implied breakevens (different
      instrument, not yet ingested).
    - Cross-country breakeven comparisons — the tool refuses cross-
      country pairs (e.g. DE_BUND vs EUR_FR_LINKER, or UK_GILT vs
      USD_TIPS) at compute time with a controlled error envelope.

    Parameters
    ----------
    nominal_curve_family : str
        Nominal sovereign curve identifier exactly as stored in
        instrument_master.  Examples: 'UST', 'UK_GILT', 'FR_OAT',
        'CANADA_GOVT'.  This leg is filtered honestly with
        instrument_type='sovereign_benchmark' on the DB read at BOTH
        endpoint tenors; passing a linker curve here returns a
        controlled error envelope.
    linker_curve_family : str
        Sovereign linker curve identifier exactly as stored in
        instrument_master.  Examples: 'USD_TIPS', 'GBP_LINKER',
        'EUR_FR_LINKER', 'CAD_RRB'.  This leg is filtered honestly
        with instrument_type='inflation_linker' on the DB read at
        BOTH endpoint tenors.
    start_tenor : str
        Start tenor of the forward window — e.g. '5Y' for 5Y5Y, '2Y'
        for 2Y3Y.  Must be present on BOTH curves at the requested
        country.
    end_tenor : str
        End tenor of the forward window — e.g. '10Y' for 5Y5Y, '3Y'
        for 2Y3Y.  Must be strictly longer than start_tenor.
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does NOT
        control the rolling z-score window or the trailing range
        window.
    field_name : str, optional
        Bloomberg field mnemonic for ALL FOUR underlying yield
        series (nominal at start_tenor, linker at start_tenor,
        nominal at end_tenor, linker at end_tenor).  Leave as the
        default empty string ""  to use the bundled
        ``default_field_name`` convention from
        forward_breakeven_simple/config.yaml (currently
        'YLD_YTM_MID').  Pass an explicit field name to override per
        call.  Mirrors the empty-string sentinel pattern used by the
        other rates tools so the YAML default actually flows
        through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's ``default_field_name``.
    field_name_arg = field_name if field_name else None
    try:
        params = ForwardBreakevenSimpleInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            start_tenor=start_tenor,
            end_tenor=end_tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_forward_breakeven_simple_tool] input validation failed: %s",
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
            "[calculate_forward_breakeven_simple_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the forward_breakeven_simple tool's bundled config
    # explicitly so the dependency is observable here.
    # load_tool_config caches by path, so this is a free lookup
    # after the first call within the MCP subprocess's lifetime.
    # Mirrors the calculate_breakeven_inflation_simple_tool wrapper
    # exactly.
    try:
        fb_config = load_tool_config(FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH)
        result = calculate_forward_breakeven_simple(
            engine=engine, params=params, config=fb_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_forward_breakeven_simple_tool] unhandled "
            "error for %s vs %s @ %s%s",
            params.nominal_curve_family,
            params.linker_curve_family,
            params.start_tenor,
            params.end_tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_forward_breakeven_simple_tool failed "
                    f"for {params.nominal_curve_family} vs "
                    f"{params.linker_curve_family} @ "
                    f"{params.start_tenor}{params.end_tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_forward_breakeven_simple_tool] tool call "
        "complete: %s vs %s @ %s%s → %s",
        params.nominal_curve_family,
        params.linker_curve_family,
        params.start_tenor,
        params.end_tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip the per-row time series fields before returning to the
    # LLM (frontend REST path returns the full payload).  Same
    # convention as the other rates tools — the LLM doesn't need
    # every historical row to answer "where's US 5Y5Y breakeven?".
    stripped_keys = {"time_series", "time_series_forward", "time_series_zscore"}
    llm_response: dict = {
        k: v for k, v in result.items() if k not in stripped_keys
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    canonical_rows = len(
        result.get("time_series_forward", {}).get("rows", []) or []
    )
    if bespoke_rows or canonical_rows:
        logger.info(
            "[calculate_forward_breakeven_simple_tool] withheld "
            "%d bespoke + %d canonical forward rows + %d zscore "
            "rows from LLM context.",
            bespoke_rows,
            canonical_rows,
            len(result.get("time_series_zscore", {}).get("rows", []) or []),
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

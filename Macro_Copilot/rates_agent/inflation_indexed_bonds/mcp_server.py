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
4. calculate_breakeven_curve_spread_tool — same-country breakeven
   curve spread between two breakeven tenors of the same
   nominal/linker pair (e.g. UST/USD_TIPS 2s10s breakeven,
   UK_GILT/GBP_LINKER 5s30s breakeven); the inflation-compensation
   term-structure object, NOT the term structure of pure expected
   inflation.
5. calculate_cross_country_breakeven_spread_simple_tool — same-tenor
   cross-country breakeven inflation differential (e.g. US 10Y
   breakeven minus EUR-FR 10Y breakeven); cross-country inflation-
   compensation differential, NOT a pure cross-country expected-
   inflation differential, AND subject to an INDEX-FAMILY MISMATCH
   caveat (e.g. CPI-U vs HICP).
6. calculate_real_yield_curve_spread_tool — same-country linker
   real-yield curve spread between two real-yield tenors of the
   same sovereign linker curve (e.g. USD_TIPS 5s10s real-yield,
   GBP_LINKER 2s10s real-yield); the real-yield curve-shape
   object, distinct from a breakeven curve spread (inflation
   compensation) and from a nominal sovereign curve spread.
7. calculate_cross_country_real_yield_spread_simple_tool — same-
   tenor cross-country linker real-yield differential (e.g.
   USD_TIPS 10Y real yield minus GBP_LINKER 10Y real yield);
   cross-country real-rate divergence object, distinct from a
   cross-country breakeven differential (inflation compensation)
   and from a sovereign nominal cross-market spread.  Subject to
   INDEX-FAMILY MISMATCH and MARKET-STRUCTURE MISMATCH caveats
   (CPI-U vs HICP vs RPI vs CAN_CPI; cross-country linker-
   liquidity / issuance-size differences).

Subsequent linker primitives (scanner_linkers, etc., per
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
    BreakevenCurveSpreadInput,
    BreakevenInflationSimpleInput,
    CrossCountryBreakevenSpreadSimpleInput,
    CrossCountryRealYieldSpreadSimpleInput,
    ForwardBreakevenSimpleInput,
    RealYieldCurveSpreadInput,
    RealYieldLevelInput,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (  # noqa: E402
    CONFIG_PATH as BREAKEVEN_CURVE_SPREAD_CONFIG_PATH,
    calculate_breakeven_curve_spread,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (  # noqa: E402
    CONFIG_PATH as BREAKEVEN_INFLATION_SIMPLE_CONFIG_PATH,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (  # noqa: E402
    CONFIG_PATH as CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
    calculate_cross_country_breakeven_spread_simple,
)
from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (  # noqa: E402
    CONFIG_PATH as CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE_CONFIG_PATH,
    calculate_cross_country_real_yield_spread_simple,
)
from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (  # noqa: E402
    CONFIG_PATH as FORWARD_BREAKEVEN_SIMPLE_CONFIG_PATH,
    calculate_forward_breakeven_simple,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (  # noqa: E402
    CONFIG_PATH as REAL_YIELD_CURVE_SPREAD_CONFIG_PATH,
    calculate_real_yield_curve_spread,
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
        "differential, a.k.a. inflation compensation), forward "
        "bond-implied breakevens between two same-country curve "
        "points (e.g. 5Y5Y, 5Y10Y), same-country breakeven curve "
        "spreads (e.g. 2s10s breakeven, 5s30s breakeven — the "
        "inflation-compensation term-structure object), AND "
        "same-tenor cross-country breakeven differentials (e.g. "
        "US 10Y breakeven vs EUR-FR 10Y breakeven — subject to an "
        "INDEX-FAMILY MISMATCH caveat between CPI-U / HICP / RPI / "
        "etc.), AND same-tenor cross-country linker real-yield "
        "differentials (e.g. USD_TIPS 10Y real yield vs GBP_LINKER "
        "10Y real yield — the cross-country real-rate divergence "
        "object, distinct from cross-country breakeven and from "
        "sovereign nominal cross-market spread; subject to the same "
        "index-family caveat plus a market-structure mismatch "
        "between linker markets).  Future releases will add "
        "inflation-compensation scanners.  Never attempt the math "
        "yourself — always call a "
        "tool and relay its output.  Do not route nominal sovereign "
        "yield questions here — those belong to the sovereign-bond "
        "agent's get_yield_levels_tool.  When relaying breakeven "
        "output, always preserve the methodology disclosure: bond-"
        "implied breakeven is inflation compensation, not a clean "
        "expected-inflation read (it carries an inflation risk "
        "premium and a liquidity premium between the nominal and "
        "linker bond); cross-country breakeven differentials carry "
        "the additional index-family mismatch caveat."
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
# TOOL 4: calculate_breakeven_curve_spread
# ===========================================================================
@mcp.tool()
def calculate_breakeven_curve_spread_tool(
    nominal_curve_family: str,
    linker_curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current same-country breakeven curve spread between
    two breakeven tenors of the same nominal/linker pair (e.g.
    UST/USD_TIPS 2s10s breakeven, UK_GILT/GBP_LINKER 5s30s
    breakeven), plus period changes, 1-year z-score, and
    deterministic historical context (high, low, percentile in bps).

        spread_bps = long_breakeven_bps - short_breakeven_bps

    where short_breakeven / long_breakeven are the spot bond-
    implied breakevens at short_tenor / long_tenor (each computed
    by the same desk-recognised spot-breakeven primitive).

    The output is the term structure of INFLATION COMPENSATION,
    NOT the term structure of pure expected inflation — each
    endpoint breakeven carries an inflation risk premium and a
    relative liquidity premium between the nominal sovereign and
    the linker, and the curve spread inherits both at each
    endpoint.  The tool's output includes an explicit
    ``methodology_label`` field carrying that disclosure plus the
    literal spread formula; preserve it when summarising the
    result to the user.

    Use this tool when the user asks about:
    - Breakeven curve shape / steepness (e.g. "Where's the US
      2s10s breakeven?", "Is the UK 5s30s breakeven flat?")
    - Breakeven curve moves              (e.g. "How much has
      the FR 2s10s breakeven steepened this week?")
    - Breakeven curve extremes          (e.g. "Is the US
      5s30s breakeven at a 1-year low?")
    - Decomposing a curve-spread move    (this tool returns BOTH
      the spread AND the two endpoint breakevens + year fractions
      used to form it).

    Do NOT use this tool for:
    - Spot bond-implied breakevens — call
      ``calculate_breakeven_inflation_simple_tool``.
    - Forward bond-implied breakevens (5Y5Y / 5Y10Y / 2Y3Y) —
      call ``calculate_forward_breakeven_simple_tool``.
    - Pure expected-inflation term-structure reads — bond-implied
      breakeven curves are the term structure of compensation,
      not expectations.
    - Cross-country breakeven comparisons — the tool refuses
      cross-country pairs (e.g. DE_BUND vs EUR_FR_LINKER, or
      UK_GILT vs USD_TIPS) at compute time with a controlled
      error envelope.
    - Sovereign nominal curve spreads — those belong to the
      sovereign-bond agent's ``calculate_curve_spread_tool``.

    Parameters
    ----------
    nominal_curve_family : str
        Nominal sovereign curve identifier exactly as stored in
        instrument_master.  Examples: 'UST', 'UK_GILT', 'FR_OAT',
        'CANADA_GOVT'.  This leg is filtered honestly with
        instrument_type='sovereign_benchmark' on the DB read at
        BOTH endpoint tenors; passing a linker curve here returns
        a controlled error envelope.
    linker_curve_family : str
        Sovereign linker curve identifier exactly as stored in
        instrument_master.  Examples: 'USD_TIPS', 'GBP_LINKER',
        'EUR_FR_LINKER', 'CAD_RRB'.  This leg is filtered
        honestly with instrument_type='inflation_linker' on the
        DB read at BOTH endpoint tenors.
    short_tenor : str
        Short tenor of the curve spread — e.g. '2Y' for 2s10s,
        '5Y' for 5s30s.  Must be present on BOTH curves at the
        requested country.
    long_tenor : str
        Long tenor of the curve spread — e.g. '10Y' for 2s10s,
        '30Y' for 5s30s.  Must be strictly longer than
        ``short_tenor``.
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does
        NOT control the rolling z-score window or the trailing
        range window.
    field_name : str, optional
        Bloomberg field mnemonic for ALL FOUR underlying yield
        series (nominal at short_tenor, linker at short_tenor,
        nominal at long_tenor, linker at long_tenor).  Leave as
        the default empty string ""  to use the bundled
        ``default_field_name`` convention from
        breakeven_curve_spread/config.yaml (currently
        'YLD_YTM_MID').  Pass an explicit field name to override
        per call.  Mirrors the empty-string sentinel pattern used
        by the other rates tools so the YAML default actually
        flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_field_name``.
    field_name_arg = field_name if field_name else None
    try:
        params = BreakevenCurveSpreadInput(
            nominal_curve_family=nominal_curve_family,
            linker_curve_family=linker_curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_breakeven_curve_spread_tool] input validation failed: %s",
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
            "[calculate_breakeven_curve_spread_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the breakeven_curve_spread tool's bundled config
    # explicitly so the dependency is observable here.
    # load_tool_config caches by path, so this is a free lookup
    # after the first call within the MCP subprocess's lifetime.
    # Mirrors the calculate_forward_breakeven_simple_tool wrapper
    # exactly.
    try:
        bcs_config = load_tool_config(BREAKEVEN_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_breakeven_curve_spread(
            engine=engine, params=params, config=bcs_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_breakeven_curve_spread_tool] unhandled "
            "error for %s vs %s @ %s%s",
            params.nominal_curve_family,
            params.linker_curve_family,
            params.short_tenor,
            params.long_tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_breakeven_curve_spread_tool failed "
                    f"for {params.nominal_curve_family} vs "
                    f"{params.linker_curve_family} @ "
                    f"{params.short_tenor}{params.long_tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_breakeven_curve_spread_tool] tool call "
        "complete: %s vs %s @ %s%s → %s",
        params.nominal_curve_family,
        params.linker_curve_family,
        params.short_tenor,
        params.long_tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip the per-row time series fields before returning to
    # the LLM (frontend REST path returns the full payload).
    # Same convention as the other rates tools — the LLM doesn't
    # need every historical row to answer "where's US 2s10s
    # breakeven?".
    stripped_keys = {"time_series", "time_series_spread", "time_series_zscore"}
    llm_response: dict = {
        k: v for k, v in result.items() if k not in stripped_keys
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    canonical_rows = len(
        result.get("time_series_spread", {}).get("rows", []) or []
    )
    if bespoke_rows or canonical_rows:
        logger.info(
            "[calculate_breakeven_curve_spread_tool] withheld "
            "%d bespoke + %d canonical spread rows + %d zscore "
            "rows from LLM context.",
            bespoke_rows,
            canonical_rows,
            len(result.get("time_series_zscore", {}).get("rows", []) or []),
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 5: calculate_cross_country_breakeven_spread_simple
# ===========================================================================
@mcp.tool()
def calculate_cross_country_breakeven_spread_simple_tool(
    country_a_nominal_pair: str,
    country_a_linker_pair: str,
    country_b_nominal_pair: str,
    country_b_linker_pair: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current same-tenor cross-country breakeven inflation
    differential between two countries' generic bond-implied
    breakevens (e.g. US 10Y breakeven minus EUR-FR 10Y breakeven,
    US 5Y breakeven minus UK 5Y breakeven), plus period changes,
    1-year z-score, and deterministic historical context (high,
    low, percentile in bps).

        spread_bps = breakeven_a_bps - breakeven_b_bps

    where breakeven_a / breakeven_b are the spot bond-implied
    breakevens at the same tenor for country_a / country_b
    respectively (each computed by the same desk-recognised
    spot-breakeven primitive constrained to that country's own
    nominal/linker pair).  Sign convention is country_a minus
    country_b — fixed.

    The output is a CROSS-COUNTRY INFLATION-COMPENSATION
    DIFFERENTIAL, NOT a pure cross-country expected-inflation
    differential.  Two load-bearing caveats:

      1. Each leg inherits the spot breakeven primitive's
         'inflation compensation, NOT pure expected inflation'
         caveat (each leg carries an inflation risk premium and a
         relative liquidity premium between its nominal sovereign
         and its linker).
      2. INDEX-FAMILY MISMATCH: different countries' linkers
         reference different inflation indices (USD CPI-U non-
         seasonally adjusted vs euro-area HICP ex-tobacco vs UK
         RPI/CPIH vs Canada CPI).  These are NOT identical
         inflation references — interpret a US 10Y breakeven minus
         EUR-FR 10Y breakeven as a CPI-U-vs-HICP differential, not
         a pure expected-inflation differential.

    The tool's output includes an explicit ``methodology_label``
    field carrying both caveats; preserve it when summarising the
    result to the user.

    Use this tool when the user asks about:
    - Cross-country breakeven differentials (e.g. "Where's US-EUR
      10Y breakeven?", "Is UK-US 5Y breakeven wide?")
    - Cross-country breakeven moves         (e.g. "How much has
      the US-EUR 10Y breakeven moved this week?")
    - Cross-country breakeven extremes      (e.g. "Is the US-UK
      5Y breakeven at a 1-year high?")
    - Decomposing a cross-country breakeven (this tool returns
      BOTH the differential AND the two underlying country
      breakevens used to form it).

    Do NOT use this tool for:
    - Same-country breakeven curve / spot work — call
      ``calculate_breakeven_curve_spread_tool`` or
      ``calculate_breakeven_inflation_simple_tool``.
    - Forward bond-implied breakevens (5Y5Y, 5Y10Y, 2Y3Y) — call
      ``calculate_forward_breakeven_simple_tool``.
    - Currency-hedged or FX-adjusted variants — this primitive is
      raw nominal differentials only.  A hedged variant ships as
      a separate primitive when FX-forward / cross-currency basis
      metadata lands.
    - Inflation-swap-based cross-country differentials — those
      ship as a separate primitive when the inflation-swap
      instrument family is ingested.
    - Sovereign nominal cross-market spreads — those belong to
      the sovereign-bond agent's
      ``calculate_cross_market_spread_tool``.

    Parameters
    ----------
    country_a_nominal_pair : str
        Country A's nominal sovereign curve identifier exactly as
        stored in instrument_master.  Examples: 'UST', 'UK_GILT',
        'FR_OAT', 'DE_BUND', 'CANADA_GOVT'.  Must pair with
        ``country_a_linker_pair`` under the same-country invariant
        enforced inside the spot breakeven primitive.
    country_a_linker_pair : str
        Country A's sovereign linker curve identifier exactly as
        stored in instrument_master.  Examples: 'USD_TIPS',
        'GBP_LINKER', 'EUR_FR_LINKER', 'CAD_RRB'.  Must share
        country AND currency with ``country_a_nominal_pair``.
    country_b_nominal_pair : str
        Country B's nominal sovereign curve identifier — MUST be
        a different sovereign issuer from
        ``country_a_nominal_pair`` (this primitive is a cross-
        country object by construction).
    country_b_linker_pair : str
        Country B's sovereign linker curve identifier — MUST be
        different from ``country_a_linker_pair`` and must pair
        with ``country_b_nominal_pair`` under the same-country
        invariant.
    tenor : str
        Single tenor applied to BOTH country legs (e.g. '5Y',
        '10Y', '30Y').  Must exist on BOTH country pairs at the
        requested country/currency.
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does
        NOT control the rolling z-score window or the trailing
        range window.
    field_name : str, optional
        Bloomberg field mnemonic for ALL FOUR underlying yield
        series (country_a_nominal at tenor, country_a_linker at
        tenor, country_b_nominal at tenor, country_b_linker at
        tenor).  Leave as the default empty string ""  to use the
        bundled ``default_field_name`` convention from
        cross_country_breakeven_spread_simple/config.yaml
        (currently 'YLD_YTM_MID').  Pass an explicit field name
        to override per call.  Mirrors the empty-string sentinel
        pattern used by the other rates tools so the YAML default
        actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_field_name``.
    field_name_arg = field_name if field_name else None
    try:
        params = CrossCountryBreakevenSpreadSimpleInput(
            country_a_nominal_pair=country_a_nominal_pair,
            country_a_linker_pair=country_a_linker_pair,
            country_b_nominal_pair=country_b_nominal_pair,
            country_b_linker_pair=country_b_linker_pair,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_cross_country_breakeven_spread_simple_tool] "
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
            "[calculate_cross_country_breakeven_spread_simple_tool] "
            "failed to connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the cross_country_breakeven_spread_simple tool's bundled
    # config explicitly so the dependency is observable here.
    # load_tool_config caches by path, so this is a free lookup
    # after the first call within the MCP subprocess's lifetime.
    # Mirrors the calculate_breakeven_curve_spread_tool wrapper
    # exactly.
    try:
        xcbs_config = load_tool_config(
            CROSS_COUNTRY_BREAKEVEN_SPREAD_SIMPLE_CONFIG_PATH,
        )
        result = calculate_cross_country_breakeven_spread_simple(
            engine=engine, params=params, config=xcbs_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_cross_country_breakeven_spread_simple_tool] "
            "unhandled error for %s/%s vs %s/%s @ %s",
            params.country_a_nominal_pair,
            params.country_a_linker_pair,
            params.country_b_nominal_pair,
            params.country_b_linker_pair,
            params.tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_cross_country_breakeven_spread_simple_tool "
                    f"failed for {params.country_a_nominal_pair}/"
                    f"{params.country_a_linker_pair} vs "
                    f"{params.country_b_nominal_pair}/"
                    f"{params.country_b_linker_pair} @ "
                    f"{params.tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_cross_country_breakeven_spread_simple_tool] "
        "tool call complete: %s/%s vs %s/%s @ %s → %s",
        params.country_a_nominal_pair,
        params.country_a_linker_pair,
        params.country_b_nominal_pair,
        params.country_b_linker_pair,
        params.tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip the per-row time series fields before returning to the
    # LLM (frontend REST path returns the full payload).  Same
    # convention as the other rates tools — the LLM doesn't need
    # every historical row to answer "where's US-EUR 10Y
    # breakeven?".
    stripped_keys = {"time_series", "time_series_spread", "time_series_zscore"}
    llm_response: dict = {
        k: v for k, v in result.items() if k not in stripped_keys
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    canonical_rows = len(
        result.get("time_series_spread", {}).get("rows", []) or []
    )
    if bespoke_rows or canonical_rows:
        logger.info(
            "[calculate_cross_country_breakeven_spread_simple_tool] "
            "withheld %d bespoke + %d canonical spread rows + %d "
            "zscore rows from LLM context.",
            bespoke_rows,
            canonical_rows,
            len(result.get("time_series_zscore", {}).get("rows", []) or []),
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 6: calculate_real_yield_curve_spread
# ===========================================================================
@mcp.tool()
def calculate_real_yield_curve_spread_tool(
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current same-country linker real-yield curve spread
    between two real-yield tenors of the same sovereign linker
    curve (e.g. USD_TIPS 5s10s real-yield, GBP_LINKER 2s10s
    real-yield, EUR_FR_LINKER 5s10s real-yield, CAD_RRB 5s30s
    real-yield), plus period changes (in bps), 1-year z-score,
    and deterministic historical context (high, low, percentile
    in PERCENT).

        spread_pct = long_real_yield_pct - short_real_yield_pct

    where short_real_yield / long_real_yield are the linker real-
    yield levels at short_tenor / long_tenor (each computed by
    the same desk-recognised real-yield-level primitive).  Real
    yields are quoted in PERCENT and the spread is reported in
    PERCENT — same units as the underlying — NOT in BPS.

    The output is the term structure of REAL YIELDS — distinct
    from a breakeven curve spread (inflation compensation) and
    from a nominal sovereign curve spread.  The tool's output
    includes an explicit ``methodology_label`` field carrying that
    framing plus the literal spread formula; preserve it when
    summarising the result to the user.

    Use this tool when the user asks about:
    - Linker real-yield curve shape / steepness (e.g. "Where's
      the TIPS 5s10s real-yield curve?", "Is the UK 5s30s
      real-yield curve flat?")
    - Real-yield curve moves              (e.g. "How much has
      the FR 2s10s real-yield curve steepened this week?")
    - Real-yield curve extremes          (e.g. "Is the US
      5s30s real-yield curve at a 1-year low?")
    - Decomposing a real-yield curve move (this tool returns
      BOTH the spread AND the two endpoint real yields + year
      fractions used to form it).

    Do NOT use this tool for:
    - Nominal sovereign curve spreads — those belong to the
      sovereign-bond agent's ``calculate_curve_spread_tool``.
    - Breakeven curve spreads (2s10s breakeven, 5s30s
      breakeven) — call ``calculate_breakeven_curve_spread_tool``.
    - Forward breakeven inflation (5Y5Y / 5Y10Y / 2Y3Y) — call
      ``calculate_forward_breakeven_simple_tool``.
    - Cross-country real-yield comparisons — this primitive
      accepts a single linker ``curve_family`` and refuses any
      non-linker curve_family with a controlled error envelope.
    - Real-yield level (single tenor) — call
      ``get_real_yield_level_tool``.

    Parameters
    ----------
    curve_family : str
        Linker curve identifier exactly as stored in
        instrument_master.  Examples: 'USD_TIPS', 'GBP_LINKER',
        'EUR_FR_LINKER', 'CAD_RRB' (see
        rates_agent/playbooks/inflation_indexed_bonds.yml for the
        ingested universe).  Non-linker curve_families (e.g.
        nominal sovereign 'UST', 'DE_BUND') are refused at
        compute time with a controlled error envelope.
    short_tenor : str
        Short tenor of the real-yield curve spread — e.g. '2Y'
        for 2s10s, '5Y' for 5s30s.  Must be a supported pillar
        on this linker curve_family.
    long_tenor : str
        Long tenor of the real-yield curve spread — e.g. '10Y'
        for 2s10s, '30Y' for 5s30s.  Must be strictly longer
        than ``short_tenor``.
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does
        NOT control the rolling z-score window or the trailing
        range window.
    field_name : str, optional
        Bloomberg field mnemonic for BOTH underlying real-yield
        series (short_tenor and long_tenor on this linker
        curve_family).  Leave as the default empty string ""  to
        use the bundled ``default_field_name`` convention from
        real_yield_curve_spread/config.yaml (currently
        'YLD_YTM_MID').  Pass an explicit field name to override
        per call.  Mirrors the empty-string sentinel pattern used
        by the other rates tools so the YAML default actually
        flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_field_name``.
    field_name_arg = field_name if field_name else None
    try:
        params = RealYieldCurveSpreadInput(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_real_yield_curve_spread_tool] input validation failed: %s",
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
            "[calculate_real_yield_curve_spread_tool] failed to "
            "connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the real_yield_curve_spread tool's bundled config
    # explicitly so the dependency is observable here.
    # load_tool_config caches by path, so this is a free lookup
    # after the first call within the MCP subprocess's lifetime.
    # Mirrors the calculate_breakeven_curve_spread_tool wrapper
    # exactly.
    try:
        rycs_config = load_tool_config(REAL_YIELD_CURVE_SPREAD_CONFIG_PATH)
        result = calculate_real_yield_curve_spread(
            engine=engine, params=params, config=rycs_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_real_yield_curve_spread_tool] unhandled "
            "error for %s @ %s%s",
            params.curve_family,
            params.short_tenor,
            params.long_tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_real_yield_curve_spread_tool failed "
                    f"for {params.curve_family} @ "
                    f"{params.short_tenor}{params.long_tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_real_yield_curve_spread_tool] tool call "
        "complete: %s @ %s%s → %s",
        params.curve_family,
        params.short_tenor,
        params.long_tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip the per-row time series fields before returning to the
    # LLM (frontend REST path returns the full payload).  Same
    # convention as the other rates tools — the LLM doesn't need
    # every historical row to answer "where's the TIPS 5s10s
    # real-yield curve?".
    stripped_keys = {"time_series", "time_series_spread", "time_series_zscore"}
    llm_response: dict = {
        k: v for k, v in result.items() if k not in stripped_keys
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    canonical_rows = len(
        result.get("time_series_spread", {}).get("rows", []) or []
    )
    if bespoke_rows or canonical_rows:
        logger.info(
            "[calculate_real_yield_curve_spread_tool] withheld "
            "%d bespoke + %d canonical spread rows + %d zscore "
            "rows from LLM context.",
            bespoke_rows,
            canonical_rows,
            len(result.get("time_series_zscore", {}).get("rows", []) or []),
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 7: calculate_cross_country_real_yield_spread_simple
# ===========================================================================
@mcp.tool()
def calculate_cross_country_real_yield_spread_simple_tool(
    first_curve_family: str,
    second_curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Get the current same-tenor cross-country linker real-yield
    differential between two linker curves' real-yield levels at
    the same tenor (e.g. USD_TIPS 10Y real yield minus GBP_LINKER
    10Y real yield, USD_TIPS 5Y real yield minus EUR_FR_LINKER 5Y
    real yield), plus period changes (in bps), 1-year z-score, and
    deterministic historical context (high, low, percentile in
    PERCENT).

        spread_pct = first_curve_real_yield_pct - second_curve_real_yield_pct

    where first_curve_real_yield / second_curve_real_yield are the
    linker real-yield levels at the same tenor for
    first_curve_family / second_curve_family respectively (each
    computed by the same desk-recognised real-yield-level
    primitive).  Sign convention is first_curve_family minus
    second_curve_family — fixed.

    The output is a CROSS-COUNTRY REAL-RATE DIFFERENTIAL, distinct
    from a cross-country breakeven differential (inflation
    compensation) and from a sovereign nominal cross-market spread.
    Two load-bearing caveats:

      1. INDEX-FAMILY MISMATCH: different countries' linkers
         reference different inflation indices (USD CPI-U non-
         seasonally adjusted vs euro-area HICP ex-tobacco vs UK
         RPI/CPIH vs Canada CPI).  These are NOT identical
         inflation references — a US 10Y real yield minus a UK 10Y
         real yield is a CPI-U-vs-RPI real-rate differential, not
         a "pure cross-country real-rate" object.
      2. MARKET-STRUCTURE MISMATCH: linker markets differ
         materially in benchmark availability at the same tenor
         pillar, issuance size, liquidity premium, and deflation-
         floor treatment.  USD TIPS, GBP linkers, EUR-area linkers
         and Canadian RRBs are NOT fungible at the same tenor —
         the displayed differential reflects real-rate divergence
         AND relative linker-market structure differences.

    The tool's output includes an explicit ``methodology_label``
    field carrying both caveats; preserve it when summarising the
    result to the user.

    Use this tool when the user asks about:
    - Cross-country real-yield differentials (e.g. "Where's
      US-UK 10Y real yield?", "Is TIPS-Canada 10Y real-rate
      differential wide?")
    - Cross-country real-rate moves         (e.g. "How much has
      the US-EUR 10Y real-rate differential moved this week?")
    - Cross-country real-rate extremes      (e.g. "Is the US-UK
      5Y real-yield differential at a 1-year high?")
    - Decomposing a cross-country real-rate move (this tool
      returns BOTH the differential AND the two underlying linker
      real yields used to form it).

    Do NOT use this tool for:
    - Same-country linker real-yield curve spreads (2s10s real-
      yield, 5s30s real-yield) — call
      ``calculate_real_yield_curve_spread_tool``.
    - Cross-country breakeven differentials — call
      ``calculate_cross_country_breakeven_spread_simple_tool``.
      Breakeven differentials are inflation-compensation
      differentials, NOT real-rate differentials.
    - Sovereign nominal cross-market spreads — those belong to
      the sovereign-bond agent's
      ``calculate_cross_market_spread_tool``.
    - Real-yield level (single tenor, single curve) — call
      ``get_real_yield_level_tool``.
    - Currency-hedged or FX-adjusted variants — this primitive is
      raw real-yield differentials only.  A hedged variant ships
      as a separate primitive when FX-forward / cross-currency
      basis metadata lands.

    Parameters
    ----------
    first_curve_family : str
        First linker curve identifier exactly as stored in
        instrument_master.  Live linker curves: 'USD_TIPS' (US),
        'GBP_LINKER' (UK), 'EUR_FR_LINKER' (France), 'CAD_RRB'
        (Canada).  Must be DIFFERENT from ``second_curve_family``
        (this primitive is a cross-country object by
        construction).  Non-linker curve_families (e.g. nominal
        sovereign 'UST', 'DE_BUND') are refused at compute time
        with a controlled error envelope.
    second_curve_family : str
        Second linker curve identifier — MUST differ from
        ``first_curve_family``.  Sign convention is first minus
        second — fixed; the tool never silently flips the sign.
        Available linker curve pairs (cross-country, both legs
        ingested in the playbook): USD_TIPS vs GBP_LINKER,
        USD_TIPS vs EUR_FR_LINKER, USD_TIPS vs CAD_RRB,
        GBP_LINKER vs EUR_FR_LINKER, GBP_LINKER vs CAD_RRB,
        EUR_FR_LINKER vs CAD_RRB (and the reverse-order variants
        with flipped sign).
    tenor : str
        Single tenor applied to BOTH curves (e.g. '5Y', '10Y',
        '30Y').  Must exist on BOTH curves; common cross-country
        tenors per pair:
          - USD_TIPS vs GBP_LINKER: 5Y / 10Y / 20Y / 30Y
          - USD_TIPS vs EUR_FR_LINKER: 5Y / 10Y
          - USD_TIPS vs CAD_RRB: 5Y / 10Y / 20Y / 30Y
          - GBP_LINKER vs EUR_FR_LINKER: 2Y / 5Y / 10Y / 15Y
          - GBP_LINKER vs CAD_RRB: 5Y / 10Y / 15Y / 20Y / 30Y
          - EUR_FR_LINKER vs CAD_RRB: 5Y / 10Y / 15Y
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does
        NOT control the rolling z-score window or the trailing
        range window.
    field_name : str, optional
        Bloomberg field mnemonic for BOTH underlying linker real-
        yield series (first_curve at tenor, second_curve at
        tenor).  Leave as the default empty string ""  to use the
        bundled ``default_field_name`` convention from
        cross_country_real_yield_spread_simple/config.yaml
        (currently 'YLD_YTM_MID').  Pass an explicit field name to
        override per call.  Mirrors the empty-string sentinel
        pattern used by the other rates tools so the YAML default
        actually flows through.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's
    # ``default_field_name``.
    field_name_arg = field_name if field_name else None
    try:
        params = CrossCountryRealYieldSpreadSimpleInput(
            first_curve_family=first_curve_family,
            second_curve_family=second_curve_family,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning(
            "[calculate_cross_country_real_yield_spread_simple_tool] "
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
            "[calculate_cross_country_real_yield_spread_simple_tool] "
            "failed to connect to TimescaleDB",
        )
        return json.dumps(
            {"error": f"Database connection failed: {exc}"},
            default=str,
        )

    # Pass the cross_country_real_yield_spread_simple tool's bundled
    # config explicitly so the dependency is observable here.
    # load_tool_config caches by path, so this is a free lookup
    # after the first call within the MCP subprocess's lifetime.
    # Mirrors the calculate_real_yield_curve_spread_tool wrapper
    # exactly.
    try:
        xcry_config = load_tool_config(
            CROSS_COUNTRY_REAL_YIELD_SPREAD_SIMPLE_CONFIG_PATH,
        )
        result = calculate_cross_country_real_yield_spread_simple(
            engine=engine, params=params, config=xcry_config,
        )
    except Exception as exc:
        logger.exception(
            "[calculate_cross_country_real_yield_spread_simple_tool] "
            "unhandled error for %s vs %s @ %s",
            params.first_curve_family,
            params.second_curve_family,
            params.tenor,
        )
        return json.dumps(
            {
                "error": (
                    "calculate_cross_country_real_yield_spread_simple_tool "
                    f"failed for {params.first_curve_family} vs "
                    f"{params.second_curve_family} @ "
                    f"{params.tenor}: {exc}"
                )
            },
            default=str,
        )

    status = "error" if "error" in result else "OK"
    logger.info(
        "[calculate_cross_country_real_yield_spread_simple_tool] "
        "tool call complete: %s vs %s @ %s → %s",
        params.first_curve_family,
        params.second_curve_family,
        params.tenor,
        status,
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Strip the per-row time series fields before returning to the
    # LLM (frontend REST path returns the full payload).  Same
    # convention as the other rates tools — the LLM doesn't need
    # every historical row to answer "where's the US-UK 10Y real-
    # yield differential?".
    stripped_keys = {"time_series", "time_series_spread", "time_series_zscore"}
    llm_response: dict = {
        k: v for k, v in result.items() if k not in stripped_keys
    }
    bespoke_rows = len(result.get("time_series", []) or [])
    canonical_rows = len(
        result.get("time_series_spread", {}).get("rows", []) or []
    )
    if bespoke_rows or canonical_rows:
        logger.info(
            "[calculate_cross_country_real_yield_spread_simple_tool] "
            "withheld %d bespoke + %d canonical spread rows + %d "
            "zscore rows from LLM context.",
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

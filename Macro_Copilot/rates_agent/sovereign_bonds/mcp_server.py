"""
mcp_server.py — MCP Server for the Rates Agent
================================================

Bridge layer between the LLM orchestrator (LangGraph) and the deterministic
Python math tools.  Exposes each tool as a callable MCP endpoint with a
machine-readable JSON schema.

Design: flat scalar parameters on the tool function, Pydantic validation
inside.  Lazy engine singleton.  stdio transport.  Logging to stderr only.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from rates_agent.sovereign_bonds.tools.schemas import (  # noqa: E402
    CurveSpreadInput,
    YieldLevelInput,
    CrossMarketSpreadInput,
    ButterflyInput,
    CurveMoveInput,
    ScannerInput,
)
from rates_agent.sovereign_bonds.tools.curve_spread import (  # noqa: E402
    CONFIG_PATH as CURVE_SPREAD_CONFIG_PATH,
    calculate_curve_spread,
)
from rates_agent.sovereign_bonds.tools.curve_move_classifier import (  # noqa: E402
    CONFIG_PATH as CURVE_MOVE_CONFIG_PATH,
    classify_curve_move_compute,
)
from rates_agent.sovereign_bonds.tools.yield_levels import (  # noqa: E402
    CONFIG_PATH as YIELD_LEVELS_CONFIG_PATH,
    get_yield_levels,
)
from rates_agent.sovereign_bonds.tools.cross_market_spread import (  # noqa: E402
    CONFIG_PATH as CROSS_MARKET_CONFIG_PATH,
    calculate_cross_market_spread,
)
from rates_agent.sovereign_bonds.tools.butterfly import (  # noqa: E402
    CONFIG_PATH as BUTTERFLY_CONFIG_PATH,
    calculate_butterfly,
)
from rates_agent.sovereign_bonds.tools.zscore_custom import (  # noqa: E402
    CONFIG_PATH as ZSCORE_CUSTOM_CONFIG_PATH,
    ZscoreCustomInput,
    calculate_zscore_custom,
)
from rates_agent.sovereign_bonds.tools.rolling_regression import (  # noqa: E402
    CONFIG_PATH as ROLLING_REGRESSION_CONFIG_PATH,
    RollingRegressionInput,
    calculate_rolling_regression,
)
from rates_agent.sovereign_bonds.tools.scanner import scan_extremes  # noqa: E402
from shared.schemas import SeriesSpec  # noqa: E402
from shared.config import load_tool_config  # noqa: E402

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rates_agent.mcp_server")

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
    name="rates-agent",
    instructions=(
        "You are the Rates Agent for a macro hedge-fund desk.  You have "
        "access to tools that perform deterministic calculations over a "
        "TimescaleDB database of daily sovereign-bond yields.  Use the "
        "tools to answer questions about yield-curve spreads, slope, "
        "curvature, cross-market differentials, and relative-value z-scores.  "
        "Never attempt to do the math yourself — always call a tool and "
        "relay its output to the user."
    ),
)


# ===========================================================================
# TOOL 1: calculate_curve_spread
# ===========================================================================
@mcp.tool()
def calculate_curve_spread_tool(
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "YLD_YTM_MID",
) -> str:
    """Calculate the basis-point spread between two tenor points on the
    SAME sovereign yield curve, plus its 1-year rolling z-score.

    Use this tool when the user asks about:
    - Curve steepness / flatness  (e.g. "How steep is the UST 2s10s?")
    - Spread levels or moves      (e.g. "What did Bund 5s30s do today?")
    - Relative-value signals       (e.g. "Is the Gilt 2s10s rich or cheap?")

    Do NOT use this for cross-market comparisons (e.g. UST vs Bund) — use
    the cross_market_spread tool instead.

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
    field_name : str, optional
        Bloomberg field mnemonic (default 'YLD_YTM_MID').
    """
    try:
        params = CurveSpreadInput(
            curve_family=curve_family, short_tenor=short_tenor,
            long_tenor=long_tenor, lookback_days=lookback_days,
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

    # Pass the curve_spread tool's bundled config explicitly so the
    # config dependency is observable here.  load_tool_config caches
    # by path, so this is a free lookup after the first call within
    # the MCP subprocess's lifetime.
    try:
        cs_config = load_tool_config(CURVE_SPREAD_CONFIG_PATH)
        result = calculate_curve_spread(
            engine=engine, params=params, config=cs_config,
        )
    except Exception as exc:
        logger.exception("Unhandled error in calculate_curve_spread for %s %s/%s",
                         params.curve_family, params.short_tenor, params.long_tenor)
        return json.dumps({"error": f"Calculation failed for {params.curve_family} "
                           f"{params.short_tenor}/{params.long_tenor}: {exc}"}, default=str)

    logger.info("Tool call complete: %s %s/%s → %s",
                params.curve_family, params.short_tenor, params.long_tenor,
                "error" if "error" in result else "OK")

    if "error" in result:
        return json.dumps(result, default=str)

    llm_response = {"current_metrics": result.get("current_metrics", {})}
    ts_rows = len(result.get("time_series", []))
    if ts_rows:
        logger.info("Withheld %d time_series rows from LLM context (frontend-only data).", ts_rows)
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 2: get_yield_levels
# ===========================================================================
@mcp.tool()
def get_yield_levels_tool(
    curve_family: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
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
        Curve identifier. Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB',
        'FR_OAT', 'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'.
    tenor : str
        The tenor point, e.g. '1Y', '2Y', '5Y', '7Y', '10Y', '20Y', '30Y'.
    lookback_days : int, optional
        Calendar days of history fetched + used to scope the
        observation_count window (default 365).  Does NOT control the
        rolling z-score window or trailing range window — those are
        config-driven (see yield_levels/config.yaml).
    field_name : str, optional
        Bloomberg field mnemonic.  Leave as the default empty string
        ""  to use the bundled ``default_field_name`` convention from
        yield_levels/config.yaml (currently 'YLD_YTM_MID').  Pass an
        explicit field name to override per call.  Mirrors the
        empty-string sentinel pattern used by curve_move_classifier
        and OIS forward_rate.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's default_field_name.
    # Without this, the LLM omitting field_name would always hit a
    # hardcoded default regardless of what the YAML says — same
    # shadowing pattern fixed for curve_move in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = YieldLevelInput(
            curve_family=curve_family, tenor=tenor,
            lookback_days=lookback_days, field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps({"error": f"Database connection failed: {exc}"}, default=str)

    # Pass the yield_levels tool's bundled config explicitly so the
    # config dependency is observable here.  load_tool_config caches
    # by path, so this is a free lookup after the first call within
    # the MCP subprocess's lifetime.
    try:
        yl_config = load_tool_config(YIELD_LEVELS_CONFIG_PATH)
        result = get_yield_levels(
            engine=engine, params=params, config=yl_config,
        )
    except Exception as exc:
        logger.exception("Unhandled error in get_yield_levels for %s %s",
                         params.curve_family, params.tenor)
        return json.dumps({"error": f"Calculation failed for {params.curve_family} "
                           f"{params.tenor}: {exc}"}, default=str)

    logger.info("Tool call complete: %s %s → %s",
                params.curve_family, params.tenor,
                "error" if "error" in result else "OK")

    if "error" in result:
        return json.dumps(result, default=str)
    return json.dumps({"current_metrics": result.get("current_metrics", {})}, default=str)


# ===========================================================================
# TOOL 3: calculate_cross_market_spread
# ===========================================================================
@mcp.tool()
def calculate_cross_market_spread_tool(
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Calculate the yield differential between the SAME tenor on TWO
    DIFFERENT sovereign curves (e.g. 10Y UST minus 10Y Bund).

    The spread is computed as curve_family_1 minus curve_family_2 in basis
    points.  Convention: for BTP-Bund spread, use curve_family_1='IT_BTP',
    curve_family_2='DE_BUND'.

    Use this tool when the user asks about:
    - Cross-market spreads      (e.g. "What's the UST-Bund 10Y spread?")
    - Peripheral spreads        (e.g. "Where's BTP-Bund trading?")
    - Transatlantic differentials (e.g. "Has the UST-Gilt 2Y widened?")

    Do NOT use this for same-curve tenor spreads (e.g. UST 2s10s) — use
    the curve_spread tool instead.

    Parameters
    ----------
    curve_family_1 : str
        The first (numerator) curve.  spread = curve_family_1 minus curve_family_2.
        Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT',
        'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'.
    curve_family_2 : str
        The second (denominator) curve.  Must be different from curve_family_1.
    tenor : str
        The tenor point to compare, e.g. '2Y', '5Y', '10Y', '30Y'.
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does NOT
        control the rolling z-score window or trailing range window —
        those are config-driven (see cross_market_spread/config.yaml).
    field_name : str, optional
        Bloomberg field mnemonic.  Leave as the default empty string
        ""  to use the bundled ``default_field_name`` convention from
        cross_market_spread/config.yaml (currently 'YLD_YTM_MID').  Pass
        an explicit field name to override per call.  Mirrors the
        empty-string sentinel pattern used by the other migrated tools.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's default_field_name.
    # Without this, the LLM omitting field_name would always hit a
    # hardcoded default regardless of what the YAML says — same
    # shadowing pattern fixed for curve_move in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = CrossMarketSpreadInput(
            curve_family_1=curve_family_1, curve_family_2=curve_family_2,
            tenor=tenor, lookback_days=lookback_days, field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps({"error": f"Database connection failed: {exc}"}, default=str)

    # Pass cross_market_spread's bundled config explicitly so the
    # config dependency is observable here.  load_tool_config caches by
    # path, so this is a free lookup after the first call within the
    # MCP subprocess's lifetime.
    try:
        cm_config = load_tool_config(CROSS_MARKET_CONFIG_PATH)
        result = calculate_cross_market_spread(
            engine=engine, params=params, config=cm_config,
        )
    except Exception as exc:
        logger.exception("Unhandled error in calculate_cross_market_spread for %s-%s %s",
                         params.curve_family_1, params.curve_family_2, params.tenor)
        return json.dumps({"error": f"Calculation failed for {params.curve_family_1}-"
                           f"{params.curve_family_2} {params.tenor}: {exc}"}, default=str)

    logger.info("Tool call complete: %s-%s %s → %s",
                params.curve_family_1, params.curve_family_2, params.tenor,
                "error" if "error" in result else "OK")

    if "error" in result:
        return json.dumps(result, default=str)

    llm_response = {"current_metrics": result.get("current_metrics", {})}
    ts_rows = len(result.get("time_series", []))
    if ts_rows:
        logger.info("Withheld %d time_series rows from LLM context (frontend-only data).", ts_rows)
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 4: calculate_butterfly
# ===========================================================================
@mcp.tool()
def calculate_butterfly_tool(
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Calculate the 3-point butterfly (curvature) on a sovereign yield
    curve: butterfly = 2 times belly minus short minus long (in basis points).

    A positive butterfly means the belly is cheap (yielding more than the
    linear interpolation of the wings).  A negative butterfly means the
    belly is rich.

    Also returns the two component 2-leg spreads (belly minus short and
    long minus belly) for decomposition.

    Use this tool when the user asks about:
    - Butterflies / curvature  (e.g. "What's the UST 2s5s10s butterfly?")
    - Belly richness/cheapness  (e.g. "Is the 5Y belly cheap or rich?")
    - Curve shape beyond slope   (e.g. "Is the Bund curve convex or concave?")

    Parameters
    ----------
    curve_family : str
        Curve identifier. Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB',
        'FR_OAT', 'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'.
    short_tenor : str
        The short wing, e.g. '2Y'.
    belly_tenor : str
        The belly (body), e.g. '5Y'.
    long_tenor : str
        The long wing, e.g. '10Y'.
        All three tenors must be different.
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does NOT
        control the rolling z-score window or trailing range window —
        those are config-driven (see butterfly/config.yaml).
    field_name : str, optional
        Bloomberg field mnemonic.  Leave as the default empty string
        ""  to use the bundled ``default_field_name`` convention from
        butterfly/config.yaml (currently 'YLD_YTM_MID').  Pass an
        explicit field name to override per call.  Mirrors the
        empty-string sentinel pattern used by yield_levels and
        curve_move_classifier.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's default_field_name.
    # Without this, the LLM omitting field_name would always hit a
    # hardcoded default regardless of what the YAML says — same
    # shadowing pattern fixed for curve_move in commit b2605ee.
    field_name_arg = field_name if field_name else None
    try:
        params = ButterflyInput(
            curve_family=curve_family, short_tenor=short_tenor,
            belly_tenor=belly_tenor, long_tenor=long_tenor,
            lookback_days=lookback_days, field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps({"error": f"Database connection failed: {exc}"}, default=str)

    # Pass the butterfly tool's bundled config explicitly so the
    # config dependency is observable here.  load_tool_config caches
    # by path, so this is a free lookup after the first call within
    # the MCP subprocess's lifetime.
    try:
        bf_config = load_tool_config(BUTTERFLY_CONFIG_PATH)
        result = calculate_butterfly(
            engine=engine, params=params, config=bf_config,
        )
    except Exception as exc:
        logger.exception("Unhandled error in calculate_butterfly for %s %s/%s/%s",
                         params.curve_family, params.short_tenor,
                         params.belly_tenor, params.long_tenor)
        return json.dumps({"error": f"Calculation failed for {params.curve_family} "
                           f"{params.short_tenor}/{params.belly_tenor}/"
                           f"{params.long_tenor}: {exc}"}, default=str)

    logger.info("Tool call complete: %s %s/%s/%s → %s",
                params.curve_family, params.short_tenor,
                params.belly_tenor, params.long_tenor,
                "error" if "error" in result else "OK")

    if "error" in result:
        return json.dumps(result, default=str)

    llm_response = {"current_metrics": result.get("current_metrics", {})}
    ts_rows = len(result.get("time_series", []))
    if ts_rows:
        logger.info("Withheld %d time_series rows from LLM context (frontend-only data).", ts_rows)
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 5: classify_curve_move
# ===========================================================================
# Renamed from classify_curve_regime_tool — see
# rates_agent/sovereign_bonds/tools/curve_move_classifier/compute.py
# "Why rename" docstring.  No backward-compat alias is exposed; the
# legacy name is gone.
@mcp.tool()
def classify_curve_move_tool(
    curve_family: str,
    front_tenor: str = "2Y",
    back_tenor: str = "10Y",
    lookback_period: str = "1d",
    field_name: str = "",
) -> str:
    """Classify the curve move over a discrete lookback period into one of
    six deterministic tags: BULL_STEEPENER, BEAR_STEEPENER,
    BULL_FLATTENER, BEAR_FLATTENER, PARALLEL_SHIFT, or TWIST.

    This is a single-observation classification — NOT a statistical
    persistence-state inference (which would justify the word "regime").
    Use it whenever the user asks about the nature of a curve move
    rather than just the numbers.

    Use this tool when the user asks about:
    - Move type           (e.g. "Was today a bull steepener?")
    - Curve dynamics      (e.g. "How has the Gilt curve moved this week?")
    - Macro interpretation (e.g. "What kind of move are we seeing in Bunds?")

    Parameters
    ----------
    curve_family : str
        Curve identifier. Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB',
        'FR_OAT', 'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'.
    front_tenor : str, optional
        The front-end leg (default '2Y').
    back_tenor : str, optional
        The back-end leg (default '10Y').  Must differ from front_tenor.
    lookback_period : str, optional
        Discrete period to measure: '1d' (today), '5d' (weekly),
        '22d' (monthly), '63d' (quarterly).  Default '1d'.  The full
        allowed set is configured per-tool via the
        ``allowed_lookback_periods`` convention in the tool's
        config.yaml.
    field_name : str, optional
        Bloomberg field mnemonic.  Leave as the default empty string ""
        to use the tool's bundled ``default_field_name`` convention
        from config.yaml (currently 'YLD_YTM_MID' for sovereigns).
        Pass an explicit field name to override per call.  Mirrors the
        empty-string sentinel pattern used by ``calculate_ois_forward_rate_tool``
        for optional tenor/date inputs — MCP serialises only flat
        scalars, so we use "" rather than None at the wire level.
    """
    # Translate the empty-string sentinel into a None that the schema
    # layer recognises and the compute layer resolves against the
    # bundled config's ``default_field_name``.  Without this, an LLM
    # that omits ``field_name`` would still hit the YAML's value
    # because the wrapper passed an explicit "YLD_YTM_MID" string;
    # this keeps the convention genuinely config-driven.
    field_name_arg = field_name if field_name else None
    try:
        params = CurveMoveInput(
            curve_family=curve_family, front_tenor=front_tenor,
            back_tenor=back_tenor, lookback_period=lookback_period,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps({"error": f"Database connection failed: {exc}"}, default=str)

    # Pass the curve-move-classifier config explicitly so the config
    # dependency is observable here.  load_tool_config caches by path.
    try:
        cm_config = load_tool_config(CURVE_MOVE_CONFIG_PATH)
        result = classify_curve_move_compute(
            engine=engine, params=params, config=cm_config,
        )
    except Exception as exc:
        logger.exception("Unhandled error in classify_curve_move for %s %s/%s %s",
                         params.curve_family, params.front_tenor,
                         params.back_tenor, params.lookback_period)
        return json.dumps({"error": f"Classification failed for {params.curve_family} "
                           f"{params.front_tenor}/{params.back_tenor} "
                           f"({params.lookback_period}): {exc}"}, default=str)

    logger.info("Tool call complete: %s %s/%s %s → %s",
                params.curve_family, params.front_tenor,
                params.back_tenor, params.lookback_period,
                "error" if "error" in result else result.get("current_metrics", {}).get("classification", "OK"))

    if "error" in result:
        return json.dumps(result, default=str)

    return json.dumps({"current_metrics": result.get("current_metrics", {})}, default=str)


# ===========================================================================
# TOOL 6: scan_extremes
# ===========================================================================
@mcp.tool()
def scan_extremes_tool(
    curve_families: str = "",
    top_n: int = 10,
    min_abs_z_score: float = 1.5,
    field_name: str = "YLD_YTM_MID",
) -> str:
    """Scan ALL sovereign yield curve instruments for z-score extremes and
    return the most statistically stretched observations, ranked by absolute
    z-score.

    This is a screener tool — it looks across the entire database to find
    anomalies the PM might not know to ask about.

    Use this tool when the user asks about:
    - Global extremes        (e.g. "What's the most stretched yield today?")
    - Morning briefing scan   (e.g. "Flag anything unusual across all markets")
    - Anomaly detection       (e.g. "Are any yields at 1-year extremes?")
    - Broad screening         (e.g. "Show me anything with a z-score above 2")

    Parameters
    ----------
    curve_families : str, optional
        Comma-separated list of curve families to scan.  If empty, scans
        ALL sovereign curves.  Examples: 'UST,DE_BUND,UK_GILT' or '' for all.
    top_n : int, optional
        Number of top extreme results to return (default 10, max 50).
    min_abs_z_score : float, optional
        Minimum absolute z-score threshold (default 1.5).  Only instruments
        with |z-score| >= this value are included.  Set to 0 to see everything.
    field_name : str, optional
        Bloomberg field mnemonic (default 'YLD_YTM_MID').
    """
    # Parse the comma-separated curve_families string into a list.
    # MCP exposes flat scalars, so we accept a string and split it.
    parsed_families = None
    if curve_families and curve_families.strip():
        parsed_families = [cf.strip() for cf in curve_families.split(",") if cf.strip()]

    try:
        params = ScannerInput(
            curve_families=parsed_families,
            top_n=top_n,
            min_abs_z_score=min_abs_z_score,
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
        result = scan_extremes(engine=engine, params=params)
    except Exception as exc:
        logger.exception("Unhandled error in scan_extremes")
        return json.dumps({"error": f"Scan failed: {exc}"}, default=str)

    logger.info("Scan complete → %s",
                "error" if "error" in result else
                f"{len(result.get('results', []))} extremes found")

    if "error" in result:
        return json.dumps(result, default=str)

    return json.dumps(result, default=str)


# ===========================================================================
# TOOL 7: zscore_custom
# ===========================================================================
@mcp.tool()
def zscore_custom_tool(
    curve_family: str,
    tenor: str,
    z_score_window_days: int,
    lookback_days: int = 365,
    field_name: str = "",
) -> str:
    """Compute a rolling z-score on one sovereign yield series using a
    user-supplied window (set per request).

    Distinct from get_yield_levels_tool, which always uses the bundled
    252-day window.  This tool exists so a desk can ask for a 60-day
    or 504-day z-score on the same point without methodology rework.

    Use this tool when the user asks about:
    - Custom-window z-score   (e.g. "What's the 60-day z-score of UST 10Y?")
    - Tactical signals        (e.g. "Show me a 90-day z-score for Bund 5Y")
    - Multi-horizon analysis  (e.g. "Compare 60d vs 252d z-scores for BTP 10Y")

    Parameters
    ----------
    curve_family : str
        Curve identifier.  Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB',
        'FR_OAT', 'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'.
    tenor : str
        Tenor point — e.g. '1Y', '2Y', '5Y', '7Y', '10Y', '20Y', '30Y'.
    z_score_window_days : int
        Rolling window length in trading days for the z-score.  This is
        the tool's central methodological choice — set per request.
        Constrained to [20, 1260].  Typical desk values: 60 (tactical),
        126 (quarterly), 252 (annual), 504 (two-year).
    lookback_days : int, optional
        Calendar days of *displayed* z-score history (default 365).
        Does NOT control the rolling window length — that is
        z_score_window_days.
    field_name : str, optional
        Bloomberg field mnemonic.  Leave as the default empty string ""
        to use the bundled ``default_field_name`` convention from
        zscore_custom/config.yaml (currently 'YLD_YTM_MID').  Mirrors
        the empty-string sentinel pattern used by curve_move_classifier
        and yield_levels.
    """
    # Translate the empty-string sentinel into None so the schema +
    # compute layers resolve against the YAML's default_field_name.
    field_name_arg = field_name if field_name else None
    try:
        params = ZscoreCustomInput(
            curve_family=curve_family,
            tenor=tenor,
            z_score_window_days=z_score_window_days,
            lookback_days=lookback_days,
            field_name=field_name_arg,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps({"error": f"Database connection failed: {exc}"}, default=str)

    # Pass the zscore_custom tool's bundled config explicitly so the
    # config dependency is observable at the call site.  load_tool_config
    # caches by path, so this is a free lookup after the first call
    # within the MCP subprocess's lifetime.
    try:
        zc_config = load_tool_config(ZSCORE_CUSTOM_CONFIG_PATH)
        result = calculate_zscore_custom(
            engine=engine, params=params, config=zc_config,
        )
    except Exception as exc:
        logger.exception("Unhandled error in calculate_zscore_custom for %s %s (window=%d)",
                         params.curve_family, params.tenor, params.z_score_window_days)
        return json.dumps({"error": f"Calculation failed for {params.curve_family} "
                           f"{params.tenor} (z_score_window_days="
                           f"{params.z_score_window_days}): {exc}"}, default=str)

    logger.info("Tool call complete: %s %s window=%d → %s",
                params.curve_family, params.tenor, params.z_score_window_days,
                "error" if "error" in result else "OK")

    if "error" in result:
        return json.dumps(result, default=str)

    # Withhold the time_series payload from the LLM (frontend-only).
    llm_response = {"current_metrics": result.get("current_metrics", {})}
    ts_rows = len(result.get("time_series", {}).get("rows", []))
    if ts_rows:
        logger.info("Withheld %d time_series rows from LLM context (frontend-only data).", ts_rows)
    return json.dumps(llm_response, default=str)


# ===========================================================================
# TOOL 8: rolling_regression  (first nested-input MCP wrapper)
# ===========================================================================
@mcp.tool()
def rolling_regression_tool(
    target_spec: SeriesSpec,
    regressor_specs: List[SeriesSpec],
    regression_window_days: int,
    lookback_days: int = 365,
) -> str:
    """Run a rolling OLS regression of one sovereign yield series on
    one or more regressor yield series, with a user-supplied window.

    Use this tool when the user asks about:
    - Hedge ratio between two yields  (e.g. "What's the rolling beta
      of BTP 10Y on Bund 10Y over the last year?")
    - Beta-adjusted RV signal         (residual after regressing one
      yield on another)
    - Multi-regressor decomposition   (e.g. "Regress UST 10Y on Bund
      10Y AND Gilt 10Y; show me the residual.")

    Distinct from the simpler bivariate-only `beta_adjusted_spread`
    tool (when that lands): rolling_regression supports any number of
    regressors and exposes the full betas + alpha + residual + R² +
    condition-flag time series.

    Parameters
    ----------
    target_spec : SeriesSpec
        The y in y on X.  ``{curve_family, tenor, field_name?}``.
        ``field_name=None`` falls through to the YAML's
        default_field_name (currently 'YLD_YTM_MID').
    regressor_specs : List[SeriesSpec]
        One or more regressor specs.  Each is also a
        ``{curve_family, tenor, field_name?}``.  Order is preserved
        in the output betas dictionary.  Target's (curve_family,
        tenor) cannot equal any regressor's — that would be a
        regression of a series on itself (degenerate).
    regression_window_days : int
        Trailing-window length in trading-day rows for each rolling
        fit.  This is the tool's central methodological choice — set
        per request.  Constrained to [10, 2520].  Typical desk
        values: 60 (tactical), 252 (annual), 504 (two-year).
    lookback_days : int, optional
        Calendar days of *displayed* history (default 365).  Does
        NOT control the rolling window length — that is
        regression_window_days.
    """
    try:
        params = RollingRegressionInput(
            target_spec=target_spec,
            regressor_specs=regressor_specs,
            regression_window_days=regression_window_days,
            lookback_days=lookback_days,
        )
    except ValidationError as exc:
        logger.warning("Input validation failed: %s", exc)
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        engine = _get_engine()
    except Exception as exc:
        logger.exception("Failed to connect to TimescaleDB")
        return json.dumps({"error": f"Database connection failed: {exc}"}, default=str)

    # Pass the rolling_regression tool's bundled config explicitly so
    # the dependency is observable at the call site.  load_tool_config
    # caches by path, so this is a free lookup after the first call
    # within the MCP subprocess's lifetime.
    try:
        rr_config = load_tool_config(ROLLING_REGRESSION_CONFIG_PATH)
        result = calculate_rolling_regression(
            engine=engine, params=params, config=rr_config,
        )
    except Exception as exc:
        logger.exception(
            "Unhandled error in calculate_rolling_regression for "
            "target=%s regressors=%s window=%d",
            params.target_spec, params.regressor_specs,
            params.regression_window_days,
        )
        return json.dumps({"error": f"Calculation failed: {exc}"}, default=str)

    logger.info(
        "Tool call complete: rolling_regression target=%s_%s "
        "regressors=%d window=%d → %s",
        params.target_spec.curve_family, params.target_spec.tenor,
        len(params.regressor_specs), params.regression_window_days,
        "error" if "error" in result else "OK",
    )

    if "error" in result:
        return json.dumps(result, default=str)

    # Withhold time-series payloads from the LLM (frontend / chart
    # consumer only).  Echo the snapshot only.
    llm_response = {"current_metrics": result.get("current_metrics", {})}
    n_beta_series = len(result.get("time_series_betas", []))
    n_residual_rows = len(result.get("time_series_residual", {}).get("rows", []))
    if n_beta_series or n_residual_rows:
        logger.info(
            "Withheld time-series payload from LLM context "
            "(%d beta series, %d residual rows).",
            n_beta_series, n_residual_rows,
        )
    return json.dumps(llm_response, default=str)


# ===========================================================================
# ENTRY POINT
# ===========================================================================
if __name__ == "__main__":
    logger.info("Starting Rates Agent MCP server (stdio transport)...")
    mcp.run(transport="stdio")

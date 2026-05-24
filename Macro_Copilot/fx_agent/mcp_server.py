from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from database.database import get_db_engine  # noqa: E402
from fx_agent.forwards.tools.forward_curve import (  # noqa: E402
    CONFIG_PATH as FX_FORWARD_CURVE_CONFIG_PATH,
    FXForwardCurveInput,
    get_fx_forward_curve,
)
from fx_agent.forwards.tools.fx_carry import (  # noqa: E402
    CONFIG_PATH as FX_CARRY_CONFIG_PATH,
    FXCarryInput,
    get_fx_carry,
)
from fx_agent.spot.tools.fx_panel import (  # noqa: E402
    CONFIG_PATH as FX_PANEL_CONFIG_PATH,
    FXPanelInput,
    calculate_fx_panel,
)
from fx_agent.spot.tools.scanner import run_fx_scanner  # noqa: E402
from fx_agent.spot.tools.schemas import FXScannerInput  # noqa: E402
from fx_agent.spot.tools.spot_levels import (  # noqa: E402
    CONFIG_PATH as FX_SPOT_LEVEL_CONFIG_PATH,
    FXSpotLevelInput,
    get_fx_spot_level,
)
from shared.config import load_tool_config  # noqa: E402


logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fx_agent.mcp_server")

_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        logger.info("Initializing database engine...")
        _engine = get_db_engine()
        logger.info("Database engine ready.")
    return _engine


mcp = FastMCP(
    name="fx-agent",
    instructions=(
        "You are the FX Agent for a macro desk. You have access to "
        "deterministic tools over stored FX spot and forward observations. "
        "Use tools for market data and calculations; do not invent levels."
    ),
)


@mcp.tool()
def get_fx_spot_level_tool(
    pair: str,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Get a single FX spot pair snapshot and rolling context."""
    try:
        params = FXSpotLevelInput(
            pair=pair,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_SPOT_LEVEL_CONFIG_PATH)
        result = get_fx_spot_level(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX spot level failed for %s", pair)
        return json.dumps({"error": f"FX spot level failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def scan_fx_spot_tool(
    market_scope: Optional[str] = None,
    top_n: int = 10,
    field_name: str = "PX_LAST",
) -> str:
    """Scan FX spot pairs for stretched z-score / momentum signals."""
    try:
        params = FXScannerInput(
            market_scope=market_scope,
            top_n=top_n,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        result = run_fx_scanner(_get_engine(), params)
    except Exception as exc:
        logger.exception("FX spot scanner failed")
        return json.dumps({"error": f"FX spot scanner failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def calculate_fx_carry_tool(
    tenor: str = "1M",
    rank_by: str = "carry_signed",
    top_n: Optional[int] = None,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Cross-sectional FX carry scanner over the standard G10 forward
    universe at a given tenor.

    For every G10 pair with both spot and same-tenor forward observations
    in the DB, returns spot, raw forward points, spot-unit forward points,
    outright forward, carry in basis points, annualised carry %, plus a
    rolling 252-day z-score / percentile / range computed on the per-date
    carry_annualized_pct series (historical spot and forward are joined
    by trade_date — no lookahead).

    Useful for: identifying the richest / cheapest carry currencies vs
    history, ranking carry trades by absolute z-score extremeness,
    monitoring carry compression / blowouts across the G10 strip.

    Parameters
    ----------
    tenor : "1W" | "1M" | "3M" | "6M" | "12M", default "1M"
    rank_by : "carry_signed" | "abs_carry" | "abs_z_score", default
        "carry_signed" (matches the pre-scanner descending-by-carry sort).
    top_n : int | None, default None (returns every pair in the substrate).
    lookback_days : int, default 365 — DB fetch window for the z-score
        history. Does NOT control the z-score window itself (252).
    field_name : str | None — Bloomberg field override; None falls
        through to PX_LAST.
    """
    try:
        params = FXCarryInput(
            tenor=tenor,
            rank_by=rank_by,
            top_n=top_n,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_CARRY_CONFIG_PATH)
        result = get_fx_carry(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception(
            "FX carry failed for tenor=%s rank_by=%s top_n=%s",
            tenor,
            rank_by,
            top_n,
        )
        return json.dumps({"error": f"FX carry failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_forward_curve_tool(
    pair: str,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """FX forward-curve term structure snapshot for one G10 pair.

    For the requested pair, returns one row per supported forward tenor
    (1W / 1M / 3M / 6M / 12M, short → long) with spot, raw forward
    points, spot-unit forward points, outright forward, carry in basis
    points, annualised carry %, and a rolling 252-day z-score /
    percentile / range on the spot-unit forward points series.

    Useful for: visualising the forward curve term structure of one
    pair, comparing carry across tenors, spotting tenors where the
    forward point is stretched vs its own history.

    Note: the z-score is on forward_points_spot_units (curve
    stretchedness per tenor), distinct from calculate_fx_carry's
    z-score which is on carry_annualized_pct (carry extremeness per
    pair). Same substrate, different question.

    Parameters
    ----------
    pair : str — FX pair as stored in instrument_master.attributes.pair.
        G10 majors: EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field override; None falls
        through to PX_LAST.
    """
    try:
        params = FXForwardCurveInput(
            pair=pair,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_FORWARD_CURVE_CONFIG_PATH)
        result = get_fx_forward_curve(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX forward curve failed for pair=%s", pair)
        return json.dumps({"error": f"FX forward curve failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def calculate_fx_panel_tool(
    market_scope: str,
    start_date: str,
    end_date: Optional[str] = None,
    missing_data_policy: Optional[str] = None,
    field_name: str = "PX_LAST",
) -> str:
    """Cross-sectional FX spot Panel for a market_scope subset.

    Assembles a wide multi-instrument Panel of FX spot levels keyed
    by pair (e.g. EURUSD, USDMXN), pivoted from market_data_daily and
    cleaned via the standard missing-data policy. Cornerstone Phase B
    primitive — every later cross-sectional FX tool (returns,
    drawdown, realized vol, correlation matrix, carry basket, factor
    decomposition) consumes a Panel produced by this primitive.

    Returns the panel's metadata (column list, date range, observation
    count, per-column units) inline; the typed Panel artifact is
    extracted by the workflow executor and dropped before LLM
    serialisation to keep the token budget sane.

    Parameters
    ----------
    market_scope : str — one of {"G10", "EM", "G10_CROSSES", "ALL"}.
        G10 = 9 G10 majors; EM = 9 EM majors; G10_CROSSES = 11 G10
        crosses; ALL = every fx_spot instrument (29).
    start_date : str — ISO date "YYYY-MM-DD" (inclusive).
    end_date : str | None — ISO date "YYYY-MM-DD"; None = latest.
    missing_data_policy : str | None — one of {"forward_fill_only",
        "raise", "drop_rows_any_missing"}; None = config default
        ("forward_fill_only").
    field_name : str, default "PX_LAST" — Bloomberg field on
        market_data_daily.
    """
    from datetime import date as _date  # local import keeps top clean

    try:
        params = FXPanelInput(
            market_scope=market_scope,
            start_date=_date.fromisoformat(start_date),
            end_date=_date.fromisoformat(end_date) if end_date else None,
            missing_data_policy=missing_data_policy,
            field_name=field_name,
        )
    except (ValidationError, ValueError) as exc:
        return json.dumps({"error": f"Invalid parameters: {exc}"}, default=str)

    try:
        config = load_tool_config(FX_PANEL_CONFIG_PATH)
        result = calculate_fx_panel(_get_engine(), params, config=config)
        # Drop the typed Panel before serialising for the LLM — the
        # workflow executor's bridge has already extracted it. Keeps
        # the JSON small (metadata only, not the full ~60k-row wide
        # DataFrame for a 26-year EM panel).
        result.pop("panel", None)
    except Exception as exc:
        logger.exception(
            "FX panel failed for scope=%s start=%s end=%s",
            market_scope, start_date, end_date,
        )
        return json.dumps({"error": f"FX panel failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


if __name__ == "__main__":
    logger.info("Starting FX Agent MCP server (stdio transport)...")
    mcp.run(transport="stdio")

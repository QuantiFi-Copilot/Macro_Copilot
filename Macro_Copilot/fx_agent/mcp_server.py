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
from fx_agent.ndf.tools.ndf_implied_carry import (  # noqa: E402
    CONFIG_PATH as FX_NDF_IMPLIED_CARRY_CONFIG_PATH,
    FXNDFImpliedCarryInput,
    calculate_fx_ndf_implied_carry,
)
from fx_agent.ndf.tools.ndf_outright import (  # noqa: E402
    CONFIG_PATH as FX_NDF_OUTRIGHT_CONFIG_PATH,
    FXNDFOutrightInput,
    get_fx_ndf_outright,
)
from fx_agent.spot.tools.drawdown import (  # noqa: E402
    CONFIG_PATH as FX_DRAWDOWN_CONFIG_PATH,
    FXDrawdownInput,
    calculate_fx_drawdown,
)
from fx_agent.spot.tools.fx_panel import (  # noqa: E402
    CONFIG_PATH as FX_PANEL_CONFIG_PATH,
    FXPanelInput,
    calculate_fx_panel,
)
from fx_agent.spot.tools.realized_vol import (  # noqa: E402
    CONFIG_PATH as FX_REALIZED_VOL_CONFIG_PATH,
    FXRealizedVolInput,
    get_fx_realized_vol,
)
from fx_agent.spot.tools.returns_series import (  # noqa: E402
    CONFIG_PATH as FX_RETURNS_SERIES_CONFIG_PATH,
    FXReturnsSeriesInput,
    get_fx_returns_series,
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
    market_scope: str = "G10",
    rank_by: str = "carry_signed",
    top_n: Optional[int] = None,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Cross-sectional FX carry scanner over the deliverable-forward
    universe at a given tenor.

    For every pair in the selected market_scope with both spot and
    same-tenor forward observations in the DB, returns spot, raw forward
    points, spot-unit forward points, outright forward, carry in basis
    points, annualised carry %, plus a rolling 252-day z-score /
    percentile / range computed on the per-date carry_annualized_pct
    series (historical spot and forward are joined by trade_date — no
    lookahead).

    Useful for: identifying the richest / cheapest carry currencies vs
    history, ranking carry trades by absolute z-score extremeness,
    monitoring carry compression / blowouts across the strip.

    Parameters
    ----------
    tenor : "1W" | "1M" | "3M" | "6M" | "12M", default "1M".
    market_scope : "G10" | "EM" | "ALL", default "G10". 'G10' = 6
        G10 majors (Phase A behaviour). 'EM' = 6 EM deliverable
        forwards (MXN/ZAR/TRY/PLN/HUF/PHP). 'ALL' = 12 (G10 + EM
        deliverable). NDFs (BRL/KRW/IDR/CNY/INR) are NOT included —
        outright vs points unit, separate compute path coming in
        Phase D.
    rank_by : "carry_signed" | "abs_carry" | "abs_z_score", default
        "carry_signed".
    top_n : int | None, default None (returns every pair in the scope).
    lookback_days : int, default 365 — DB fetch window for the z-score
        history. Does NOT control the z-score window itself (252).
    field_name : str | None — Bloomberg field override; None falls
        through to PX_LAST.
    """
    try:
        params = FXCarryInput(
            tenor=tenor,
            market_scope=market_scope,
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


@mcp.tool()
def get_fx_returns_series_tool(
    pair: str,
    horizon: str = "daily",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Log-returns time series for one FX spot pair at a chosen horizon.

    Returns the rolling log-returns at the requested horizon
    (daily / weekly / monthly) plus snapshot summary stats (current
    return, mean / std / min / max over the lookback). Built on top
    of the spot substrate via ``shared.analytics.fx_fetch.fetch_fx_spot_series``.

    Parameters
    ----------
    pair : str — six-char FX pair (e.g. 'EURUSD', 'USDMXN').
    horizon : str, default 'daily' — one of 'daily' / 'weekly' /
        'monthly'. Trading-day periods (1 / 5 / 22).
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXReturnsSeriesInput(
            pair=pair,
            horizon=horizon,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_RETURNS_SERIES_CONFIG_PATH)
        result = get_fx_returns_series(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX returns series failed for pair=%s horizon=%s", pair, horizon)
        return json.dumps({"error": f"FX returns series failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def calculate_fx_drawdown_tool(
    pair: str,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Running drawdown of an FX spot pair vs its trailing peak.

    Computes DD_t = (P_t / running_max(P_0..P_t)) - 1 over the
    lookback window. Returns the drawdown TimeSeries (RATIO, always
    ≤ 0) plus a snapshot summary (current drawdown, max drawdown,
    peak / trough dates, recovery date / time-to-recovery).

    Computed on the raw spot series; sign interpretation is the
    consumer's call based on position direction.

    Parameters
    ----------
    pair : str — six-char FX pair (e.g. 'EURUSD').
    lookback_days : int, default 365. Controls fetch window AND the
        running-max anchor (pre-window peaks are NOT carried in).
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXDrawdownInput(
            pair=pair,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_DRAWDOWN_CONFIG_PATH)
        result = calculate_fx_drawdown(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX drawdown failed for pair=%s", pair)
        return json.dumps({"error": f"FX drawdown failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_realized_vol_tool(
    pair: str,
    window_days: int = 30,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Rolling annualized realized vol of an FX spot pair.

    Computes σ(t) = std(daily_log_returns_t-w..t) * sqrt(252) * 100
    expressed in PERCENT (8.0 = 8% / year). Returns the vol
    TimeSeries plus a snapshot summary (current, mean / min / max
    over the lookback).

    Parameters
    ----------
    pair : str — six-char FX pair (e.g. 'EURUSD').
    window_days : int, default 30. Trading-day rolling window.
        Common alternatives: 60 (quarter), 252 (year).
    lookback_days : int, default 365. Display + snapshot lookback.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXRealizedVolInput(
            pair=pair,
            window_days=window_days,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_REALIZED_VOL_CONFIG_PATH)
        result = get_fx_realized_vol(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX realized vol failed for pair=%s window=%s", pair, window_days)
        return json.dumps({"error": f"FX realized vol failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_ndf_outright_tool(
    ndf_code: str,
    tenor: str = "1M",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Single-(ndf, tenor) NDF outright snapshot.

    For one NDF family (CCN+ / IRN+ / BCN+ / KWN+ / IHN+ / NTN+) at a
    given tenor, returns the latest outright value, daily / weekly /
    monthly % changes, rolling 252-day z-score, trailing 252-day
    high / low / percentile, and observation count. Mirrors
    get_fx_spot_level but for the NDF substrate.

    NDF outrights are quoted in spot-equivalent units (USD per local
    currency), NOT as forward points — for the carry analysis, use
    calculate_fx_ndf_implied_carry.

    Parameters
    ----------
    ndf_code : str — one of CCN+ (USDCNY), IRN+ (USDINR), BCN+ (USDBRL),
        KWN+ (USDKRW), IHN+ (USDIDR), NTN+ (USDTWD).
    tenor : str, default '1M' — one of '1W', '1M', '3M', '6M', '12M'.
    lookback_days : int, default 365 — fetch window for z-score history.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST. Use
        'PX_BID' / 'PX_ASK' for bid/ask side.
    """
    try:
        params = FXNDFOutrightInput(
            ndf_code=ndf_code,
            tenor=tenor,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_NDF_OUTRIGHT_CONFIG_PATH)
        result = get_fx_ndf_outright(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX NDF outright failed for %s %s", ndf_code, tenor)
        return json.dumps({"error": f"FX NDF outright failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def calculate_fx_ndf_implied_carry_tool(
    tenor: str = "1M",
    spot_convention: str = "settlement",
    rank_by: str = "carry_signed",
    top_n: Optional[int] = None,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Cross-sectional FX NDF implied carry scanner.

    For every NDF family (CCN+ / IRN+ / BCN+ / KWN+ / IHN+ / NTN+) at
    a given tenor, joins the latest underlying spot and same-tenor
    NDF outright observation (per trade_date — no lookahead), computes
    implied annualized carry as (outright/spot - 1) * (252/tenor_days)
    * 100, and ranks the cross-section. Each row carries a rolling
    252-day z-score / percentile / range on its OWN historical
    implied-carry series.

    Mirrors calculate_fx_carry but for the NDF outright substrate
    (no points → no divisor). NDFs are quoted outright by convention,
    so the carry formula is direct.

    Useful for: identifying rich / cheap NDF carry vs history, ranking
    NDF carry trades by extremeness, monitoring NDF basis differentials
    (with spot_convention='offshore_tradable' for CCN+).

    Parameters
    ----------
    tenor : str, default '1M' — one of '1W', '1M', '3M', '6M', '12M'.
    spot_convention : str, default 'settlement' — 'settlement' uses
        each NDF's official spot reference (textbook implied carry).
        'offshore_tradable' swaps USDCNY → USDCNH for CCN+ (other
        NDFs fall back to settlement so coverage is uniform).
    rank_by : str, default 'carry_signed' — one of 'carry_signed' /
        'abs_carry' / 'abs_z_score'.
    top_n : int | None, default None (returns every NDF in scope).
    lookback_days : int, default 365 — DB fetch window for z-score
        history.
    field_name : str | None — Bloomberg field for BOTH spot and NDF
        legs; None ⇒ PX_LAST. Use 'PX_BID' / 'PX_ASK' for bid/ask
        side carry.
    """
    try:
        params = FXNDFImpliedCarryInput(
            tenor=tenor,
            spot_convention=spot_convention,
            rank_by=rank_by,
            top_n=top_n,
            lookback_days=lookback_days,
            field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_NDF_IMPLIED_CARRY_CONFIG_PATH)
        result = calculate_fx_ndf_implied_carry(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception(
            "FX NDF implied carry failed tenor=%s rank_by=%s top_n=%s",
            tenor, rank_by, top_n,
        )
        return json.dumps({"error": f"FX NDF implied carry failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


@mcp.tool()
def scan_fx_ndf_carry_tool(
    tenor: str = "1M",
    top_n: int = 5,
    spot_convention: str = "settlement",
    lookback_days: int = 365,
) -> str:
    """Scanner-mode FX NDF carry: top-N most extreme by absolute z-score.

    Thin wrapper around calculate_fx_ndf_implied_carry with curated
    defaults (rank_by='abs_z_score', top_n=5) for the 'what's the
    most extreme NDF carry today?' use case. Same compute path —
    use calculate_fx_ndf_implied_carry directly when you want a
    different rank_by or to see every NDF in scope.

    Parameters
    ----------
    tenor : str, default '1M'.
    top_n : int, default 5.
    spot_convention : str, default 'settlement'. See
        calculate_fx_ndf_implied_carry for the full description.
    lookback_days : int, default 365.
    """
    try:
        params = FXNDFImpliedCarryInput(
            tenor=tenor,
            spot_convention=spot_convention,
            rank_by="abs_z_score",
            top_n=top_n,
            lookback_days=lookback_days,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)

    try:
        config = load_tool_config(FX_NDF_IMPLIED_CARRY_CONFIG_PATH)
        result = calculate_fx_ndf_implied_carry(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX NDF carry scanner failed tenor=%s", tenor)
        return json.dumps({"error": f"FX NDF carry scanner failed: {exc}"}, default=str)

    return json.dumps(result, default=str)


if __name__ == "__main__":
    logger.info("Starting FX Agent MCP server (stdio transport)...")
    mcp.run(transport="stdio")

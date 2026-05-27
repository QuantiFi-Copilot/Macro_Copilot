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
from fx_agent.forwards.tools.carry_basket import (  # noqa: E402
    CONFIG_PATH as FX_CARRY_BASKET_CONFIG_PATH,
    FXCarryBasketInput,
    get_fx_carry_basket,
)
from fx_agent.forwards.tools.cross_currency_basis import (  # noqa: E402
    CONFIG_PATH as FX_XCCY_BASIS_CONFIG_PATH,
    FXCrossCurrencyBasisInput,
    get_fx_cross_currency_basis,
)
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
from fx_agent.forwards.tools.implied_yield_differential import (  # noqa: E402
    CONFIG_PATH as FX_IMPLIED_YIELD_DIFF_CONFIG_PATH,
    FXImpliedYieldDifferentialInput,
    get_fx_implied_yield_differential,
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
from fx_agent.vol.tools.atm_vol_level import (  # noqa: E402
    CONFIG_PATH as FX_ATM_VOL_LEVEL_CONFIG_PATH,
    FXAtmVolLevelInput,
    get_fx_atm_vol_level,
)
from fx_agent.vol.tools.butterfly import (  # noqa: E402
    CONFIG_PATH as FX_BUTTERFLY_CONFIG_PATH,
    FXButterflyInput,
    get_fx_butterfly,
)
from fx_agent.vol.tools.risk_reversal import (  # noqa: E402
    CONFIG_PATH as FX_RISK_REVERSAL_CONFIG_PATH,
    FXRiskReversalInput,
    get_fx_risk_reversal,
)
from fx_agent.vol.tools.vol_calendar_spread import (  # noqa: E402
    CONFIG_PATH as FX_VOL_CALENDAR_SPREAD_CONFIG_PATH,
    FXVolCalendarSpreadInput,
    get_fx_vol_calendar_spread,
)
from fx_agent.vol.tools.vol_risk_premium import (  # noqa: E402
    CONFIG_PATH as FX_VOL_RISK_PREMIUM_CONFIG_PATH,
    FXVolRiskPremiumInput,
    get_fx_vol_risk_premium,
)
from fx_agent.vol.tools.vol_scanner import (  # noqa: E402
    CONFIG_PATH as FX_VOL_SCANNER_CONFIG_PATH,
    FXVolScannerInput,
    run_fx_vol_scanner,
)
from fx_agent.vol.tools.vol_smile import (  # noqa: E402
    CONFIG_PATH as FX_VOL_SMILE_CONFIG_PATH,
    FXVolSmileInput,
    get_fx_vol_smile,
)
from fx_agent.vol.tools.vol_term_structure import (  # noqa: E402
    CONFIG_PATH as FX_VOL_TERM_STRUCTURE_CONFIG_PATH,
    FXVolTermStructureInput,
    get_fx_vol_term_structure,
)
from fx_agent.vol.tools.vol_z_score import (  # noqa: E402
    CONFIG_PATH as FX_VOL_Z_SCORE_CONFIG_PATH,
    FXVolZScoreInput,
    get_fx_vol_z_score,
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


@mcp.tool()
def get_fx_atm_vol_level_tool(
    pair: str,
    tenor: str = "1M",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Single-(pair, tenor) ATM implied vol snapshot.

    Returns latest ATM vol (PERCENT), daily / weekly / monthly absolute
    vol-point changes, rolling 252-day z-score, trailing 252-day
    high/low/percentile, and observation count. Mirror of
    get_fx_spot_level but for the ATM vol substrate (115 standard-tenor
    instruments).

    Parameters
    ----------
    pair : str — six-char FX pair (e.g. 'EURUSD', 'USDMXN', 'EURJPY').
    tenor : str, default '1M' — one of '1W', '1M', '3M', '6M', '12M'.
    lookback_days : int, default 365 — DB fetch window for z-score history.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST. Use
        'PX_BID' / 'PX_ASK' for bid/ask side (std-tenor 115 instruments).
    """
    try:
        params = FXAtmVolLevelInput(
            pair=pair, tenor=tenor, lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_ATM_VOL_LEVEL_CONFIG_PATH)
        result = get_fx_atm_vol_level(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX ATM vol level failed for %s %s", pair, tenor)
        return json.dumps({"error": f"FX ATM vol level failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_vol_term_structure_tool(
    pair: str,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """ATM vol term structure snapshot for one pair.

    For the requested pair, returns one row per standard tenor (1W /
    1M / 3M / 6M / 12M, short → long) with current vol, absolute vol-
    point changes, rolling 252-day z-score / percentile / range.
    Tenors without DB data are silently skipped.

    Parameters
    ----------
    pair : str — six-char FX pair.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXVolTermStructureInput(
            pair=pair, lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_VOL_TERM_STRUCTURE_CONFIG_PATH)
        result = get_fx_vol_term_structure(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX vol term structure failed for %s", pair)
        return json.dumps({"error": f"FX vol term structure failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def scan_fx_vol_tool(
    tenor: str = "1M",
    market_scope: str = "G10",
    rank_by: str = "vol_signed",
    top_n: Optional[int] = None,
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Cross-sectional ATM vol scanner across pairs at one tenor.

    For one tenor and market_scope (G10 / EM / G10_CROSSES / ALL),
    returns one row per pair with current vol, rolling 252-day
    z-score / percentile / range, ranked by 'vol_signed' /
    'abs_vol' / 'abs_z_score'.

    Useful for identifying the highest / lowest vol pairs or the
    pairs whose vol is most stretched vs own history (abs_z_score).

    Parameters
    ----------
    tenor : str, default '1M'.
    market_scope : str, default 'G10'. Choices: 'G10', 'EM',
        'G10_CROSSES', 'ALL'.
    rank_by : str, default 'vol_signed'. Choices: 'vol_signed',
        'abs_vol', 'abs_z_score'.
    top_n : int | None — truncate to top-N after sorting.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXVolScannerInput(
            tenor=tenor, market_scope=market_scope, rank_by=rank_by,
            top_n=top_n, lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_VOL_SCANNER_CONFIG_PATH)
        result = run_fx_vol_scanner(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX vol scanner failed tenor=%s scope=%s", tenor, market_scope)
        return json.dumps({"error": f"FX vol scanner failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_vol_z_score_tool(
    pair: str,
    tenor: str = "1M",
    lookback_days: int = 730,
    field_name: Optional[str] = None,
) -> str:
    """Rolling 252-day z-score TIME SERIES for one (pair, tenor) ATM vol.

    Returns the full time-series of rolling z-scores plus summary stats
    (current, min / max / mean). Useful for visualizing vol-regime
    trajectory rather than just the current snapshot. For a current-
    snapshot single value, prefer get_fx_atm_vol_level.

    Parameters
    ----------
    pair : str — six-char FX pair.
    tenor : str, default '1M'.
    lookback_days : int, default 730 (2 years) — wider than other tools'
        365 so the rolling 252-day z-score series has ~1 year of valid
        emitted points.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXVolZScoreInput(
            pair=pair, tenor=tenor, lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_VOL_Z_SCORE_CONFIG_PATH)
        result = get_fx_vol_z_score(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX vol z-score failed for %s %s", pair, tenor)
        return json.dumps({"error": f"FX vol z-score failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_risk_reversal_tool(
    pair: str,
    delta_anchor: int = 25,
    tenor: str = "1M",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Single-(pair, delta_anchor, tenor) FX risk reversal snapshot.

    RR is the call_Δ - put_Δ implied-vol differential at the chosen
    delta anchor (25Δ or 10Δ), the desk-standard skew indicator.
    Positive RR = call skew dominant (upside risk priced); negative
    RR = put skew (downside risk). Quoted in absolute vol points.

    Returns latest RR, daily / weekly / monthly absolute vol-point
    changes, rolling 252-day z-score, trailing 252-day high / low /
    percentile, observation count. Mirror of get_fx_atm_vol_level for
    the smile substrate (instrument_type='fx_vol_smile').

    Parameters
    ----------
    pair : str — six-char FX pair (e.g. 'EURUSD', 'USDMXN').
    delta_anchor : int, default 25. Choices: 25 or 10. SINGLE central
        methodology knob — defines what the RR IS (25Δ vs 10Δ skew
        are structurally different observations).
    tenor : str, default '1M' — one of '1W', '1M', '3M', '6M', '12M'.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST. Use
        'PX_BID' / 'PX_ASK' for bid/ask side.
    """
    try:
        params = FXRiskReversalInput(
            pair=pair, delta_anchor=delta_anchor, tenor=tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_RISK_REVERSAL_CONFIG_PATH)
        result = get_fx_risk_reversal(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception(
            "FX risk reversal failed for %s %sR %s", pair, delta_anchor, tenor,
        )
        return json.dumps({"error": f"FX risk reversal failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_butterfly_tool(
    pair: str,
    delta_anchor: int = 25,
    tenor: str = "1M",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Single-(pair, delta_anchor, tenor) FX butterfly snapshot.

    BF is the average OTM wing vol minus the ATM vol at the chosen
    delta anchor (25Δ or 10Δ), the desk-standard kurtosis / wing-
    richness indicator. Positive BF = wings rich (market pricing
    tail / jump risk); negative BF = wings cheap. Quoted in absolute
    vol points.

    Returns latest BF, daily / weekly / monthly absolute vol-point
    changes, rolling 252-day z-score, trailing 252-day high / low /
    percentile, observation count. Smile-substrate analog of
    risk_reversal for the wing-richness leg.

    Parameters
    ----------
    pair : str — six-char FX pair.
    delta_anchor : int, default 25. Choices: 25 or 10. SINGLE central
        methodology knob.
    tenor : str, default '1M'.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXButterflyInput(
            pair=pair, delta_anchor=delta_anchor, tenor=tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_BUTTERFLY_CONFIG_PATH)
        result = get_fx_butterfly(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception(
            "FX butterfly failed for %s %sB %s", pair, delta_anchor, tenor,
        )
        return json.dumps({"error": f"FX butterfly failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_vol_risk_premium_tool(
    pair: str,
    tenor: str = "1M",
    realized_window_basis: str = "tenor_matched",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Single-(pair, tenor) FX vol risk premium snapshot.

    VRP = implied ATM vol minus realized vol over a matched horizon.
    Positive VRP = implied rich vs realized (vol-seller positive
    carry); negative = implied cheap (vol-buyer positive carry).
    Quoted in absolute vol points. The canonical vol-carry primitive.

    Joins fx_vol implied with fx_spot-derived realized by trade_date,
    computes the premium per date, then snapshot + rolling 252d.

    Parameters
    ----------
    pair : str — six-char FX pair (e.g. 'EURUSD').
    tenor : str, default '1M' — one of '1W', '1M', '3M', '6M', '12M'.
    realized_window_basis : str, default 'tenor_matched'. Choices:
        'tenor_matched' (default, realized window = tenor trading days)
        or 'fixed_30d' (30 trading days, useful for cross-tenor
        comparability). SINGLE central methodology knob.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXVolRiskPremiumInput(
            pair=pair, tenor=tenor,
            realized_window_basis=realized_window_basis,
            lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_VOL_RISK_PREMIUM_CONFIG_PATH)
        result = get_fx_vol_risk_premium(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception(
            "FX vol risk premium failed for %s %s basis=%s",
            pair, tenor, realized_window_basis,
        )
        return json.dumps({"error": f"FX vol risk premium failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_vol_calendar_spread_tool(
    pair: str,
    short_tenor: str = "1M",
    long_tenor: str = "3M",
    spread_direction: str = "long_minus_short",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Single-(pair, short_tenor, long_tenor) FX vol calendar spread.

    Calendar spread = long-tenor ATM minus short-tenor ATM (signed
    per spread_direction). Captures vol term-structure carry:
    positive long_minus_short = contango (upward-sloping curve);
    negative = backwardation.

    Inner-joins two ATM implied histories by trade_date, then runs
    snapshot + rolling 252d on the spread series.

    Parameters
    ----------
    pair : str — six-char FX pair.
    short_tenor : str, default '1M'.
    long_tenor : str, default '3M'. Must be strictly later than
        short_tenor (in trading-day terms; Pydantic-enforced).
    spread_direction : str, default 'long_minus_short'. Choices:
        'long_minus_short' (contango +ve, the canonical convention)
        or 'short_minus_long' (backwardation +ve). SINGLE central
        methodology knob.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXVolCalendarSpreadInput(
            pair=pair, short_tenor=short_tenor, long_tenor=long_tenor,
            spread_direction=spread_direction,
            lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_VOL_CALENDAR_SPREAD_CONFIG_PATH)
        result = get_fx_vol_calendar_spread(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception(
            "FX vol calendar spread failed for %s %s/%s direction=%s",
            pair, short_tenor, long_tenor, spread_direction,
        )
        return json.dumps({"error": f"FX vol calendar spread failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_vol_smile_tool(
    pair: str,
    tenor: str = "1M",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Aggregate 5-point FX vol smile snapshot for one (pair, tenor).

    Returns the full smile: ATM (level) + 25R + 25B + 10R + 10B
    (differentials) in one call. Each point carries snapshot,
    1/5/21-day absolute changes, and rolling 252-day z-score /
    percentile / range — same recipe as the singleton primitives.

    Aggregate as_of_date = MIN of per-point latest dates so cross-
    point comparisons are aligned to a common confirmed trading day.
    Fails loud if ANY of the 5 series is missing for the requested
    (pair, tenor, lookback_days) — no silent partial smiles.

    Parameters
    ----------
    pair : str — six-char FX pair.
    tenor : str, default '1M' — one of '1W', '1M', '3M', '6M', '12M'.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXVolSmileInput(
            pair=pair, tenor=tenor, lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_VOL_SMILE_CONFIG_PATH)
        result = get_fx_vol_smile(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX vol smile failed for %s %s", pair, tenor)
        return json.dumps({"error": f"FX vol smile failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_cross_currency_basis_tool(
    pair: str,
    tenor: str = "1M",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Single-(pair, tenor) FX cross-currency basis snapshot.

    The CIP-violation spread between what FX forwards imply about the
    local-vs-USD rate spread and what the observed OIS curves show.

    SIGN CONVENTION (Bloomberg/BCRX-style, hard-locked):
      basis_bps = (fx_iyd - (local_ois - usd_ois)) * 100
      NEGATIVE basis = USD scarcity (FX-implied USD funding rate > USD
      OIS — USD funder demands premium via swap). POSITIVE = USD
      abundance. For DM pairs in normal regimes, typical -50 to -5 bp.

    First fx_agent primitive consuming the rates_agent OIS substrate
    via shared.analytics.rates_fetch.fetch_cross_market_pair.

    V1 SCOPE: G10 USD-leg only with OIS coverage — EURUSD, GBPUSD,
    USDJPY, AUDUSD, USDCAD. Other pairs fail-loud at Pydantic.

    Parameters
    ----------
    pair : str — one of {EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD}.
    tenor : str, default '1M' — one of '1W', '1M', '3M', '6M', '12M'.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXCrossCurrencyBasisInput(
            pair=pair, tenor=tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_XCCY_BASIS_CONFIG_PATH)
        result = get_fx_cross_currency_basis(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX cross-currency basis failed for %s %s", pair, tenor)
        return json.dumps({"error": f"FX cross-currency basis failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_implied_yield_differential_tool(
    pair: str,
    tenor: str = "1M",
    lookback_days: int = 365,
    field_name: Optional[str] = None,
) -> str:
    """Single-(pair, tenor) FX implied yield differential snapshot.

    The local-minus-USD rate spread implied from forward points via
    Covered Interest Parity (CIP). PM-friendly framing of the same
    math fx_carry uses internally. Sign convention HARD-LOCKED:
    positive = local rate > USD rate, negative = local rate < USD rate.

    Pair MUST contain a USD leg. Non-USD G10 crosses (EURJPY, GBPCHF,
    ...) fail-loud at compute time. Identity check vs fx_carry:
    |this.differential| == |fx_carry.carry_annualized_pct|.

    Parameters
    ----------
    pair : str — six-char FX pair with a USD leg.
    tenor : str, default '1M' — one of '1W', '1M', '3M', '6M', '12M'.
    lookback_days : int, default 365.
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXImpliedYieldDifferentialInput(
            pair=pair, tenor=tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_IMPLIED_YIELD_DIFF_CONFIG_PATH)
        result = get_fx_implied_yield_differential(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception("FX implied yield differential failed for %s %s", pair, tenor)
        return json.dumps({"error": f"FX implied yield differential failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


@mcp.tool()
def get_fx_carry_basket_tool(
    market_scope: str = "G10",
    tenor: str = "1M",
    top_n: int = 3,
    basket_construction: str = "long_short_top_n",
    lookback_days: int = 730,
    field_name: Optional[str] = None,
) -> str:
    """FX carry basket STRATEGY INDEX (not an executable backtest).

    Daily mark-to-market excess return of a paper long-top-N /
    short-bottom-N FX carry portfolio (cross-sectional equal-weight,
    rebalanced monthly = 21 trading days, 1-day signal lag for no
    look-ahead). Returns the cumulative index TimeSeries plus
    snapshot annualized return / vol / Sharpe / max drawdown.

    POSITIONING SIGNAL ONLY — NOT an executable backtest. V1 applies
    NO transaction costs / bid-ask / slippage / forward roll costs.
    Real-money equivalent typically diverges by 200-400 bp annualized
    after those frictions.

    Parameters
    ----------
    market_scope : str, default 'G10' — one of 'G10', 'EM', 'ALL'.
    tenor : str, default '1M' — forward tenor for signal + holding.
    top_n : int, default 3 — number of long (and short) legs.
    basket_construction : str, default 'long_short_top_n' — one of
        'long_short_top_n' (zero-cost cross-sectional, default) or
        'long_only_top_n' (unidirectional bet). SINGLE methodology
        knob.
    lookback_days : int, default 730 — fetch window for the strategy
        index series (2y default for ~1y of valid daily basket
        returns after rebalance warmup).
    field_name : str | None — Bloomberg field; None ⇒ PX_LAST.
    """
    try:
        params = FXCarryBasketInput(
            market_scope=market_scope, tenor=tenor, top_n=top_n,
            basket_construction=basket_construction,
            lookback_days=lookback_days, field_name=field_name,
        )
    except ValidationError as exc:
        return json.dumps({"error": f"Invalid parameters: {exc.errors()}"}, default=str)
    try:
        config = load_tool_config(FX_CARRY_BASKET_CONFIG_PATH)
        result = get_fx_carry_basket(_get_engine(), params, config=config)
    except Exception as exc:
        logger.exception(
            "FX carry basket failed for scope=%s tenor=%s top_n=%s",
            market_scope, tenor, top_n,
        )
        return json.dumps({"error": f"FX carry basket failed: {exc}"}, default=str)
    return json.dumps(result, default=str)


if __name__ == "__main__":
    logger.info("Starting FX Agent MCP server (stdio transport)...")
    mcp.run(transport="stdio")

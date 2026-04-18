"""
rates.py — Rates Page API Endpoints
=====================================

Two categories of endpoints:

**Card endpoints** (power the Rates morning-briefing page):
    /yield-snapshot, /curve-shapes, /scanner, /cross-market, /regimes

**Workspace endpoints** (power the "See more in workspace" flow):
    /detail/yield, /detail/spread, /detail/cross-market,
    /detail/butterfly, /detail/regime

Card endpoints return pre-aggregated data for multiple instruments at
once.  Workspace endpoints return full detail (including time_series for
charts) for a single query.  Both categories call the existing Python
tool functions directly — no LLM, no MCP.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Any, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.engine import Engine

from api.dependencies import get_engine, settings
from rates_agent.tools.schemas import (
    CurveSpreadInput,
    CurveSpreadOutput,
    CrossMarketSpreadInput,
    CrossMarketSpreadOutput,
    CurveRegimeInput,
    CurveRegimeOutput,
    ScannerInput,
    ButterflyInput,
    ButterflyOutput,
    YieldLevelInput,
    YieldLevelOutput,
)
from rates_agent.tools.curve_spread import calculate_curve_spread
from rates_agent.tools.cross_market_spread import calculate_cross_market_spread
from rates_agent.tools.curve_regime import classify_curve_regime
from rates_agent.tools.scanner import scan_extremes
from rates_agent.tools.butterfly import calculate_butterfly
from rates_agent.tools.yield_levels import get_yield_levels

logger = logging.getLogger("api.routes.rates")

router = APIRouter()


# ============================================================================
# CARD RESPONSE MODELS
# ============================================================================

class YieldSnapshotRow(BaseModel):
    curve_family: str
    tenor: str
    yield_pct: Optional[float] = None
    daily_change_bps: Optional[float] = None
    weekly_change_bps: Optional[float] = None
    monthly_change_bps: Optional[float] = None
    z_score: Optional[float] = None
    high_252d_pct: Optional[float] = None
    low_252d_pct: Optional[float] = None
    percentile_252d: Optional[float] = None
    as_of_date: Optional[str] = None


class YieldSnapshotResponse(BaseModel):
    rows: list[YieldSnapshotRow]
    curve_families: list[str]
    tenors: list[str]


class SparklinePoint(BaseModel):
    date: str
    value: float


class CurveShapeRow(BaseModel):
    curve_family: str
    spread_label: str
    spread_bps: Optional[float] = None
    daily_change_bps: Optional[float] = None
    z_score: Optional[float] = None
    short_tenor_yield: Optional[float] = None
    long_tenor_yield: Optional[float] = None
    as_of_date: Optional[str] = None
    sparkline: list[SparklinePoint] = Field(default_factory=list)


class CurveShapesResponse(BaseModel):
    curves: list[CurveShapeRow]


class ScannerRow(BaseModel):
    rank: int
    curve_family: str
    tenor: str
    yield_pct: Optional[float] = None
    daily_change_bps: Optional[float] = None
    z_score: Optional[float] = None
    percentile_252d: Optional[float] = None
    signal: str = ""
    as_of_date: Optional[str] = None


class ScannerResponse(BaseModel):
    summary: str
    results: list[ScannerRow]


class CrossMarketRow(BaseModel):
    spread_label: str
    curve_family_1: str
    curve_family_2: str
    tenor: str
    spread_bps: Optional[float] = None
    daily_change_bps: Optional[float] = None
    weekly_change_bps: Optional[float] = None
    monthly_change_bps: Optional[float] = None
    z_score: Optional[float] = None
    percentile_252d: Optional[float] = None
    as_of_date: Optional[str] = None
    sparkline: list[SparklinePoint] = Field(default_factory=list)


class CrossMarketResponse(BaseModel):
    pairs: list[CrossMarketRow]


class RegimeRow(BaseModel):
    curve_family: str
    lookback_period: str
    regime_tag: str
    regime_description: str = ""
    spread_label: str = ""
    front_tenor: str = ""
    back_tenor: str = ""
    front_change_bps: Optional[float] = None
    back_change_bps: Optional[float] = None
    spread_change_bps: Optional[float] = None
    as_of_date: Optional[str] = None


class RegimeResponse(BaseModel):
    regimes: list[RegimeRow]


# ============================================================================
# HELPERS
# ============================================================================

def _safe_float(value: Any, decimals: int = 4) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, decimals)
    except (TypeError, ValueError):
        return None


def _extract_sparkline(
    time_series: list[dict], max_points: int = 60
) -> list[SparklinePoint]:
    """Trim a time_series list to the last N points for sparkline rendering."""
    trimmed = time_series[-max_points:] if len(time_series) > max_points else time_series
    return [
        SparklinePoint(
            date=row["date"],
            value=row.get("spread_bps", row.get("butterfly_bps", 0)),
        )
        for row in trimmed
    ]


def _tool_result_or_raise(result: dict, context: str) -> dict:
    """Check a tool result dict for an error key and raise the
    correct HTTP status:

    - 404  — data not found (missing curve, tenor, insufficient history)
    - 503  — infrastructure failure (DB connection, query timeout)
    - 500  — unexpected / unclassified error
    """
    if "error" not in result:
        return result

    error_msg = result["error"]
    lower = error_msg.lower()

    # 404 — the query was valid but no data matched
    not_found_phrases = [
        "no data found", "missing tenor", "missing curve",
        "no overlapping observations", "no observations within",
        "all values were null", "insufficient data",
        "no instruments found",
    ]
    if any(phrase in lower for phrase in not_found_phrases):
        raise HTTPException(status_code=404, detail=f"{context}: {error_msg}")

    # 503 — infrastructure / connectivity failures
    infra_phrases = [
        "database connection failed", "connection refused",
        "timeout", "could not connect", "operational error",
    ]
    if any(phrase in lower for phrase in infra_phrases):
        raise HTTPException(status_code=503, detail=f"{context}: {error_msg}")

    # 500 — genuinely unexpected
    raise HTTPException(status_code=500, detail=f"{context}: {error_msg}")


_BATCH_YIELD_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        tenor,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = 'sovereign_benchmark'
      AND field_name      = :field_name
      AND trade_date     >= :start_date
      AND tenor IS NOT NULL
    ORDER BY curve_family, tenor, trade_date
""")


def _compute_yield_snapshot(engine: Engine) -> list[YieldSnapshotRow]:
    """Batch-compute yield metrics for all sovereign benchmark instruments."""
    Z_SCORE_WINDOW = 252
    buffer_days = int(Z_SCORE_WINDOW * 1.5)
    start_date = date.today() - timedelta(days=365 + buffer_days)

    with engine.connect() as conn:
        result = conn.execute(
            _BATCH_YIELD_SQL,
            {"field_name": "YLD_YTM_MID", "start_date": start_date.isoformat()},
        )
        rows = result.fetchall()
        columns = list(result.keys())

    raw_df = pd.DataFrame(rows, columns=columns)
    if raw_df.empty:
        return []

    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.dropna(subset=["field_value"])
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "curve_family", "tenor"], keep="last"
    )

    snapshot_rows: list[YieldSnapshotRow] = []

    for (curve_family, tenor), group in raw_df.groupby(["curve_family", "tenor"]):
        group = group.set_index("trade_date").sort_index()
        group = group.ffill(limit=5)
        yields = group["field_value"]

        if len(yields) < 2:
            continue

        current = float(yields.iloc[-1])
        as_of = yields.index[-1].strftime("%Y-%m-%d")

        daily = _safe_float((current - float(yields.iloc[-2])) * 100, 2) if len(yields) >= 2 else None
        weekly = _safe_float((current - float(yields.iloc[-6])) * 100, 2) if len(yields) >= 6 else None
        monthly = _safe_float((current - float(yields.iloc[-22])) * 100, 2) if len(yields) >= 22 else None

        z = None
        if len(yields) >= 60:
            rm = yields.rolling(window=Z_SCORE_WINDOW, min_periods=60).mean()
            rs = yields.rolling(window=Z_SCORE_WINDOW, min_periods=60).std()
            z_series = (yields - rm) / rs
            z = _safe_float(z_series.iloc[-1])

        trailing = yields.iloc[-Z_SCORE_WINDOW:] if len(yields) >= Z_SCORE_WINDOW else yields
        high_252 = _safe_float(trailing.max())
        low_252 = _safe_float(trailing.min())
        percentile = None
        if high_252 is not None and low_252 is not None and high_252 != low_252:
            percentile = round((current - low_252) / (high_252 - low_252) * 100, 1)

        snapshot_rows.append(
            YieldSnapshotRow(
                curve_family=curve_family,
                tenor=tenor,
                yield_pct=_safe_float(current),
                daily_change_bps=daily,
                weekly_change_bps=weekly,
                monthly_change_bps=monthly,
                z_score=z,
                high_252d_pct=high_252,
                low_252d_pct=low_252,
                percentile_252d=percentile,
                as_of_date=as_of,
            )
        )

    return snapshot_rows


# ============================================================================
# CARD ENDPOINTS — power the Rates morning-briefing page
# ============================================================================

TENOR_ORDER = {
    "1Y": 1, "2Y": 2, "3Y": 3, "5Y": 5, "7Y": 7, "10Y": 10,
    "15Y": 15, "20Y": 20, "25Y": 25, "30Y": 30, "50Y": 50,
}


@router.get(
    "/yield-snapshot",
    response_model=YieldSnapshotResponse,
    summary="Yield Snapshot Grid",
)
def yield_snapshot(
    engine: Engine = Depends(get_engine),
    tenors: str = Query(default="", description="Comma-separated tenor filter."),
    curves: str = Query(default="", description="Comma-separated curve filter."),
):
    try:
        rows = _compute_yield_snapshot(engine)
    except Exception as exc:
        logger.exception("yield-snapshot: DB query failed")
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")

    # An empty unfiltered result means the DB has no sovereign data at all.
    # That is an infrastructure problem, not a valid empty state.
    if not rows:
        raise HTTPException(
            status_code=503,
            detail="No sovereign benchmark data found.  The database may be empty or unreachable.",
        )

    if tenors:
        tenor_set = {t.strip() for t in tenors.split(",") if t.strip()}
        rows = [r for r in rows if r.tenor in tenor_set]
    if curves:
        curve_set = {c.strip() for c in curves.split(",") if c.strip()}
        rows = [r for r in rows if r.curve_family in curve_set]

    rows.sort(key=lambda r: (r.curve_family, TENOR_ORDER.get(r.tenor, 99)))

    return YieldSnapshotResponse(
        rows=rows,
        curve_families=sorted({r.curve_family for r in rows}),
        tenors=sorted({r.tenor for r in rows}, key=lambda t: TENOR_ORDER.get(t, 99)),
    )


@router.get(
    "/curve-shapes",
    response_model=CurveShapesResponse,
    summary="Curve Shape Strip (2s10s sparklines)",
)
def curve_shapes(
    engine: Engine = Depends(get_engine),
    curves: str = Query(default=""),
    short_tenor: str = Query(default="2Y"),
    long_tenor: str = Query(default="10Y"),
):
    curve_list = (
        [c.strip() for c in curves.split(",") if c.strip()]
        if curves else settings.RATES_CURVES
    )
    results: list[CurveShapeRow] = []
    failures = 0

    for curve_family in curve_list:
        try:
            params = CurveSpreadInput(
                curve_family=curve_family, short_tenor=short_tenor,
                long_tenor=long_tenor, lookback_days=90,
            )
            output = calculate_curve_spread(engine=engine, params=params)
            if "error" in output:
                logger.warning("curve-shapes: %s failed: %s", curve_family, output["error"])
                failures += 1
                continue

            metrics = output.get("current_metrics", {})
            ts = output.get("time_series", [])
            results.append(CurveShapeRow(
                curve_family=curve_family,
                spread_label=metrics.get("spread_label", ""),
                spread_bps=metrics.get("current_spread_bps"),
                daily_change_bps=metrics.get("daily_change_bps"),
                z_score=metrics.get("current_z_score"),
                short_tenor_yield=metrics.get("short_tenor_yield"),
                long_tenor_yield=metrics.get("long_tenor_yield"),
                as_of_date=metrics.get("as_of_date"),
                sparkline=_extract_sparkline(ts),
            ))
        except Exception:
            logger.exception("curve-shapes: error for %s", curve_family)
            failures += 1

    if not results and failures > 0:
        raise HTTPException(
            status_code=503,
            detail=f"All {failures} curve shape queries failed.  Database may be unavailable.",
        )

    return CurveShapesResponse(curves=results)


@router.get(
    "/scanner",
    response_model=ScannerResponse,
    summary="Extreme Scanner",
)
def scanner(
    engine: Engine = Depends(get_engine),
    top_n: int = Query(default=10, ge=1, le=50),
    min_z: float = Query(default=1.5, ge=0.0, alias="min_abs_z_score"),
    curves: str = Query(default=""),
):
    parsed_families = (
        [c.strip() for c in curves.split(",") if c.strip()]
        if curves else None
    )

    try:
        params = ScannerInput(
            curve_families=parsed_families, top_n=top_n, min_abs_z_score=min_z,
        )
        output = scan_extremes(engine=engine, params=params)
    except Exception as exc:
        logger.exception("scanner: tool execution failed")
        raise HTTPException(status_code=503, detail=f"Scanner failed: {exc}")

    if "error" in output:
        error_msg = output["error"]
        lower = error_msg.lower()

        # Real data/infra problem: the DB has no sovereign data at all
        if "no sovereign benchmark data found" in lower or "verify the database" in lower:
            raise HTTPException(status_code=503, detail=f"Scanner data unavailable: {error_msg}")

        # Valid empty result: scan worked, nothing exceeded the threshold
        return ScannerResponse(summary=error_msg, results=[])

    return ScannerResponse(
        summary=output.get("scan_summary", ""),
        results=[
            ScannerRow(
                rank=r.get("rank", 0), curve_family=r.get("curve_family", ""),
                tenor=r.get("tenor", ""), yield_pct=r.get("current_yield_pct"),
                daily_change_bps=r.get("daily_change_bps"),
                z_score=r.get("z_score"), percentile_252d=r.get("percentile_252d"),
                signal=r.get("signal", ""), as_of_date=r.get("as_of_date"),
            )
            for r in output.get("results", [])
        ],
    )


@router.get(
    "/cross-market",
    response_model=CrossMarketResponse,
    summary="Cross-Market Spreads (default pairs)",
)
def cross_market(engine: Engine = Depends(get_engine)):
    results: list[CrossMarketRow] = []
    failures = 0

    for cf1, cf2, tenor in settings.CROSS_MARKET_PAIRS:
        try:
            params = CrossMarketSpreadInput(
                curve_family_1=cf1, curve_family_2=cf2,
                tenor=tenor, lookback_days=90,
            )
            output = calculate_cross_market_spread(engine=engine, params=params)
            if "error" in output:
                logger.warning("cross-market: %s-%s %s failed: %s", cf1, cf2, tenor, output["error"])
                failures += 1
                continue

            metrics = output.get("current_metrics", {})
            ts = output.get("time_series", [])
            results.append(CrossMarketRow(
                spread_label=metrics.get("spread_label", ""),
                curve_family_1=cf1, curve_family_2=cf2, tenor=tenor,
                spread_bps=metrics.get("current_spread_bps"),
                daily_change_bps=metrics.get("daily_change_bps"),
                weekly_change_bps=metrics.get("weekly_change_bps"),
                monthly_change_bps=metrics.get("monthly_change_bps"),
                z_score=metrics.get("current_z_score"),
                percentile_252d=metrics.get("percentile_252d"),
                as_of_date=metrics.get("as_of_date"),
                sparkline=_extract_sparkline(ts),
            ))
        except Exception:
            logger.exception("cross-market: error for %s-%s %s", cf1, cf2, tenor)
            failures += 1

    if not results and failures > 0:
        raise HTTPException(
            status_code=503,
            detail=f"All {failures} cross-market queries failed.  Database may be unavailable.",
        )

    return CrossMarketResponse(pairs=results)


@router.get(
    "/regimes",
    response_model=RegimeResponse,
    summary="Regime Monitor",
)
def regimes(
    engine: Engine = Depends(get_engine),
    curves: str = Query(default=""),
    front_tenor: str = Query(default="2Y"),
    back_tenor: str = Query(default="10Y"),
):
    curve_list = (
        [c.strip() for c in curves.split(",") if c.strip()]
        if curves else settings.RATES_CURVES
    )
    results: list[RegimeRow] = []
    failures = 0

    for curve_family in curve_list:
        for period in settings.REGIME_PERIODS:
            try:
                params = CurveRegimeInput(
                    curve_family=curve_family, front_tenor=front_tenor,
                    back_tenor=back_tenor, lookback_period=period,
                )
                output = classify_curve_regime(engine=engine, params=params)
                if "error" in output:
                    logger.warning("regimes: %s %s failed: %s", curve_family, period, output["error"])
                    failures += 1
                    continue

                m = output.get("current_metrics", {})
                results.append(RegimeRow(
                    curve_family=curve_family, lookback_period=period,
                    regime_tag=m.get("regime_tag", "UNKNOWN"),
                    regime_description=m.get("regime_description", ""),
                    spread_label=m.get("spread_label", ""),
                    front_tenor=m.get("front_tenor", front_tenor),
                    back_tenor=m.get("back_tenor", back_tenor),
                    front_change_bps=m.get("front_change_bps"),
                    back_change_bps=m.get("back_change_bps"),
                    spread_change_bps=m.get("spread_change_bps"),
                    as_of_date=m.get("as_of_date"),
                ))
            except Exception:
                logger.exception("regimes: error for %s %s", curve_family, period)
                failures += 1

    if not results and failures > 0:
        raise HTTPException(
            status_code=503,
            detail=f"All {failures} regime queries failed.  Database may be unavailable.",
        )

    return RegimeResponse(regimes=results)


# ============================================================================
# WORKSPACE ENDPOINTS — full detail with time_series for charts
# ============================================================================

@router.get(
    "/detail/yield",
    response_model=YieldLevelOutput,
    summary="Yield Level Detail (workspace)",
    description="Full yield level detail for a single curve/tenor point.",
)
def yield_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    tenor: str = Query(..., description="e.g. '10Y'"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: str = Query(default="YLD_YTM_MID"),
):
    try:
        params = YieldLevelInput(
            curve_family=curve_family, tenor=tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = get_yield_levels(engine=engine, params=params)
    except Exception as exc:
        logger.exception("detail/yield: tool failed for %s %s", curve_family, tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Yield level for {curve_family} {tenor}")
    return result


@router.get(
    "/detail/spread",
    response_model=CurveSpreadOutput,
    summary="Curve Spread Detail (workspace)",
    description=(
        "Full 2-point spread with time_series for charting.  "
        "Returns the complete output including the z-score history."
    ),
)
def spread_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    short_tenor: str = Query(default="2Y"),
    long_tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: str = Query(default="YLD_YTM_MID"),
):
    try:
        params = CurveSpreadInput(
            curve_family=curve_family, short_tenor=short_tenor,
            long_tenor=long_tenor, lookback_days=lookback_days,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = calculate_curve_spread(engine=engine, params=params)
    except Exception as exc:
        logger.exception("detail/spread: tool failed for %s %s/%s", curve_family, short_tenor, long_tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Spread for {curve_family} {short_tenor}/{long_tenor}")
    return result


@router.get(
    "/detail/cross-market",
    response_model=CrossMarketSpreadOutput,
    summary="Cross-Market Spread Detail (workspace)",
    description=(
        "Full cross-market spread with time_series for charting.  "
        "Supports arbitrary curve pairs and tenors."
    ),
)
def cross_market_detail(
    engine: Engine = Depends(get_engine),
    curve_family_1: str = Query(..., description="e.g. 'IT_BTP'"),
    curve_family_2: str = Query(..., description="e.g. 'DE_BUND'"),
    tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: str = Query(default="YLD_YTM_MID"),
):
    try:
        params = CrossMarketSpreadInput(
            curve_family_1=curve_family_1, curve_family_2=curve_family_2,
            tenor=tenor, lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = calculate_cross_market_spread(engine=engine, params=params)
    except Exception as exc:
        logger.exception("detail/cross-market: tool failed for %s-%s %s",
                         curve_family_1, curve_family_2, tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Cross-market {curve_family_1}-{curve_family_2} {tenor}")
    return result


@router.get(
    "/detail/butterfly",
    response_model=ButterflyOutput,
    summary="Butterfly Detail (workspace)",
    description="Full 3-point butterfly with time_series for charting.",
)
def butterfly_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    short_tenor: str = Query(default="2Y"),
    belly_tenor: str = Query(default="5Y"),
    long_tenor: str = Query(default="10Y"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: str = Query(default="YLD_YTM_MID"),
):
    try:
        params = ButterflyInput(
            curve_family=curve_family, short_tenor=short_tenor,
            belly_tenor=belly_tenor, long_tenor=long_tenor,
            lookback_days=lookback_days, field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = calculate_butterfly(engine=engine, params=params)
    except Exception as exc:
        logger.exception("detail/butterfly: tool failed for %s %s/%s/%s",
                         curve_family, short_tenor, belly_tenor, long_tenor)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Butterfly for {curve_family} {short_tenor}/{belly_tenor}/{long_tenor}")
    return result


@router.get(
    "/detail/regime",
    response_model=CurveRegimeOutput,
    summary="Curve Regime Detail (workspace)",
    description="Deterministic curve-move classification for any curve and period.",
)
def regime_detail(
    engine: Engine = Depends(get_engine),
    curve_family: str = Query(..., description="e.g. 'UST'"),
    front_tenor: str = Query(default="2Y"),
    back_tenor: str = Query(default="10Y"),
    lookback_period: str = Query(default="1d", description="'1d', '5d', or '22d'"),
    field_name: str = Query(default="YLD_YTM_MID"),
):
    try:
        params = CurveRegimeInput(
            curve_family=curve_family, front_tenor=front_tenor,
            back_tenor=back_tenor, lookback_period=lookback_period,
            field_name=field_name,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = classify_curve_regime(engine=engine, params=params)
    except Exception as exc:
        logger.exception("detail/regime: tool failed for %s %s/%s %s",
                         curve_family, front_tenor, back_tenor, lookback_period)
        raise HTTPException(status_code=503, detail=f"Database error: {exc}")

    _tool_result_or_raise(result, f"Regime for {curve_family} {front_tenor}/{back_tenor} ({lookback_period})")
    return result

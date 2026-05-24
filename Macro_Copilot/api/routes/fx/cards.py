from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.engine import Engine

from api.dependencies import get_engine
from fx_agent.forwards.tools.forward_curve import get_fx_forward_curve
from fx_agent.forwards.tools.forward_curve.schemas import FXForwardCurveInput
from fx_agent.forwards.tools.fx_carry import get_fx_carry
from fx_agent.forwards.tools.fx_carry.schemas import FXCarryInput
from fx_agent.spot.tools.scanner import run_fx_scanner
from fx_agent.spot.tools.schemas import FXScannerInput
from fx_agent.spot.tools.spot_levels import get_fx_spot_level
from fx_agent.spot.tools.spot_levels.schemas import FXSpotLevelInput

router = APIRouter()


class FXCardsResponse(BaseModel):
    scanner: dict


@router.get("/scanner", summary="FX Spot Scanner")
def scanner(
    market_scope: Optional[str] = Query(default=None),
    top_n: int = Query(default=10, ge=1, le=50),
    engine: Engine = Depends(get_engine),
):
    try:
        return run_fx_scanner(
            engine,
            FXScannerInput(market_scope=market_scope, top_n=top_n)
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX scanner failed: {exc}")


@router.get("/spot-level", summary="FX Spot Level")
def spot_level(
    pair: str = Query(..., description="FX pair, e.g. EURUSD"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    engine: Engine = Depends(get_engine),
):
    try:
        return get_fx_spot_level(
            engine,
            FXSpotLevelInput(pair=pair, lookback_days=lookback_days)
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX spot level failed: {exc}")


@router.get("/carry", summary="FX Carry Scanner")
def carry(
    tenor: str = Query(
        default="1M",
        description="Forward tenor: one of 1W, 1M, 3M, 6M, 12M.",
    ),
    rank_by: str = Query(
        default="carry_signed",
        description=(
            "Row ordering. carry_signed = descending by annualised "
            "carry (default). abs_carry = descending by absolute carry "
            "magnitude. abs_z_score = descending by absolute carry "
            "z-score magnitude (None z-scores at the end)."
        ),
    ),
    top_n: Optional[int] = Query(
        default=None,
        ge=1,
        description=(
            "Truncate to top-N rows after sorting. None returns every "
            "pair in the substrate."
        ),
    ),
    lookback_days: int = Query(
        default=365,
        ge=30,
        le=7300,
        description=(
            "DB fetch window for the z-score history. Does NOT control "
            "the rolling z-score window (252 trading days, fixed in "
            "config)."
        ),
    ),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field override for both spot and forward points. "
            "None falls through to PX_LAST."
        ),
    ),
    engine: Engine = Depends(get_engine),
):
    try:
        return get_fx_carry(
            engine,
            FXCarryInput(
                tenor=tenor,
                rank_by=rank_by,
                top_n=top_n,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX carry failed: {exc}")


@router.get("/forward-curve", summary="FX Forward Curve")
def forward_curve(
    pair: str = Query(
        ...,
        description=(
            "FX pair. G10 majors with forward points ingested: EURUSD, "
            "GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF."
        ),
    ),
    lookback_days: int = Query(
        default=365,
        ge=30,
        le=7300,
        description=(
            "DB fetch window for the z-score history. Does NOT control "
            "the rolling z-score window (252 trading days)."
        ),
    ),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field override for both spot and forward points. "
            "None falls through to PX_LAST."
        ),
    ),
    engine: Engine = Depends(get_engine),
):
    try:
        return get_fx_forward_curve(
            engine,
            FXForwardCurveInput(
                pair=pair,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"FX forward curve failed: {exc}"
        )

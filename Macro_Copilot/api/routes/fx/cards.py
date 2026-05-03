from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

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
):
    try:
        output = run_fx_scanner(
            FXScannerInput(market_scope=market_scope, top_n=top_n)
        )
        return output.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX scanner failed: {exc}")


@router.get("/spot-level", summary="FX Spot Level")
def spot_level(
    pair: str = Query(..., description="FX pair, e.g. EURUSD"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
):
    try:
        output = get_fx_spot_level(
            FXSpotLevelInput(pair=pair, lookback_days=lookback_days)
        )
        return output.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX spot level failed: {exc}")


@router.get("/carry", summary="FX Carry")
def carry(
    tenor: str = Query(default="1M", description="Forward tenor, e.g. 1W, 1M, 3M, 6M"),
):
    try:
        output = get_fx_carry(FXCarryInput(tenor=tenor))
        return output.model_dump()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX carry failed: {exc}")
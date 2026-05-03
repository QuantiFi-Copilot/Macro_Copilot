"""
detail.py — FX Workspace Detail Endpoints
=========================================

Full-detail endpoints that power the FX "See more in workspace" flow.

These endpoints expose deterministic FX analytics for workspace/detail
views. They call Python tools directly and return complete structured
outputs from the FX Agent.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from fx_agent.forwards.tools.forward_curve import get_fx_forward_curve
from fx_agent.forwards.tools.forward_curve.schemas import (
    FXForwardCurveInput,
    FXForwardCurveOutput,
)
from fx_agent.forwards.tools.fx_carry import get_fx_carry
from fx_agent.forwards.tools.fx_carry.schemas import FXCarryInput, FXCarryOutput
from fx_agent.macro.tools.trade_setup import get_fx_trade_setup
from fx_agent.macro.tools.trade_setup.schemas import (
    FXTradeSetupInput,
    FXTradeSetupOutput,
)
from fx_agent.spot.tools.spot_levels import get_fx_spot_level
from fx_agent.spot.tools.spot_levels.schemas import FXSpotLevelInput, FXSpotLevelOutput
from fx_agent.vol.tools.realized_vol import get_fx_realized_vol
from fx_agent.vol.tools.realized_vol.schemas import (
    FXRealizedVolInput,
    FXRealizedVolOutput,
)

logger = logging.getLogger("api.routes.fx.detail")

router = APIRouter()


def _tool_result_or_raise(result: object, context: str) -> object:
    """
    Normalize deterministic tool errors into API errors.

    Current FX tools mostly raise exceptions directly or return Pydantic
    outputs. This helper is kept for consistency with Rates detail routes
    and future tools that may return error envelopes.
    """
    if isinstance(result, dict) and "error" in result:
        error_msg = str(result["error"])
        lower = error_msg.lower()

        not_found_phrases = [
            "no data found",
            "no fx spot data found",
            "no observations",
            "no overlapping observations",
            "all values were null",
            "insufficient data",
            "unsupported pair",
            "unsupported tenor",
        ]
        if any(phrase in lower for phrase in not_found_phrases):
            raise HTTPException(status_code=404, detail=f"{context}: {error_msg}")

        infra_phrases = [
            "database connection failed",
            "connection refused",
            "timeout",
            "could not connect",
            "operational error",
        ]
        if any(phrase in lower for phrase in infra_phrases):
            raise HTTPException(status_code=503, detail=f"{context}: {error_msg}")

        user_input_phrases = [
            "invalid pair",
            "invalid tenor",
            "conflicts with the yaml",
        ]
        if any(phrase in lower for phrase in user_input_phrases):
            raise HTTPException(status_code=422, detail=f"{context}: {error_msg}")

        raise HTTPException(status_code=500, detail=f"{context}: {error_msg}")

    return result


@router.get(
    "/detail/spot-level",
    response_model=FXSpotLevelOutput,
    summary="FX Spot Level Detail (workspace)",
)
def fx_spot_level_detail(
    pair: str = Query(..., description="FX pair, e.g. EURUSD, GBPUSD, USDJPY."),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(
        default=None,
        description=(
            "Bloomberg field mnemonic. Omit to use the FX spot tool default "
            "field, currently PX_LAST."
        ),
    ),
):
    try:
        params = FXSpotLevelInput(
            pair=pair,
            lookback_days=lookback_days,
            field_name=field_name or "PX_LAST",
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = get_fx_spot_level(params=params)
    except ValueError as exc:
        logger.info("detail/spot-level: no data for %s", pair)
        raise HTTPException(status_code=404, detail=f"FX spot level for {pair}: {exc}")
    except Exception as exc:
        logger.exception("detail/spot-level: tool failed for %s", pair)
        raise HTTPException(status_code=503, detail=f"FX spot level failed: {exc}")

    _tool_result_or_raise(result, f"FX spot level for {pair}")
    return result


@router.get(
    "/detail/carry",
    response_model=FXCarryOutput,
    summary="FX Carry Detail (workspace)",
)
def fx_carry_detail(
    tenor: str = Query(default="1M", description="Forward tenor, e.g. 1W, 1M, 3M, 6M."),
):
    try:
        params = FXCarryInput(tenor=tenor)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = get_fx_carry(params=params)
    except ValueError as exc:
        logger.info("detail/carry: no data for tenor=%s", tenor)
        raise HTTPException(status_code=404, detail=f"FX carry {tenor}: {exc}")
    except Exception as exc:
        logger.exception("detail/carry: tool failed for tenor=%s", tenor)
        raise HTTPException(status_code=503, detail=f"FX carry failed: {exc}")

    _tool_result_or_raise(result, f"FX carry for tenor={tenor}")
    return result


@router.get(
    "/detail/forward-curve",
    response_model=FXForwardCurveOutput,
    summary="FX Forward Curve Detail (workspace)",
)
def fx_forward_curve_detail(
    pair: str = Query(..., description="FX pair, e.g. EURUSD, GBPUSD, USDJPY."),
):
    try:
        params = FXForwardCurveInput(pair=pair)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = get_fx_forward_curve(params=params)
    except ValueError as exc:
        logger.info("detail/forward-curve: no data for %s", pair)
        raise HTTPException(status_code=404, detail=f"FX forward curve for {pair}: {exc}")
    except Exception as exc:
        logger.exception("detail/forward-curve: tool failed for %s", pair)
        raise HTTPException(status_code=503, detail=f"FX forward curve failed: {exc}")

    _tool_result_or_raise(result, f"FX forward curve for {pair}")
    return result


@router.get(
    "/detail/realized-vol",
    response_model=FXRealizedVolOutput,
    summary="FX Realized Vol Detail (workspace)",
)
def fx_realized_vol_detail(
    pair: str = Query(..., description="FX pair, e.g. EURUSD, GBPUSD, USDJPY."),
    window_observations: int = Query(default=21, ge=5, le=252),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    return_type: str = Query(default="log_return", pattern="^(log_return|simple_return)$"),
    field_name: Optional[str] = Query(default=None),
):
    try:
        params = FXRealizedVolInput(
            pair=pair,
            window_observations=window_observations,
            lookback_days=lookback_days,
            return_type=return_type,
            field_name=field_name or "PX_LAST",
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = get_fx_realized_vol(params=params)
    except ValueError as exc:
        logger.info("detail/realized-vol: no data for %s", pair)
        raise HTTPException(status_code=404, detail=f"FX realized vol for {pair}: {exc}")
    except Exception as exc:
        logger.exception("detail/realized-vol: tool failed for %s", pair)
        raise HTTPException(status_code=503, detail=f"FX realized vol failed: {exc}")

    _tool_result_or_raise(result, f"FX realized vol for {pair}")
    return result


@router.get(
    "/detail/trade-setup",
    response_model=FXTradeSetupOutput,
    summary="FX Trade Setup Detail (workspace)",
)
def fx_trade_setup_detail(
    pair: str = Query(..., description="FX pair, e.g. EURUSD, GBPUSD, USDJPY."),
    tenor: str = Query(default="1M", description="Carry tenor, e.g. 1W, 1M, 3M, 6M."),
    vol_window_observations: int = Query(default=21, ge=5, le=252),
    lookback_days: int = Query(default=365, ge=30, le=7300),
):
    try:
        params = FXTradeSetupInput(
            pair=pair,
            tenor=tenor,
            vol_window_observations=vol_window_observations,
            lookback_days=lookback_days,
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid parameters: {exc}")

    try:
        result = get_fx_trade_setup(params=params)
    except ValueError as exc:
        logger.info("detail/trade-setup: no data for %s", pair)
        raise HTTPException(status_code=404, detail=f"FX trade setup for {pair}: {exc}")
    except Exception as exc:
        logger.exception("detail/trade-setup: tool failed for %s", pair)
        raise HTTPException(status_code=503, detail=f"FX trade setup failed: {exc}")

    _tool_result_or_raise(result, f"FX trade setup for {pair}")
    return result

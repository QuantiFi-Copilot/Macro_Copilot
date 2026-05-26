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
from fx_agent.ndf.tools.ndf_implied_carry import calculate_fx_ndf_implied_carry
from fx_agent.ndf.tools.ndf_implied_carry.schemas import FXNDFImpliedCarryInput
from fx_agent.ndf.tools.ndf_outright import get_fx_ndf_outright
from fx_agent.ndf.tools.ndf_outright.schemas import FXNDFOutrightInput
from fx_agent.vol.tools.atm_vol_level import get_fx_atm_vol_level
from fx_agent.vol.tools.atm_vol_level.schemas import FXAtmVolLevelInput
from fx_agent.vol.tools.vol_scanner import run_fx_vol_scanner
from fx_agent.vol.tools.vol_scanner.schemas import FXVolScannerInput
from fx_agent.vol.tools.vol_term_structure import get_fx_vol_term_structure
from fx_agent.vol.tools.vol_term_structure.schemas import FXVolTermStructureInput
from fx_agent.vol.tools.vol_z_score import get_fx_vol_z_score
from fx_agent.vol.tools.vol_z_score.schemas import FXVolZScoreInput
from fx_agent.spot.tools.drawdown import calculate_fx_drawdown
from fx_agent.spot.tools.drawdown.schemas import FXDrawdownInput
from fx_agent.spot.tools.fx_panel import calculate_fx_panel
from fx_agent.spot.tools.fx_panel.schemas import FXPanelInput
from fx_agent.spot.tools.realized_vol import get_fx_realized_vol
from fx_agent.spot.tools.realized_vol.schemas import FXRealizedVolInput
from fx_agent.spot.tools.returns_series import get_fx_returns_series
from fx_agent.spot.tools.returns_series.schemas import FXReturnsSeriesInput
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
    market_scope: str = Query(
        default="G10",
        description=(
            "Universe filter. 'G10' (default, 6 majors), 'EM' (6 EM "
            "deliverable: MXN/ZAR/TRY/PLN/HUF/PHP), 'ALL' (12 = G10 + EM). "
            "NDFs (BRL/KRW/IDR/CNY/INR) are not included — separate "
            "compute path coming in Phase D."
        ),
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
                market_scope=market_scope,
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


@router.post(
    "/panel",
    summary="FX Cross-Sectional Spot Panel",
    description=(
        "Assemble a typed Panel of FX spot levels for a market_scope "
        "subset (G10 / EM / G10_CROSSES / ALL). Returns metadata "
        "(columns = pair names, date range, observation count, "
        "per-column units) — the typed Panel artifact itself is "
        "extracted by the workflow executor and is not part of the "
        "HTTP body."
    ),
)
def panel(
    payload: FXPanelInput,
    engine: Engine = Depends(get_engine),
):
    try:
        result = calculate_fx_panel(engine, payload)
        # Drop the typed Panel from the wire shape — the HTTP client
        # gets the summary metadata only. The typed artifact path is
        # the workflow executor's, not the REST surface's.
        result.pop("panel", None)
        return result
    except ValueError as exc:
        # Substrate-level fail-loud cases (empty fetch, bad scope,
        # bad policy, empty post-policy panel) → 422 Unprocessable
        # Entity rather than 503; the request was syntactically OK
        # but semantically incompatible with the substrate state.
        raise HTTPException(status_code=422, detail=f"FX panel failed: {exc}")
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"FX panel failed: {exc}"
        )


@router.get("/returns-series", summary="FX Log-Returns Series")
def returns_series(
    pair: str = Query(..., description="Six-char FX pair (e.g. 'EURUSD')."),
    horizon: str = Query(
        default="daily",
        description="One of 'daily' / 'weekly' / 'monthly'.",
    ),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    try:
        return get_fx_returns_series(
            engine,
            FXReturnsSeriesInput(
                pair=pair,
                horizon=horizon,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except ValueError as exc:
        # Substrate-level fail-loud (empty fetch, etc.) → 422
        raise HTTPException(status_code=422, detail=f"FX returns series failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX returns series failed: {exc}")


@router.get("/drawdown", summary="FX Drawdown")
def drawdown(
    pair: str = Query(..., description="Six-char FX pair (e.g. 'EURUSD')."),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    try:
        return calculate_fx_drawdown(
            engine,
            FXDrawdownInput(
                pair=pair,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX drawdown failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX drawdown failed: {exc}")


@router.get("/realized-vol", summary="FX Rolling Realized Vol")
def realized_vol(
    pair: str = Query(..., description="Six-char FX pair (e.g. 'EURUSD')."),
    window_days: int = Query(
        default=30, ge=5, le=504,
        description="Rolling vol window in trading days.",
    ),
    lookback_days: int = Query(default=365, ge=60, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    try:
        return get_fx_realized_vol(
            engine,
            FXRealizedVolInput(
                pair=pair,
                window_days=window_days,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX realized vol failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX realized vol failed: {exc}")


@router.get("/ndf-outright", summary="FX NDF Outright Snapshot")
def ndf_outright(
    ndf_code: str = Query(..., description="NDF family code (e.g. 'CCN+', 'NTN+')"),
    tenor: str = Query(default="1M"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    """Single-(NDF family, tenor) outright snapshot with rolling stats."""
    try:
        return get_fx_ndf_outright(
            engine,
            FXNDFOutrightInput(
                ndf_code=ndf_code,
                tenor=tenor,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX NDF outright failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX NDF outright failed: {exc}")


@router.get("/ndf-implied-carry", summary="FX NDF Implied Carry Scanner")
def ndf_implied_carry(
    tenor: str = Query(default="1M"),
    spot_convention: str = Query(
        default="settlement",
        description=(
            "'settlement' (default) = NDF official spot reference. "
            "'offshore_tradable' = USDCNH for CCN+ (others fall back to settlement)."
        ),
    ),
    rank_by: str = Query(
        default="carry_signed",
        description="One of 'carry_signed', 'abs_carry', 'abs_z_score'.",
    ),
    top_n: Optional[int] = Query(default=None, ge=1, le=20),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    """Cross-sectional FX NDF implied carry over the 6 NDF families."""
    try:
        return calculate_fx_ndf_implied_carry(
            engine,
            FXNDFImpliedCarryInput(
                tenor=tenor,
                spot_convention=spot_convention,
                rank_by=rank_by,
                top_n=top_n,
                lookback_days=lookback_days,
                field_name=field_name,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX NDF implied carry failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX NDF implied carry failed: {exc}")


@router.get("/ndf-carry-scanner", summary="FX NDF Carry Scanner (Top-N by abs z-score)")
def ndf_carry_scanner(
    tenor: str = Query(default="1M"),
    top_n: int = Query(default=5, ge=1, le=20),
    spot_convention: str = Query(default="settlement"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    engine: Engine = Depends(get_engine),
):
    """Top-N most extreme NDFs by absolute z-score of implied carry."""
    try:
        return calculate_fx_ndf_implied_carry(
            engine,
            FXNDFImpliedCarryInput(
                tenor=tenor,
                spot_convention=spot_convention,
                rank_by="abs_z_score",
                top_n=top_n,
                lookback_days=lookback_days,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX NDF carry scanner failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX NDF carry scanner failed: {exc}")


@router.get("/atm-vol-level", summary="FX ATM Vol Level Snapshot")
def atm_vol_level(
    pair: str = Query(..., description="FX pair, e.g. EURUSD."),
    tenor: str = Query(default="1M"),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    """Single-(pair, tenor) ATM vol snapshot with rolling 252d stats."""
    try:
        return get_fx_atm_vol_level(
            engine,
            FXAtmVolLevelInput(
                pair=pair, tenor=tenor, lookback_days=lookback_days, field_name=field_name,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX ATM vol level failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX ATM vol level failed: {exc}")


@router.get("/vol-term-structure", summary="FX Vol Term Structure")
def vol_term_structure(
    pair: str = Query(..., description="FX pair, e.g. EURUSD."),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    """ATM vol curve across all standard tenors (1W-12M) for one pair."""
    try:
        return get_fx_vol_term_structure(
            engine,
            FXVolTermStructureInput(
                pair=pair, lookback_days=lookback_days, field_name=field_name,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX vol term structure failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX vol term structure failed: {exc}")


@router.get("/vol-scanner", summary="FX Cross-Sectional Vol Scanner")
def vol_scanner(
    tenor: str = Query(default="1M"),
    market_scope: str = Query(default="G10", description="G10 / EM / G10_CROSSES / ALL"),
    rank_by: str = Query(default="vol_signed", description="vol_signed / abs_vol / abs_z_score"),
    top_n: Optional[int] = Query(default=None, ge=1, le=50),
    lookback_days: int = Query(default=365, ge=30, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    """Cross-sectional ranking of ATM vol across pairs at one tenor."""
    try:
        return run_fx_vol_scanner(
            engine,
            FXVolScannerInput(
                tenor=tenor, market_scope=market_scope, rank_by=rank_by,
                top_n=top_n, lookback_days=lookback_days, field_name=field_name,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX vol scanner failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX vol scanner failed: {exc}")


@router.get("/vol-z-score-series", summary="FX Vol Rolling Z-Score Time Series")
def vol_z_score_series(
    pair: str = Query(..., description="FX pair, e.g. EURUSD."),
    tenor: str = Query(default="1M"),
    lookback_days: int = Query(default=730, ge=60, le=7300),
    field_name: Optional[str] = Query(default=None),
    engine: Engine = Depends(get_engine),
):
    """Rolling 252-day z-score time series of ATM vol for one (pair, tenor)."""
    try:
        return get_fx_vol_z_score(
            engine,
            FXVolZScoreInput(
                pair=pair, tenor=tenor, lookback_days=lookback_days, field_name=field_name,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"FX vol z-score failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"FX vol z-score failed: {exc}")

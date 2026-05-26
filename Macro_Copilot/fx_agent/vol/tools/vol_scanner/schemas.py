"""Pydantic schemas for the FX vol cross-sectional scanner.

For one tenor, returns one row per pair in the requested market_scope
with current ATM vol, rolling 252-day z-score / percentile / range,
ranked by signed vol level / absolute vol level / absolute z-score.
Mirror of scan_fx_spot (run_fx_scanner) but for the ATM vol substrate.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from fx_agent.vol._shared import FXVolMarketScope, FXVolStandardTenor


FXVolRankBy = Literal["vol_signed", "abs_vol", "abs_z_score"]


class FXVolScannerInput(BaseModel):
    tenor: FXVolStandardTenor = Field(
        default="1M",
        description=(
            "ATM vol tenor for the cross-section. One of '1W', '1M', "
            "'3M', '6M', '12M'."
        ),
    )
    market_scope: FXVolMarketScope = Field(
        default="G10",
        description=(
            "Universe filter. 'G10' = 6 G10 majors. 'EM' = 17 EM/NDF-"
            "currency pairs. 'G10_CROSSES' = 11 G10 cross pairs (added "
            "in Tradability 2026-05-26). 'ALL' = G10 + EM + crosses = "
            "34 pairs at standard tenors."
        ),
    )
    rank_by: FXVolRankBy = Field(
        default="vol_signed",
        description=(
            "How to order the output rows. 'vol_signed' = descending "
            "by current vol level (the highest vol first). 'abs_vol' "
            "= descending by absolute vol level. 'abs_z_score' = "
            "descending by absolute z-score (richness vs own history)."
        ),
    )
    top_n: Optional[int] = Field(
        default=None, ge=1,
        description=(
            "Truncate to top-N after sorting. None returns every pair "
            "in scope."
        ),
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description="DB fetch window for z-score history.",
    )
    field_name: Optional[str] = Field(
        default=None,
        description="Bloomberg field override. None falls through to PX_LAST.",
    )


class FXVolScannerRow(BaseModel):
    pair: str
    tenor: str
    vendor_ticker: str
    as_of_date: str
    current_atm_vol_pct: float
    z_score: Optional[float]
    percentile_252d: Optional[float]
    high_252d: Optional[float]
    low_252d: Optional[float]
    observation_count: int
    rank: int


class FXVolScannerOutput(BaseModel):
    tenor: str
    market_scope: str
    rows: list[FXVolScannerRow]

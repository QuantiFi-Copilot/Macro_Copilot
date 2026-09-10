"""Pydantic schemas for the FX vol skew scanner.

Phase F1 (2026-05-27).

Ranks pairs across a market_scope by their current
risk-reversal / butterfly skew level. Used to identify
cross-sectional skew dislocations (e.g. pairs trading rich
on calls vs own history).

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR13.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


FXVolSkewScannerMarketScope = Literal["G10", "EM", "G10_CROSSES", "ALL"]
FXVolSkewScannerTenor = Literal["1W", "1M", "3M", "6M", "12M"]
FXVolSkewScannerSmilePoint = Literal["25R", "25B", "10R", "10B"]
FXVolSkewScannerRankBy = Literal["skew_signed", "abs_skew", "abs_z_score"]


class FXVolSkewScannerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_scope: FXVolSkewScannerMarketScope = Field(
        default="G10",
        description="Universe filter.",
    )
    tenor: FXVolSkewScannerTenor = Field(
        default="1M",
        description="Vol tenor.",
    )
    smile_point: FXVolSkewScannerSmilePoint = Field(
        default="25R",
        description=(
            "Smile point. '25R'/'10R' = risk reversal (call-put vol "
            "spread). '25B'/'10B' = butterfly (smile wings vs ATM)."
        ),
    )
    rank_by: FXVolSkewScannerRankBy = Field(
        default="skew_signed",
        description=(
            "'skew_signed' = descending by signed skew (most-positive "
            "first). 'abs_skew' = descending by |skew|. 'abs_z_score' "
            "= descending by |z|."
        ),
    )
    top_n: Optional[int] = Field(
        default=None, ge=1,
        description="Truncate to top-N after sorting.",
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description="DB fetch window passed to fx_vol_panel.",
    )


class FXVolSkewScannerRow(BaseModel):
    pair: str
    tenor: str
    smile_point: str
    as_of_date: str
    current_skew_vol_pct: float
    z_score: Optional[float]
    percentile_252d: Optional[float]
    high_252d_pct: Optional[float]
    low_252d_pct: Optional[float]
    observation_count: int
    rank: int


class FXVolSkewScannerOutput(BaseModel):
    tenor: str
    smile_point: str
    market_scope: str
    rows: List[FXVolSkewScannerRow]


__all__ = [
    "FXVolSkewScannerInput",
    "FXVolSkewScannerOutput",
    "FXVolSkewScannerRow",
    "FXVolSkewScannerMarketScope",
    "FXVolSkewScannerTenor",
    "FXVolSkewScannerSmilePoint",
    "FXVolSkewScannerRankBy",
]

"""Pydantic schemas for the FX implied yield differential scanner.

Phase F1 (2026-05-27).

Ranks pairs across a market_scope by their current FX-implied
(local minus USD) annualised rate spread in PERCENT. The sign
convention matches implied_yield_differential primitive: for
xxxUSD pairs (EURUSD, GBPUSD, AUDUSD, NZDUSD) the local
currency is the base and the sign is flipped so the output is
LOCAL - USD; for USDxxx pairs (USDJPY, USDCAD, USDMXN, etc.)
the local currency is the quote and no flip is needed.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR13.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


FXIYDScannerMarketScope = Literal["G10", "EM", "ALL"]
FXIYDScannerTenor = Literal["1W", "1M", "3M", "6M", "12M"]
FXIYDScannerRankBy = Literal["iyd_signed", "abs_iyd", "abs_z_score"]


class FXIYDScannerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_scope: FXIYDScannerMarketScope = Field(
        default="G10",
        description="Universe filter (G10 / EM / ALL).",
    )
    tenor: FXIYDScannerTenor = Field(
        default="1M",
        description="Forward tenor.",
    )
    rank_by: FXIYDScannerRankBy = Field(
        default="iyd_signed",
        description=(
            "'iyd_signed' = descending by signed iyd (highest "
            "local-minus-USD spread first). 'abs_iyd' = descending "
            "by |iyd|. 'abs_z_score' = descending by |z|."
        ),
    )
    top_n: Optional[int] = Field(
        default=None, ge=1,
        description="Truncate to top-N after sorting.",
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description="DB fetch window for the iyd time series.",
    )


class FXIYDScannerRow(BaseModel):
    pair: str
    tenor: str
    as_of_date: str
    current_iyd_pct: float
    z_score: Optional[float]
    percentile_252d: Optional[float]
    high_252d_pct: Optional[float]
    low_252d_pct: Optional[float]
    observation_count: int
    rank: int


class FXIYDScannerOutput(BaseModel):
    tenor: str
    market_scope: str
    rows: List[FXIYDScannerRow]


__all__ = [
    "FXIYDScannerInput",
    "FXIYDScannerOutput",
    "FXIYDScannerRow",
    "FXIYDScannerMarketScope",
    "FXIYDScannerTenor",
    "FXIYDScannerRankBy",
]

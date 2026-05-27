"""Pydantic schemas for the FX vol calendar spread scanner.

Phase F1 (2026-05-27).

Ranks pairs across a market_scope by their current (front - back)
ATM vol spread. Negative spread = vol-curve inverted (front-end
elevated vs back); positive spread = normal upward-sloping vol
term structure.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR13.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


FXCalendarSpreadScannerMarketScope = Literal["G10", "EM", "G10_CROSSES", "ALL"]
FXCalendarSpreadScannerTenor = Literal["1W", "1M", "3M", "6M", "12M"]
FXCalendarSpreadScannerRankBy = Literal["spread_signed", "abs_spread", "abs_z_score"]


class FXCalendarSpreadScannerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market_scope: FXCalendarSpreadScannerMarketScope = Field(
        default="G10",
        description="Universe filter.",
    )
    front_tenor: FXCalendarSpreadScannerTenor = Field(
        default="1M",
        description="Front leg of the calendar spread (e.g. '1M').",
    )
    back_tenor: FXCalendarSpreadScannerTenor = Field(
        default="3M",
        description="Back leg of the calendar spread (e.g. '3M').",
    )
    rank_by: FXCalendarSpreadScannerRankBy = Field(
        default="spread_signed",
        description=(
            "'spread_signed' = descending by (front - back) (largest "
            "POSITIVE = front-elevated). 'abs_spread' = descending "
            "by |spread|. 'abs_z_score' = descending by |z|."
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

    @model_validator(mode="after")
    def _front_back_distinct(self) -> "FXCalendarSpreadScannerInput":
        if self.front_tenor == self.back_tenor:
            raise ValueError(
                f"front_tenor ({self.front_tenor}) must differ from "
                f"back_tenor ({self.back_tenor})."
            )
        return self


class FXCalendarSpreadScannerRow(BaseModel):
    pair: str
    front_tenor: str
    back_tenor: str
    as_of_date: str
    current_front_vol_pct: float
    current_back_vol_pct: float
    current_spread_vol_pct: float
    z_score: Optional[float]
    percentile_252d: Optional[float]
    high_252d_pct: Optional[float]
    low_252d_pct: Optional[float]
    observation_count: int
    rank: int


class FXCalendarSpreadScannerOutput(BaseModel):
    front_tenor: str
    back_tenor: str
    market_scope: str
    rows: List[FXCalendarSpreadScannerRow]


__all__ = [
    "FXCalendarSpreadScannerInput",
    "FXCalendarSpreadScannerOutput",
    "FXCalendarSpreadScannerRow",
    "FXCalendarSpreadScannerMarketScope",
    "FXCalendarSpreadScannerTenor",
    "FXCalendarSpreadScannerRankBy",
]

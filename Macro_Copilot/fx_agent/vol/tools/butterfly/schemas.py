"""Pydantic schemas for the FX butterfly snapshot tool.

A butterfly (BF) is the implied-vol differential between the average
of the OTM wings and the ATM at a given delta — a desk-recognized
kurtosis / wing-richness indicator (PR5). Positive BF = wings rich
(market pricing tail risk); negative BF = wings cheap. Quoted in vol
points.

Single-(pair, delta_anchor, tenor) snapshot primitive with rolling
252-day z-score / percentile / range. Analog of risk_reversal for the
butterfly leg of the smile. Central knob (PR8) = delta_anchor ∈ {25, 10}.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR8, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from fx_agent.vol._shared import FXSmileDelta, FXVolStandardTenor


class FXButterflyInput(BaseModel):
    pair: str = Field(
        ...,
        description="FX pair, e.g. 'EURUSD', 'USDMXN'.",
    )
    delta_anchor: FXSmileDelta = Field(
        default=25,
        description=(
            "Delta anchor for the butterfly. 25 = standard 25Δ BF, 10 = "
            "wing-side 10Δ BF (kurtosis indicator). Default 25 — desk-"
            "standard wing monitor delta. The SINGLE central methodology "
            "knob (PR8)."
        ),
    )
    tenor: FXVolStandardTenor = Field(
        default="1M",
        description="Smile tenor. One of '1W', '1M', '3M', '6M', '12M'.",
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description="DB fetch window for z-score history.",
    )
    field_name: Optional[str] = Field(
        default=None,
        description="Bloomberg field; None ⇒ PX_LAST. Use 'PX_BID'/'PX_ASK' for bid/ask side.",
    )


class FXButterflyMetrics(BaseModel):
    as_of_date: str
    pair: str
    delta_anchor: int
    smile_point: str = Field(
        ...,
        description="Resolved smile_point identifier ('25B' or '10B').",
    )
    tenor: str
    vendor_ticker: str
    current_butterfly_vol_pts: float = Field(
        ...,
        description=(
            "Current BF in vol points (positive = wings rich; "
            "negative = wings cheap). ABSOLUTE vol points convention."
        ),
    )
    daily_change_vol_pts: Optional[float] = None
    weekly_change_vol_pts: Optional[float] = None
    monthly_change_vol_pts: Optional[float] = None
    z_score: Optional[float] = None
    high_252d: Optional[float] = None
    low_252d: Optional[float] = None
    percentile_252d: Optional[float] = None
    observation_count: int


class FXButterflyOutput(BaseModel):
    current_metrics: FXButterflyMetrics

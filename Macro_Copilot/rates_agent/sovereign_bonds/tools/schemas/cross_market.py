"""Pydantic schemas for the cross-market spread tool."""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class CrossMarketSpreadInput(BaseModel):
    """Parameters for computing the yield differential between the same tenor
    on two different sovereign curves (e.g. UST 10Y − BUND 10Y)."""
    curve_family_1: str = Field(..., description="The first (numerator) curve family. The spread is computed as curve_family_1 − curve_family_2.")
    curve_family_2: str = Field(..., description="The second (denominator) curve family.")
    tenor: str = Field(..., description="The tenor point to compare across markets, e.g. '2Y', '5Y', '10Y', '30Y'.")
    lookback_days: int = Field(default=365, ge=30, le=7300, description="Calendar days of displayed history.")
    field_name: str = Field(default="YLD_YTM_MID", description="The observation field to use. Must match the exact value in the database.")

    @model_validator(mode="after")
    def _curves_must_differ(self) -> "CrossMarketSpreadInput":
        if self.curve_family_1 == self.curve_family_2:
            raise ValueError(f"curve_family_1 and curve_family_2 must be different, but both are '{self.curve_family_1}'. For same-curve spreads, use the curve_spread tool instead.")
        return self


class CrossMarketSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for a cross-market yield differential."""
    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family_1: str
    curve_family_2: str
    tenor: str
    spread_label: str = Field(..., description="Human-readable label, e.g. 'IT_BTP-DE_BUND 10Y'.")
    current_spread_bps: float = Field(..., description="Current yield differential in basis points.")
    daily_change_bps: Optional[float] = Field(None, description="1-day change in the spread (bps).")
    weekly_change_bps: Optional[float] = Field(None, description="5-trading-day change in the spread (bps).")
    monthly_change_bps: Optional[float] = Field(None, description="21-trading-day change in the spread (bps).")
    current_z_score: Optional[float] = Field(None, description="Rolling 252-day z-score of the spread.")
    rolling_window_days: int = Field(..., description="Window used for the z-score calculation.")
    high_252d_bps: Optional[float] = Field(None, description="Highest spread over trailing 252 trading days (bps).")
    low_252d_bps: Optional[float] = Field(None, description="Lowest spread over trailing 252 trading days (bps).")
    percentile_252d: Optional[float] = Field(None, description="Percentile rank within trailing 252-day range (0-100).")
    curve_family_1_yield: Optional[float] = Field(None, description="Latest yield on curve_family_1 (percent).")
    curve_family_2_yield: Optional[float] = Field(None, description="Latest yield on curve_family_2 (percent).")


class CrossMarketSpreadTimeSeriesRow(BaseModel):
    """Single row in the cross-market spread time-series."""
    date: str
    spread_bps: float
    z_score: Optional[float] = None


class CrossMarketSpreadOutput(BaseModel):
    """Top-level response for the cross-market spread tool."""
    current_metrics: CrossMarketSpreadCurrentMetrics
    time_series: List[CrossMarketSpreadTimeSeriesRow]

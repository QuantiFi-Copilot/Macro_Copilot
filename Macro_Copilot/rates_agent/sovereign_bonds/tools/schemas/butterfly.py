"""Pydantic schemas for the butterfly (curvature) tool."""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class ButterflyInput(BaseModel):
    """Parameters for computing the 3-point butterfly (curvature) on a
    single sovereign curve.
    butterfly = 2 × belly − short − long (in bps)."""
    curve_family: str = Field(..., description="Curve family identifier. Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT'.")
    short_tenor: str = Field(..., description="The short wing, e.g. '2Y'.")
    belly_tenor: str = Field(..., description="The belly (body) of the butterfly, e.g. '5Y'.")
    long_tenor: str = Field(..., description="The long wing, e.g. '10Y'.")
    lookback_days: int = Field(default=365, ge=30, le=7300, description="Calendar days of displayed history.")
    field_name: str = Field(default="YLD_YTM_MID", description="The observation field to use. Must match the exact value in the database.")

    @model_validator(mode="after")
    def _tenors_must_all_differ(self) -> "ButterflyInput":
        tenors = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(tenors)) != 3:
            raise ValueError(f"short_tenor, belly_tenor, and long_tenor must all be different, but got {tenors}.")
        return self


class ButterflyCurrentMetrics(BaseModel):
    """Snapshot metrics for a 3-point butterfly."""
    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    butterfly_label: str = Field(..., description="Human-readable label, e.g. '2s5s10s'.")
    current_butterfly_bps: float = Field(..., description="Current butterfly in bps. Positive = belly is cheap, negative = belly is rich.")
    daily_change_bps: Optional[float] = Field(None, description="1-day change in the butterfly (bps).")
    current_z_score: Optional[float] = Field(None, description="Rolling 252-day z-score of the butterfly.")
    rolling_window_days: int = Field(..., description="Window used for the z-score calculation.")
    high_252d_bps: Optional[float] = Field(None, description="Highest butterfly over trailing 252 trading days (bps).")
    low_252d_bps: Optional[float] = Field(None, description="Lowest butterfly over trailing 252 trading days (bps).")
    percentile_252d: Optional[float] = Field(None, description="Percentile rank within trailing 252-day range (0-100).")
    wing_short_bps: Optional[float] = Field(None, description="Component spread: belly − short (bps).")
    wing_long_bps: Optional[float] = Field(None, description="Component spread: long − belly (bps).")
    short_tenor_yield: Optional[float] = Field(None, description="Latest yield on the short wing (percent).")
    belly_tenor_yield: Optional[float] = Field(None, description="Latest yield on the belly (percent).")
    long_tenor_yield: Optional[float] = Field(None, description="Latest yield on the long wing (percent).")


class ButterflyTimeSeriesRow(BaseModel):
    """Single row in the butterfly time-series."""
    date: str
    butterfly_bps: float
    z_score: Optional[float] = None


class ButterflyOutput(BaseModel):
    """Top-level response for the butterfly tool."""
    current_metrics: ButterflyCurrentMetrics
    time_series: List[ButterflyTimeSeriesRow]

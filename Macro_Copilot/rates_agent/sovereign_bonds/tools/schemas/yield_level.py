"""Pydantic schemas for the yield level tool."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class YieldLevelInput(BaseModel):
    """Parameters for querying a single yield point on a curve."""
    curve_family: str = Field(..., description="Curve family identifier. Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT', 'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'.")
    tenor: str = Field(..., description="The tenor point to query, e.g. '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', '20Y', '30Y'.")
    lookback_days: int = Field(default=365, ge=30, le=7300, description="Calendar days of history for z-score, high/low, and percentile calculations.")
    field_name: str = Field(default="YLD_YTM_MID", description="The observation field to use. Must match the exact value in the database.")


class YieldLevelMetrics(BaseModel):
    """Deterministic snapshot for a single yield point."""
    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    tenor: str
    current_yield_pct: float = Field(..., description="Current yield in percent.")
    daily_change_bps: Optional[float] = Field(None, description="1-day change in basis points.")
    weekly_change_bps: Optional[float] = Field(None, description="5-trading-day change in basis points.")
    monthly_change_bps: Optional[float] = Field(None, description="21-trading-day change in basis points.")
    z_score: Optional[float] = Field(None, description="Rolling 252-trading-day z-score.")
    high_252d_pct: Optional[float] = Field(None, description="Highest yield over trailing 252 trading days.")
    low_252d_pct: Optional[float] = Field(None, description="Lowest yield over trailing 252 trading days.")
    percentile_252d: Optional[float] = Field(None, description="Percentile rank within trailing 252-day range (0-100).")
    observation_count: int = Field(..., description="Number of trading days in the calculation window.")


class YieldLevelOutput(BaseModel):
    """Top-level response for the yield level tool."""
    current_metrics: YieldLevelMetrics

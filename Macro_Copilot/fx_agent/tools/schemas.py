"""
Pydantic input/output schemas for the FX Agent deterministic tools.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FXSpotLevelInput(BaseModel):
    """Parameters for querying a single FX spot pair."""

    pair: str = Field(
        ...,
        description="FX pair identifier, e.g. 'EURUSD', 'GBPUSD', 'USDJPY'.",
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description="Calendar days of history used for calculations.",
    )
    field_name: str = Field(
        default="PX_LAST",
        description="Bloomberg field to query. Defaults to PX_LAST.",
    )


class FXSpotLevelMetrics(BaseModel):
    """Deterministic snapshot for a single FX spot pair."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    pair: str
    current_spot: float = Field(..., description="Latest spot level.")
    daily_change_pct: Optional[float] = Field(
        None, description="1-day percentage change."
    )
    weekly_change_pct: Optional[float] = Field(
        None, description="5-trading-day percentage change."
    )
    monthly_change_pct: Optional[float] = Field(
        None, description="21-trading-day percentage change."
    )
    z_score: Optional[float] = Field(
        None,
        description="Rolling 252-trading-day z-score of the spot level.",
    )
    high_252d: Optional[float] = Field(
        None, description="Highest spot level over trailing 252 trading days."
    )
    low_252d: Optional[float] = Field(
        None, description="Lowest spot level over trailing 252 trading days."
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank of current spot within trailing 252-day range (0-100).",
    )
    observation_count: int = Field(
        ..., description="Number of observations in the display window."
    )


class FXSpotLevelOutput(BaseModel):
    """Top-level response for the FX spot level tool."""

    current_metrics: FXSpotLevelMetrics

class FXScannerInput(BaseModel):
    """Parameters for scanning FX spot pairs."""

    market_scope: Optional[str] = Field(
        default=None,
        description="Optional market filter, e.g. 'G10'.",
    )
    top_n: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of pairs to return.",
    )
    field_name: str = Field(
        default="PX_LAST",
        description="Bloomberg field to query.",
    )


class FXScannerRow(BaseModel):
    """One row in the FX scanner output."""

    pair: str
    ticker: str
    as_of_date: str
    current_spot: float
    daily_change_pct: Optional[float] = None
    weekly_change_pct: Optional[float] = None
    monthly_change_pct: Optional[float] = None
    momentum_1m_pct: Optional[float] = None
    momentum_3m_pct: Optional[float] = None
    z_score: Optional[float] = None
    signal: str = "Neutral"


class FXScannerOutput(BaseModel):
    """Top-level response for the FX scanner tool."""

    rows: list[FXScannerRow]
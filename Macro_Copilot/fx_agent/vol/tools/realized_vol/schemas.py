from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FXRealizedVolInput(BaseModel):
    """Parameters for FX realized-volatility analytics."""

    pair: str = Field(
        ...,
        description="FX pair identifier, e.g. 'EURUSD', 'GBPUSD', 'USDJPY'.",
    )
    window_observations: int = Field(
        default=21,
        ge=5,
        le=252,
        description="Rolling window length in observations.",
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description="Calendar days of history fetched for the output.",
    )
    return_type: Literal["log_return", "simple_return"] = Field(
        default="log_return",
        description="Return convention used before annualising volatility.",
    )
    field_name: str = Field(
        default="PX_LAST",
        description="Bloomberg field to query. Defaults to PX_LAST.",
    )


class FXRealizedVolMetrics(BaseModel):
    as_of_date: str
    pair: str
    spot: float
    window_observations: int
    realized_vol_annualized_pct: float | None
    realized_vol_z_score: float | None
    daily_return_pct: float | None
    observation_count: int


class FXRealizedVolTimeSeriesRow(BaseModel):
    date: str
    realized_vol_annualized_pct: float | None


class FXRealizedVolOutput(BaseModel):
    current_metrics: FXRealizedVolMetrics
    time_series: list[FXRealizedVolTimeSeriesRow]

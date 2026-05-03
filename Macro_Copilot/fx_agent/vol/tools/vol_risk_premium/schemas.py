from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FXVolRiskPremiumInput(BaseModel):
    """Parameters for FX implied-vs-realized volatility premium."""

    pair: str = Field(
        ...,
        description="FX pair identifier, e.g. 'EURUSD', 'GBPUSD', 'USDJPY'.",
    )
    tenor: str = Field(
        default="1M",
        description="Implied-vol tenor. Current ingested universe supports 1M.",
    )
    realized_window_observations: int = Field(
        default=21,
        ge=5,
        le=252,
        description="Rolling window for realized volatility.",
    )
    lookback_days: int = Field(
        default=365,
        ge=90,
        le=7300,
        description="Calendar days of history fetched for the output.",
    )
    field_name: str = Field(
        default="PX_LAST",
        description="Bloomberg field to query. Defaults to PX_LAST.",
    )


class FXVolRiskPremiumMetrics(BaseModel):
    as_of_date: str
    pair: str
    tenor: str
    implied_vol_pct: float
    realized_vol_annualized_pct: float | None
    vol_risk_premium_pct: float | None
    implied_vol_z_score: float | None
    premium_z_score: float | None
    signal: Literal["vol rich", "vol cheap", "fair"]
    suggested_expression: Literal["prefer selling vol", "prefer owning vol", "neutral"]
    observation_count: int


class FXVolRiskPremiumTimeSeriesRow(BaseModel):
    date: str
    implied_vol_pct: float | None
    realized_vol_annualized_pct: float | None
    vol_risk_premium_pct: float | None


class FXVolRiskPremiumOutput(BaseModel):
    current_metrics: FXVolRiskPremiumMetrics
    time_series: list[FXVolRiskPremiumTimeSeriesRow]

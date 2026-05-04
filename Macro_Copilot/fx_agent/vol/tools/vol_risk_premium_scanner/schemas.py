from __future__ import annotations

from pydantic import BaseModel, Field


class FXVolRiskPremiumScannerInput(BaseModel):
    tenor: str = Field(default="1M")
    realized_window_observations: int = Field(default=21, ge=5, le=252)
    lookback_days: int = Field(default=365, ge=90, le=7300)
    top_n: int = Field(default=10, ge=1, le=50)


class FXVolRiskPremiumScannerRow(BaseModel):
    pair: str
    as_of_date: str
    implied_vol_pct: float
    realized_vol_annualized_pct: float | None
    vol_risk_premium_pct: float | None
    premium_z_score: float | None
    signal: str
    suggested_expression: str


class FXVolRiskPremiumScannerOutput(BaseModel):
    tenor: str
    rows: list[FXVolRiskPremiumScannerRow]

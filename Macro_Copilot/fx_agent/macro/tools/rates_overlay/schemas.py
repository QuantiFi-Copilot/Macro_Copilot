from __future__ import annotations

from pydantic import BaseModel, Field


class FXRatesDifferentialInput(BaseModel):
    pair: str = Field(default="EURUSD")
    fx_tenor: str = Field(default="1M")
    rate_curve_family_1: str = Field(default="UST")
    rate_curve_family_2: str = Field(default="DE_BUND")
    rate_tenor: str = Field(default="2Y")
    lookback_days: int = Field(default=365, ge=30, le=7300)
    fx_vol_window_observations: int = Field(default=21, ge=5, le=252)
    rates_field_name: str = Field(default="YLD_YTM_MID")


class FXRatesDifferentialSnapshot(BaseModel):
    label: str
    as_of_date: str
    curve_family_1_rate_pct: float
    curve_family_2_rate_pct: float
    differential_bps: float
    daily_change_bps: float | None = None
    monthly_change_bps: float | None = None
    observation_count: int


class FXRatesDifferentialOutput(BaseModel):
    pair: str
    fx_summary: str
    fx_direction: str
    fx_score: float
    rates_status: str
    rates_snapshot: FXRatesDifferentialSnapshot | None = None
    consistency_label: str
    conclusion: str
    missing_data: list[str] = Field(default_factory=list)
    drivers: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


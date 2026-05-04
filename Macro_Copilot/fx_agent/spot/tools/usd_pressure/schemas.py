from __future__ import annotations

from pydantic import BaseModel, Field


class FXUSDPressureInput(BaseModel):
    lookback_days: int = Field(default=365, ge=90, le=7300)
    field_name: str = Field(default="PX_LAST")


class FXUSDPressureRow(BaseModel):
    pair: str
    as_of_date: str
    spot: float
    monthly_change_pct: float | None
    usd_pressure_pct: float | None
    z_score: float | None
    signal: str


class FXUSDPressureOutput(BaseModel):
    as_of_date: str
    pressure_regime: str
    usd_pressure_score: float
    dxy_monthly_change_pct: float | None
    rows: list[FXUSDPressureRow]

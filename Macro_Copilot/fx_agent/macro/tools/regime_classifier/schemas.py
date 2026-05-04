from __future__ import annotations

from pydantic import BaseModel, Field


class FXRegimeClassifierInput(BaseModel):
    anchor_pair: str = Field(
        default="EURUSD",
        description="Reference FX pair used for macro overlay context.",
    )
    tenor: str = Field(
        default="1M",
        description="Carry and implied-vol tenor used for regime checks.",
    )
    lookback_days: int = Field(default=365, ge=90, le=7300)
    realized_window_observations: int = Field(default=21, ge=5, le=252)
    correlation_window_observations: int = Field(default=63, ge=20, le=252)
    field_name: str = Field(default="PX_LAST")


class FXRegimeComponent(BaseModel):
    name: str
    label: str
    score: float
    summary: str


class FXRegimeClassifierOutput(BaseModel):
    as_of_date: str | None = None
    anchor_pair: str
    overall_regime: str
    confidence: str
    total_score: float
    usd_regime: str
    risk_regime: str
    vol_regime: str
    carry_regime: str
    components: list[FXRegimeComponent]
    drivers: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


from __future__ import annotations

from pydantic import BaseModel, Field


class FXCorrelationBetaInput(BaseModel):
    pair: str = Field(
        ...,
        description="FX pair identifier, e.g. EURUSD, GBPUSD, USDJPY.",
    )
    lookback_days: int = Field(default=365, ge=90, le=7300)
    window_observations: int = Field(default=63, ge=20, le=252)
    field_name: str = Field(default="PX_LAST")


class FXCorrelationBetaRow(BaseModel):
    ticker: str
    label: str
    proxy_family: str
    observations: int
    correlation: float | None
    beta: float | None
    r_squared: float | None
    proxy_1m_change_pct: float | None
    sensitivity_label: str
    interpretation: str


class FXCorrelationBetaOutput(BaseModel):
    pair: str
    as_of_date: str
    window_observations: int
    dominant_driver: str | None = None
    dominant_correlation: float | None = None
    rows: list[FXCorrelationBetaRow]
    summary: str
    risks: list[str] = Field(default_factory=list)
    follow_ups: list[str] = Field(default_factory=list)


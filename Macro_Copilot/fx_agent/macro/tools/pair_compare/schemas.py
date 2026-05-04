from __future__ import annotations

from pydantic import BaseModel, Field


class FXPairCompareInput(BaseModel):
    pair_1: str = Field(..., description="First FX pair, e.g. EURUSD.")
    pair_2: str = Field(..., description="Second FX pair, e.g. GBPUSD.")
    tenor: str = Field(default="1M")
    vol_window_observations: int = Field(default=21, ge=5, le=252)
    lookback_days: int = Field(default=365, ge=90, le=7300)


class FXPairCompareRow(BaseModel):
    pair: str
    direction: str
    confidence: str
    trade_setup_score: float
    spot: float
    carry_annualized_pct: float | None
    realized_vol_annualized_pct: float | None
    vol_risk_premium_pct: float | None
    macro_regime: str
    macro_regime_score: float


class FXPairCompareOutput(BaseModel):
    pair_1: str
    pair_2: str
    preferred_pair: str | None
    preference_rationale: str
    rows: list[FXPairCompareRow]

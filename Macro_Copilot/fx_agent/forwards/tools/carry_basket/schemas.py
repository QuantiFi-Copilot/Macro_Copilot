from __future__ import annotations

from pydantic import BaseModel, Field


class FXCarryBasketInput(BaseModel):
    tenor: str = Field(default="1M")
    basket_size: int = Field(default=2, ge=1, le=5)
    max_realized_vol_pct: float = Field(default=12.0, ge=1.0, le=50.0)
    max_abs_spot_z_score: float = Field(default=2.0, ge=0.5, le=5.0)
    lookback_days: int = Field(default=365, ge=90, le=7300)


class FXCarryBasketLeg(BaseModel):
    pair: str
    side: str
    carry_annualized_pct: float
    realized_vol_annualized_pct: float | None
    spot_z_score: float | None
    rationale: str


class FXCarryBasketOutput(BaseModel):
    tenor: str
    basket_label: str
    long_legs: list[FXCarryBasketLeg]
    short_legs: list[FXCarryBasketLeg]
    excluded_pairs: list[str]

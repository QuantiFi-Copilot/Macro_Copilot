from __future__ import annotations

from pydantic import BaseModel, Field


class FXCarryDecayInput(BaseModel):
    pair: str = Field(
        ...,
        description="FX pair identifier, e.g. EURUSD, GBPUSD, USDJPY.",
    )


class FXCarryDecayTenorRow(BaseModel):
    tenor: str
    tenor_days: int
    carry_annualized_pct: float
    carry_bps_spot: float
    outright_forward: float


class FXCarryDecayOutput(BaseModel):
    pair: str
    spot: float | None = None
    as_of_date: str | None = None
    decay_label: str
    best_tenor: str | None = None
    best_carry_annualized_pct: float | None = None
    front_carry_annualized_pct: float | None = None
    back_carry_annualized_pct: float | None = None
    slope_front_to_back_pct: float | None = None
    curve_span: str | None = None
    rows: list[FXCarryDecayTenorRow]
    summary: str
    risks: list[str] = Field(default_factory=list)


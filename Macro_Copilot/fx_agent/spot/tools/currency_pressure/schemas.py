from __future__ import annotations

from pydantic import BaseModel, Field


class FXCurrencyPressureInput(BaseModel):
    currencies: list[str] | None = Field(
        default=None,
        description="Optional currencies to rank, e.g. ['USD', 'EUR']. Defaults to G10.",
    )
    lookback_days: int = Field(default=365, ge=90, le=7300)
    field_name: str = Field(default="PX_LAST")


class FXCurrencyPairContribution(BaseModel):
    pair: str
    base_currency: str
    quote_currency: str
    monthly_change_pct: float | None
    contribution_pct: float | None
    z_score: float | None
    as_of_date: str


class FXCurrencyPressureRow(BaseModel):
    currency: str
    pressure_score_pct: float
    signal: str
    pair_count: int
    confirming_pairs: list[str] = Field(default_factory=list)
    challenging_pairs: list[str] = Field(default_factory=list)
    contributions: list[FXCurrencyPairContribution] = Field(default_factory=list)


class FXCurrencyPressureOutput(BaseModel):
    as_of_date: str | None = None
    strongest_currency: str | None = None
    weakest_currency: str | None = None
    rows: list[FXCurrencyPressureRow]


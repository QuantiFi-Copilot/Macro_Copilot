from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FXCurrencyThesisInput(BaseModel):
    currency: str = Field(default="USD")
    view: Literal["long", "short"] = Field(default="long")
    lookback_days: int = Field(default=365, ge=90, le=7300)
    top_n: int = Field(default=5, ge=1, le=12)
    field_name: str = Field(default="PX_LAST")


class FXCurrencyThesisMetric(BaseModel):
    name: str
    value: str
    status: Literal["confirming", "challenging", "neutral"]
    detail: str


class FXCurrencyThesisExpression(BaseModel):
    pair: str
    expression: str
    rationale: str
    currency_contribution_pct: float | None = None
    z_score: float | None = None
    monthly_change_pct: float | None = None


class FXCurrencyThesisOutput(BaseModel):
    currency: str
    view: Literal["long", "short"]
    as_of_date: str | None = None
    thesis_status: Literal["supportive", "mixed", "hostile"]
    confidence: Literal["high", "medium", "low"]
    confirmation_score: float
    summary: str
    currency_pressure_score_pct: float
    currency_rank: int | None = None
    strongest_currency: str | None = None
    weakest_currency: str | None = None
    confirmations: list[str] = Field(default_factory=list)
    challenges: list[str] = Field(default_factory=list)
    best_expressions: list[FXCurrencyThesisExpression] = Field(default_factory=list)
    stretched_counter_moves: list[FXCurrencyThesisExpression] = Field(default_factory=list)
    metrics_to_watch: list[FXCurrencyThesisMetric] = Field(default_factory=list)
    invalidation_signals: list[str] = Field(default_factory=list)


from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FXUSDThesisInput(BaseModel):
    usd_view: Literal["long_usd", "short_usd"] = Field(default="long_usd")
    anchor_pair: str = Field(default="EURUSD")
    lookback_days: int = Field(default=365, ge=90, le=7300)
    top_n: int = Field(default=5, ge=1, le=12)
    field_name: str = Field(default="PX_LAST")


class FXUSDThesisMetric(BaseModel):
    name: str
    value: str
    status: Literal["confirming", "challenging", "neutral"]
    detail: str


class FXUSDThesisExpression(BaseModel):
    pair: str
    expression: str
    rationale: str
    usd_pressure_pct: float | None = None
    z_score: float | None = None
    monthly_change_pct: float | None = None


class FXUSDThesisOutput(BaseModel):
    usd_view: Literal["long_usd", "short_usd"]
    as_of_date: str | None = None
    thesis_status: Literal["supportive", "mixed", "hostile"]
    confidence: Literal["high", "medium", "low"]
    confirmation_score: float
    summary: str
    confirmations: list[str] = Field(default_factory=list)
    challenges: list[str] = Field(default_factory=list)
    best_expressions: list[FXUSDThesisExpression] = Field(default_factory=list)
    stretched_counter_moves: list[FXUSDThesisExpression] = Field(default_factory=list)
    metrics_to_watch: list[FXUSDThesisMetric] = Field(default_factory=list)
    invalidation_signals: list[str] = Field(default_factory=list)


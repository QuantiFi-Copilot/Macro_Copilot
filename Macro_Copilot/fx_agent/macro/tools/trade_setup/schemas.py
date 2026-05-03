from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FXTradeSetupInput(BaseModel):
    """Parameters for a single-pair FX trade setup."""

    pair: str = Field(
        ...,
        description="FX pair identifier, e.g. 'EURUSD', 'GBPUSD', 'USDJPY'.",
    )
    tenor: str = Field(
        default="1M",
        description="Forward tenor used for carry, e.g. 1W, 1M, 3M, 6M.",
    )
    vol_window_observations: int = Field(
        default=21,
        ge=5,
        le=252,
        description="Rolling observation window for realized volatility.",
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description="Calendar days of spot history used for spot and vol context.",
    )


class FXTradeSetupSignal(BaseModel):
    name: str
    score: float
    stance: Literal["bullish", "bearish", "neutral"]
    description: str


class FXTradeSetupOutput(BaseModel):
    pair: str
    as_of_date: str
    tenor: str
    direction: Literal["bullish", "bearish", "neutral"]
    confidence: Literal["high", "medium", "low"]
    total_score: float
    summary: str
    key_drivers: list[str]
    risks: list[str]
    follow_up_questions: list[str]
    signals: list[FXTradeSetupSignal]
    spot_snapshot: dict
    carry_snapshot: dict | None
    forward_curve: list[dict]
    realized_vol_snapshot: dict

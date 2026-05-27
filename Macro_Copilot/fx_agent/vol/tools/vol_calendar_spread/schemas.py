"""Pydantic schemas for the FX vol calendar-spread snapshot tool.

The calendar spread is the ATM implied-vol differential between a
long tenor and a short tenor — the canonical vol term-structure
carry. Positive spread (long - short > 0) = contango (upward-sloping
term structure); negative = backwardation.

Single-(pair, short_tenor, long_tenor) snapshot primitive with the
same shape as risk_reversal / vol_risk_premium. Central knob (PR8)
= spread_direction (sign convention).

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR8, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from fx_agent.vol._shared import FXVolStandardTenor, tenor_trading_days


FXSpreadDirection = Literal["long_minus_short", "short_minus_long"]


class FXVolCalendarSpreadInput(BaseModel):
    """Per-query parameters for the FX vol calendar-spread snapshot.

    Per PR8: ``spread_direction`` is the SINGLE central methodology
    knob. pair / short_tenor / long_tenor are instrument selectors;
    lookback_days controls fetch window only; field_name is the
    standard PX_LAST/PX_BID/PX_ASK sentinel.

    short_tenor must be strictly < long_tenor (in trading days) —
    enforced by a model validator so callers fail-loud at the
    schema boundary if they invert the legs.
    """

    pair: str = Field(
        ...,
        description="FX pair, e.g. 'EURUSD', 'USDMXN'.",
    )
    short_tenor: FXVolStandardTenor = Field(
        default="1M",
        description="Shorter ATM vol tenor of the spread.",
    )
    long_tenor: FXVolStandardTenor = Field(
        default="3M",
        description=(
            "Longer ATM vol tenor of the spread. Must be strictly later "
            "than short_tenor (in trading-day terms)."
        ),
    )
    spread_direction: FXSpreadDirection = Field(
        default="long_minus_short",
        description=(
            "Sign convention for the spread. 'long_minus_short' "
            "(DEFAULT) = positive when contango (upward-sloping term "
            "structure, vol typically richer at the back end). "
            "'short_minus_long' = positive when backwardation (event-"
            "risk pricing pulls the front end above the back). The "
            "SINGLE central methodology knob (PR8)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched. Does NOT control the "
            "rolling 252-day window on the SPREAD series (YAML-locked)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override for BOTH legs. None falls through "
            "to PX_LAST."
        ),
    )

    @model_validator(mode="after")
    def _short_must_be_shorter(self) -> "FXVolCalendarSpreadInput":
        if tenor_trading_days(self.short_tenor) >= tenor_trading_days(self.long_tenor):
            raise ValueError(
                f"short_tenor {self.short_tenor!r} is not strictly "
                f"shorter than long_tenor {self.long_tenor!r}. Use "
                "two distinct standard tenors ordered short → long."
            )
        return self


class FXVolCalendarSpreadMetrics(BaseModel):
    as_of_date: str
    pair: str
    short_tenor: str
    long_tenor: str
    spread_direction: str
    vendor_ticker_short: str
    vendor_ticker_long: str
    current_short_vol_pct: float = Field(
        ...,
        description="Latest ATM implied vol at short_tenor in PERCENT.",
    )
    current_long_vol_pct: float = Field(
        ...,
        description="Latest ATM implied vol at long_tenor in PERCENT.",
    )
    current_spread_vol_pts: float = Field(
        ...,
        description=(
            "Latest calendar spread in ABSOLUTE vol points, signed per "
            "spread_direction. With long_minus_short: positive = "
            "contango; negative = backwardation."
        ),
    )
    daily_change_vol_pts: Optional[float] = None
    weekly_change_vol_pts: Optional[float] = None
    monthly_change_vol_pts: Optional[float] = None
    z_score: Optional[float] = None
    high_252d: Optional[float] = None
    low_252d: Optional[float] = None
    percentile_252d: Optional[float] = None
    observation_count: int


class FXVolCalendarSpreadOutput(BaseModel):
    current_metrics: FXVolCalendarSpreadMetrics

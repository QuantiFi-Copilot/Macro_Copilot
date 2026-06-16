"""Pydantic I/O schemas for swap_carry_and_roll (§7-C, Bucket-1A)."""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class SwapCarryAndRollInput(BaseModel):
    """Per-query inputs for the OIS swap carry/roll decomposition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description="OIS curve family, e.g. 'USD_SOFR_OIS', 'EUR_ESTR_OIS'.",
    )
    tenor: str = Field(
        default="10Y",
        description="The swap tenor T whose carry/roll to decompose.",
    )
    horizon: Optional[str] = Field(
        default=None,
        description=(
            "Holding horizon h (e.g. '3M', '6M').  None (default) → config "
            "default_horizon.  Must be shorter than the tenor."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description="Calendar days of displayed history.",
    )
    field_name: Optional[str] = Field(
        default=None,
        description="OIS rate field; None → config default_swap_rate_field.",
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )


class SwapCarryAndRollCurrentMetrics(BaseModel):
    """Snapshot of the current carry/roll decomposition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str
    curve_family: str
    tenor: str
    horizon: str
    spot_rate_pct: Optional[float]          # s(T)
    rolled_spot_rate_pct: Optional[float]   # s(T-h)
    financing_rate_pct: Optional[float]     # s(h) — the horizon OIS rate
    roll_bps: Optional[float]               # s(T) - s(T-h)  (curve slide)
    carry_bps: Optional[float]              # s(T) - s(h)    (coupon - financing)
    total_carry_roll_bps: Optional[float]   # roll + carry


class SwapCarryAndRollTimeSeriesRow(BaseModel):
    """One row of the carry/roll history (bps)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    roll_bps: Optional[float] = None
    carry_bps: Optional[float] = None
    total_bps: Optional[float] = None


class SwapCarryAndRollOutput(BaseModel):
    """Top-level response for the carry/roll decomposition."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: SwapCarryAndRollCurrentMetrics
    time_series: List[SwapCarryAndRollTimeSeriesRow]
    # Composable headline: the total carry+roll history (bps).
    time_series_total_carry_roll: TimeSeries
    methodology_disclosures: List[str]


__all__ = [
    "SwapCarryAndRollInput",
    "SwapCarryAndRollCurrentMetrics",
    "SwapCarryAndRollTimeSeriesRow",
    "SwapCarryAndRollOutput",
]

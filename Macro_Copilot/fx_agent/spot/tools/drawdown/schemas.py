"""Pydantic schemas for the calculate_fx_drawdown tool.

Phase B follow-up (2026-05-25).

Single-pair derived primitive: produces the running drawdown time
series of an FX spot rate against its trailing peak, plus a snapshot
summary (current drawdown, max drawdown over the window, peak / trough
dates, recovery date / time).

Drawdown definition (V1, locked):
  DD_t = (P_t / running_max(P_0..P_t)) - 1
  Always ≤ 0. -0.10 means the rate is 10% below its trailing peak.

Output unit: RATIO (drawdown is a unitless fraction).

Directionality note: drawdown is computed on the raw spot series
(PX_LAST) with no sign flipping. Whether "spot going down" is good or
bad for a particular position is the consumer's call — this tool
reports the raw price-relative-to-peak; the LLM / downstream workflow
interprets sign based on the trader's position direction.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class FXDrawdownInput(BaseModel):
    """Parameters the LLM extracts to compute drawdown for one FX pair."""

    model_config = ConfigDict(extra="forbid")

    pair: str = Field(
        ...,
        min_length=6,
        max_length=6,
        description=(
            "FX pair as stored in instrument_master.attributes.pair "
            "(e.g. 'EURUSD', 'USDMXN'). Six characters, base + quote."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB. Controls the "
            "display window AND the lookback over which the running max "
            "is computed. Peak before the window's start is NOT carried "
            "in (the running max anchors on the first row of the "
            "display slice — keep this in mind when comparing windows)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field on market_data_daily. None ⇒ resolved from "
            "config.yaml's default_field_name (currently 'PX_LAST')."
        ),
    )


class FXDrawdownMetrics(BaseModel):
    """Snapshot summary of drawdown over the lookback window."""

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date in the window (YYYY-MM-DD).")
    pair: str
    current_drawdown: float = Field(
        ...,
        description=(
            "Drawdown at the most recent trade date, expressed as a "
            "RATIO ≤ 0. -0.05 means the spot is 5% below the running "
            "peak from the window's start to date."
        ),
    )
    max_drawdown: float = Field(
        ...,
        description=(
            "Most negative drawdown observed in the window. Equals "
            "min(time_series.rows[*].value)."
        ),
    )
    max_drawdown_date: str = Field(
        ...,
        description="Date of the most negative drawdown (trough date) (YYYY-MM-DD).",
    )
    peak_date: str = Field(
        ...,
        description=(
            "Date of the running peak that the max_drawdown is measured "
            "against — i.e. the date when the running max was first "
            "established before the trough at max_drawdown_date "
            "(YYYY-MM-DD)."
        ),
    )
    peak_value: float = Field(
        ...,
        description="Spot value at peak_date.",
    )
    trough_value: float = Field(
        ...,
        description="Spot value at max_drawdown_date.",
    )
    recovery_date: Optional[str] = Field(
        None,
        description=(
            "Date when the spot first matched or exceeded peak_value "
            "AFTER max_drawdown_date. None if the spot has not yet "
            "recovered within the window."
        ),
    )
    time_to_recovery_days: Optional[int] = Field(
        None,
        description=(
            "Number of trading days between max_drawdown_date and "
            "recovery_date (inclusive of recovery_date, exclusive of "
            "max_drawdown_date). None if not yet recovered."
        ),
    )
    observation_count: int = Field(
        ...,
        description="Number of trading days in the display window.",
    )


class FXDrawdownOutput(BaseModel):
    """Top-level response for calculate_fx_drawdown."""

    model_config = ConfigDict(extra="forbid")

    current_metrics: FXDrawdownMetrics
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Running drawdown time series over the display window. "
            "Closed-enum TimeSeriesUnits.RATIO units; series_name = "
            "'<pair_lower>_drawdown'. Each row's value is the "
            "drawdown at that date — always ≤ 0. Rounded with the "
            "same drawdown_round_decimals as the snapshot so "
            "current_metrics.current_drawdown equals "
            "time_series.rows[-1].value STRICTLY."
        ),
    )


__all__ = [
    "FXDrawdownInput",
    "FXDrawdownMetrics",
    "FXDrawdownOutput",
]

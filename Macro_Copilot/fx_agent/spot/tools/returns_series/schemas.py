"""Pydantic schemas for the get_fx_returns_series tool.

Phase B follow-up (2026-05-25).

Single-pair derived primitive: produces a clean log-returns time
series at a configurable horizon (daily / weekly / monthly) plus a
summary snapshot (latest return, mean / std / min / max over the
lookback window).

Output shape mirrors ``rates_agent/sovereign_bonds/tools/yield_levels``
exactly (compliance check 2026-05-25): a ``current_metrics`` snapshot
field + a ``time_series: TimeSeries`` canonical-shape field with
closed-enum ``TimeSeriesUnits.RATIO`` (log-returns are unitless ratios
of price-relatives).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class FXReturnsSeriesInput(BaseModel):
    """Parameters the LLM extracts to query log-returns for one FX pair."""

    model_config = ConfigDict(extra="forbid")

    pair: str = Field(
        ...,
        min_length=6,
        max_length=6,
        description=(
            "FX pair as stored in instrument_master.attributes.pair "
            "(e.g. 'EURUSD', 'USDMXN'). Six characters, base + quote "
            "currency. Not the vendor ticker."
        ),
    )
    horizon: Literal["daily", "weekly", "monthly"] = Field(
        default="daily",
        description=(
            "Return horizon. 'daily' = log(P_t) - log(P_t-1); 'weekly' "
            "= log(P_t) - log(P_t-5); 'monthly' = log(P_t) - log(P_t-22). "
            "Periods are trading days, not calendar days, per "
            "config.yaml's horizon_to_periods mapping."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB. Controls the "
            "display window for the time series AND the lookback for the "
            "snapshot summary stats (mean / std / min / max). Does not "
            "affect the horizon (set by 'horizon' param above)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field on market_data_daily. None ⇒ resolved from "
            "config.yaml's default_field_name (currently 'PX_LAST')."
        ),
    )


class FXReturnsSeriesMetrics(BaseModel):
    """Snapshot summary of the returns series over the lookback window."""

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date with a valid return (YYYY-MM-DD).")
    pair: str
    horizon: str = Field(..., description="Echo of input horizon ('daily' / 'weekly' / 'monthly').")
    current_return: Optional[float] = Field(
        None,
        description=(
            "Log-return at the most recent trade date — log(P_T) - "
            "log(P_T-h) where h is the horizon periods. None if the "
            "lookback didn't have enough rows to compute it."
        ),
    )
    mean_return: Optional[float] = Field(
        None,
        description="Arithmetic mean of log-returns over the lookback window.",
    )
    std_return: Optional[float] = Field(
        None,
        description="Sample standard deviation of log-returns over the lookback (ddof=1).",
    )
    min_return: Optional[float] = Field(
        None,
        description="Lowest log-return observed in the lookback window.",
    )
    max_return: Optional[float] = Field(
        None,
        description="Highest log-return observed in the lookback window.",
    )
    observation_count: int = Field(
        ...,
        description=(
            "Number of valid (non-NaN) return observations in the "
            "lookback window. Equals len(time_series.rows) minus any "
            "warmup rows that are still NaN (None) at the start."
        ),
    )


class FXReturnsSeriesOutput(BaseModel):
    """Top-level response for get_fx_returns_series."""

    model_config = ConfigDict(extra="forbid")

    current_metrics: FXReturnsSeriesMetrics
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Historical log-returns at the requested horizon over the "
            "display window. Closed-enum TimeSeriesUnits.RATIO units; "
            "series_name = '<pair_lower>_log_return_<horizon>'. Rows in "
            "the warmup (first horizon_periods rows where the lag is "
            "not yet defined) carry value=None. Each non-None row is "
            "rounded with the same return_round_decimals convention the "
            "snapshot uses, so current_metrics.current_return equals "
            "time_series.rows[-1].value STRICTLY."
        ),
    )


__all__ = [
    "FXReturnsSeriesInput",
    "FXReturnsSeriesMetrics",
    "FXReturnsSeriesOutput",
]

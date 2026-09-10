"""Pydantic schemas for the get_fx_realized_vol tool.

Phase B follow-up (2026-05-25).

Single-pair derived primitive: produces the rolling realized
volatility of an FX spot rate, annualized via sqrt(252).

Output unit: PERCENT (vol conventionally quoted in % — e.g.,
"EURUSD has 8% annualized vol"). The value 8.0 means 8% / year, not
0.08.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class FXRealizedVolInput(BaseModel):
    """Parameters the LLM extracts to query realized vol for one FX pair."""

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
    window_days: int = Field(
        default=30,
        ge=5,
        le=504,
        description=(
            "Rolling window over which the daily-log-return standard "
            "deviation is computed, in TRADING days. Default 30 (≈ 1.5 "
            "calendar months) balances responsiveness vs stability. "
            "Common alternatives: 60 (quarter), 252 (year). The first "
            "(window_days - 1) rows of the output are NaN by "
            "construction (warmup)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=60,
        le=7300,
        description=(
            "Calendar days of history fetched + displayed. Controls "
            "the time-series length AND the snapshot summary lookback "
            "(mean / min / max of the rolling vol). Must be larger "
            "than window_days for meaningful output."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field on market_data_daily. None ⇒ resolved "
            "from config.yaml's default_field_name (currently 'PX_LAST')."
        ),
    )


class FXRealizedVolMetrics(BaseModel):
    """Snapshot summary of realized vol over the lookback window."""

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date with a valid vol (YYYY-MM-DD).")
    pair: str
    window_days: int = Field(..., description="Echo of input window_days.")
    current_realized_vol_pct: Optional[float] = Field(
        None,
        description=(
            "Most recent annualized realized vol, in PERCENT (8.0 = "
            "8% / year). None if no valid (non-NaN) vol in the window."
        ),
    )
    mean_realized_vol_pct: Optional[float] = Field(
        None,
        description="Mean of realized vol values over the lookback window (PERCENT).",
    )
    min_realized_vol_pct: Optional[float] = Field(
        None,
        description="Lowest realized vol observed in the lookback (PERCENT).",
    )
    max_realized_vol_pct: Optional[float] = Field(
        None,
        description="Highest realized vol observed in the lookback (PERCENT).",
    )
    observation_count: int = Field(
        ...,
        description="Number of valid (non-NaN) vol observations in the lookback window.",
    )


class FXRealizedVolOutput(BaseModel):
    """Top-level response for get_fx_realized_vol."""

    model_config = ConfigDict(extra="forbid")

    current_metrics: FXRealizedVolMetrics
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Rolling realized vol time series over the display window. "
            "Closed-enum TimeSeriesUnits.PERCENT units; series_name = "
            "'<pair_lower>_realized_vol_<window_days>d'. First "
            "(window_days - 1) rows carry value=None (warmup). "
            "Annualization factor sqrt(252) is hardcoded in config "
            "(annualization_trading_days_per_year). Rows rounded to "
            "vol_round_decimals so the snapshot's "
            "current_realized_vol_pct equals time_series.rows[-1].value "
            "STRICTLY."
        ),
    )


__all__ = [
    "FXRealizedVolInput",
    "FXRealizedVolMetrics",
    "FXRealizedVolOutput",
]

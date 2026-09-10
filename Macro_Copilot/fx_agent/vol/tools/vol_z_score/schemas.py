"""Pydantic schemas for the FX vol rolling z-score time series.

Returns the rolling 252-day z-score TimeSeries of a single (pair,
tenor) ATM vol — useful for visualizing vol regime trajectory rather
than just the current snapshot. Analog of get_fx_returns_series in
the spot domain (single-pair derived TimeSeries).

For a current-snapshot single value, use get_fx_atm_vol_level instead
(this tool's snapshot field is just the last point of the series).

V1.1 (2026-05-27 compliance follow-up): output now emits the canonical
``shared.schemas.time_series.TimeSeries`` shape (PR13 / WT-binding /
event-study composability). The legacy ``rows`` field is preserved
alongside ``time_series`` for backward compatibility during the
transition.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from fx_agent.vol._shared import FXVolStandardTenor
from shared.schemas import TimeSeries


class FXVolZScoreInput(BaseModel):
    pair: str = Field(
        ...,
        description="FX pair, e.g. 'EURUSD', 'USDMXN'.",
    )
    tenor: FXVolStandardTenor = Field(
        default="1M",
        description="ATM vol tenor. One of '1W', '1M', '3M', '6M', '12M'.",
    )
    lookback_days: int = Field(
        default=730, ge=60, le=7300,
        description=(
            "Calendar days of vol history fetched. Default 730 (2 years) "
            "is wider than other tools' 365 so the rolling 252-day "
            "z-score series has at least 1 year of valid output. The "
            "first 252 days of the fetched history feed the rolling "
            "window without emitting; only days >= 252-from-start get "
            "an emitted z-score."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description="Bloomberg field override. None falls through to PX_LAST.",
    )


class FXVolZScoreRow(BaseModel):
    """Legacy row shape kept for backward compatibility.

    Consumers building on the canonical TimeSeries shape should use
    ``output.time_series.rows`` instead (which has the standard
    ``date`` / ``value`` field names).
    """

    trade_date: str
    z_score: float


class FXVolZScoreSnapshot(BaseModel):
    as_of_date: str
    pair: str
    tenor: str
    vendor_ticker: str
    current_atm_vol_pct: float
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Z-score on the as_of_date. None when fewer than "
            "z_score_min_periods observations are available in the "
            "trailing window — e.g. early in the lookback when the "
            "rolling window isn't full yet."
        ),
    )
    series_min_z: Optional[float] = Field(
        None, description="Minimum z-score over the emitted series."
    )
    series_max_z: Optional[float] = Field(
        None, description="Maximum z-score over the emitted series."
    )
    series_mean_z: Optional[float] = Field(
        None, description="Mean z-score over the emitted series."
    )
    observation_count_full_series: int = Field(
        ..., description="Total non-NA observations in the lookback window."
    )
    observation_count_emitted: int = Field(
        ..., description="Number of valid z-score points in the output rows."
    )


class FXVolZScoreOutput(BaseModel):
    snapshot: FXVolZScoreSnapshot
    rows: list[FXVolZScoreRow] = Field(
        ...,
        description=(
            "LEGACY row shape — (trade_date, z_score) tuples. Kept for "
            "backward compatibility. New consumers should use the "
            "``time_series`` field which carries the canonical "
            "shared.schemas.TimeSeries shape."
        ),
    )
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Canonical TimeSeries shape (PR13 / event-study composable). "
            "units = Z_SCORE, series_name = "
            "'<pair_lower>_v<tenor>_atm_vol_zscore_252d'. Each row's "
            "date is YYYY-MM-DD. Rows where the trailing window is too "
            "short for a valid z-score are EXCLUDED."
        ),
    )
    units: str = Field(
        default="z_score",
        description="Legacy field — matches TimeSeriesUnits.Z_SCORE.",
    )

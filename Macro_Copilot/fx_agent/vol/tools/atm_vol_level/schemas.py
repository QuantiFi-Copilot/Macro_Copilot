"""Pydantic schemas for the FX ATM vol level snapshot tool.

Single-(pair, tenor) ATM vol snapshot: current vol, daily / weekly /
monthly absolute vol-point changes, rolling 252-day z-score, trailing
252-day high / low / percentile, observation count. Mirror of
get_fx_spot_level but for the ATM vol substrate.

Note on change semantics: vol changes here are ABSOLUTE vol points
(e.g. -0.3 = 0.3 vol points lower), NOT percent changes like spot.
This matches trader convention — vol moves are quoted in vol points,
not in percent of the vol level.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from fx_agent.vol._shared import FXVolStandardTenor


class FXAtmVolLevelInput(BaseModel):
    """Parameters for the FX ATM vol level snapshot."""

    pair: str = Field(
        ...,
        description=(
            "FX pair, e.g. 'EURUSD', 'USDMXN', 'EURJPY'. Must exist in "
            "instrument_master with instrument_type='fx_vol' and "
            "attributes.pair matching."
        ),
    )
    tenor: FXVolStandardTenor = Field(
        default="1M",
        description=(
            "ATM vol tenor. One of '1W', '1M', '3M', '6M', '12M' "
            "(standard strip). BBG ticker convention is V1Y for 12M; "
            "the converter handles that mapping. Extended tenors "
            "(ON/2W/2M/9M/2Y) ingested in DB but NOT exposed by this "
            "tool in V1."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched. Does NOT control the "
            "rolling 252-day z-score window (locked in config)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field override. None falls through "
            "to config default (PX_LAST). Use 'PX_BID' / 'PX_ASK' to "
            "inspect the bid/ask side of the ATM vol quote (substrate "
            "coverage: G10 std-tenor + EM std-tenor 115 instruments)."
        ),
    )


class FXAtmVolLevelMetrics(BaseModel):
    as_of_date: str
    pair: str
    tenor: str
    vendor_ticker: str
    current_atm_vol_pct: float = Field(
        ...,
        description=(
            "Current ATM implied vol in PERCENT (8.0 = 8% / year). "
            "Bloomberg ATM vol convention."
        ),
    )
    daily_change_vol_pts: Optional[float] = Field(
        None,
        description=(
            "1 trading-day change in ABSOLUTE vol points (not %). "
            "+0.3 = 0.3 vol points higher than yesterday."
        ),
    )
    weekly_change_vol_pts: Optional[float] = Field(
        None,
        description="5 trading-day change in absolute vol points.",
    )
    monthly_change_vol_pts: Optional[float] = Field(
        None,
        description="21 trading-day change in absolute vol points.",
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-day z-score of the ATM vol level. None when "
            "the series has fewer than z_score_min_periods observations."
        ),
    )
    high_252d: Optional[float] = Field(
        None, description="Highest ATM vol over the trailing 252 days."
    )
    low_252d: Optional[float] = Field(
        None, description="Lowest ATM vol over the trailing 252 days."
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank (0-100) of the current vol in the 252d range.",
    )
    observation_count: int = Field(..., description="Non-NA obs in the lookback window.")


class FXAtmVolLevelOutput(BaseModel):
    current_metrics: FXAtmVolLevelMetrics

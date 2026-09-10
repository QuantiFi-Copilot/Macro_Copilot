"""Pydantic schemas for the FX vol term structure tool.

Single-pair, all-standard-tenors ATM vol curve snapshot. Mirror of
get_fx_forward_curve but for the ATM vol substrate. Returns one row
per tenor (1W / 1M / 3M / 6M / 12M) with the current ATM vol level,
absolute period changes, and rolling 252-day z-score / percentile /
range computed on each tenor's own historical vol series.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FXVolTermStructureInput(BaseModel):
    pair: str = Field(
        ...,
        description=(
            "FX pair, e.g. 'EURUSD', 'USDMXN', 'EURJPY'. Must have at "
            "least one standard-tenor ATM vol observation in "
            "instrument_master."
        ),
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "DB fetch window for the z-score history. Does NOT control "
            "the rolling 252-day window itself (locked in config)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override. None falls through to PX_LAST."
        ),
    )


class FXVolTermStructureRow(BaseModel):
    tenor: str
    vendor_ticker: str
    as_of_date: str
    current_atm_vol_pct: float
    daily_change_vol_pts: Optional[float]
    weekly_change_vol_pts: Optional[float]
    monthly_change_vol_pts: Optional[float]
    z_score: Optional[float]
    high_252d: Optional[float]
    low_252d: Optional[float]
    percentile_252d: Optional[float]
    observation_count: int


class FXVolTermStructureOutput(BaseModel):
    pair: str
    rows: list[FXVolTermStructureRow] = Field(
        ...,
        description=(
            "One row per standard tenor (1W / 1M / 3M / 6M / 12M), "
            "ordered short -> long. Tenors with no DB data are "
            "silently skipped — the rows list has fewer entries when "
            "history is sparse."
        ),
    )

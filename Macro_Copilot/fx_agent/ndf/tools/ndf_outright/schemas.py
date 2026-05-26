"""Pydantic schemas for the FX NDF outright snapshot tool.

Single-(ndf, tenor) snapshot primitive: latest outright value, daily /
weekly / monthly percent changes, rolling 252-day z-score, trailing
252-day high / low / percentile, observation count. Analog of
get_fx_spot_level but for NDFs (which are quoted outright, not points).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from fx_agent.ndf._shared import NDFCode, NDFTenor


class FXNDFOutrightInput(BaseModel):
    """Parameters for the FX NDF outright snapshot."""

    ndf_code: NDFCode = Field(
        ...,
        description=(
            "NDF family code. One of: 'CCN+' (USDCNY), 'IRN+' (USDINR), "
            "'BCN+' (USDBRL), 'KWN+' (USDKRW), 'IHN+' (USDIDR), "
            "'NTN+' (USDTWD). The tool resolves the vendor_ticker as "
            "f'{ndf_code}{tenor} Curncy'."
        ),
    )
    tenor: NDFTenor = Field(
        default="1M",
        description=(
            "NDF tenor. One of '1W', '1M', '3M', '6M', '12M'. Default "
            "'1M' — matches the convention of the deliverable-forwards "
            "fx_carry tool."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of NDF outright history fetched from the DB "
            "to build the z-score / percentile / range. Does NOT control "
            "the rolling 252-day z-score window (locked in config)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field override. None (default) falls "
            "through to the config's default_ndf_field (PX_LAST). Use "
            "'PX_BID' / 'PX_ASK' to inspect the bid/ask side of the "
            "outright."
        ),
    )


class FXNDFOutrightMetrics(BaseModel):
    as_of_date: str
    ndf_code: str
    underlying_pair: str
    vendor_ticker: str
    tenor: str
    current_outright: float = Field(
        ...,
        description=(
            "Latest NDF outright value (same units as spot — already in "
            "USD per local currency, NOT in forward points)."
        ),
    )
    daily_change_pct: Optional[float] = Field(
        None,
        description="1 trading day % change of the outright.",
    )
    weekly_change_pct: Optional[float] = Field(
        None,
        description="5 trading day % change of the outright.",
    )
    monthly_change_pct: Optional[float] = Field(
        None,
        description="21 trading day % change of the outright.",
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-day z-score of the outright. None when the "
            "series has fewer than z_score_min_periods observations."
        ),
    )
    high_252d: Optional[float] = Field(
        None,
        description="Highest outright over the trailing 252 days.",
    )
    low_252d: Optional[float] = Field(
        None,
        description="Lowest outright over the trailing 252 days.",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank (0-100) of the current outright in the 252d range.",
    )
    observation_count: int = Field(
        ...,
        description="Number of observations in the lookback window.",
    )


class FXNDFOutrightOutput(BaseModel):
    current_metrics: FXNDFOutrightMetrics

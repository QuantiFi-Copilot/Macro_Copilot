"""Pydantic schemas for the OIS rate-level tool."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


class OISRateLevelInput(BaseModel):
    """Parameters the LLM must extract from the user to fetch an OIS
    rate level.  Every field maps directly to a filter column on
    ``macro_data.v_market_data_daily_enriched``."""

    curve_family: str = Field(
        ...,
        description=(
            "OIS curve family identifier as stored in instrument_master. "
            "Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', 'GBP_SONIA_OIS', "
            "'JPY_OIS', 'AUD_OIS', 'CAD_OIS'."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor point on the OIS curve.  OIS curves have a dense "
            "short-end grid: '1W', '1M', '2M', '3M', '6M', '9M', '1Y', "
            "'2Y', '3Y', '5Y', '10Y', '20Y', '30Y'."
        ),
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Number of calendar days of history used for computing "
            "z-score / high / low / percentile.  The rolling z-score "
            "window is ALWAYS a fixed 252 trading days; this parameter "
            "only controls how far back the tool looks for the "
            "observation count and context stats."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description=(
            "Observation field to use.  Defaults to 'PX_LAST' — the "
            "mid par swap rate from Bloomberg.  Other valid values: "
            "'PX_BID', 'PX_ASK'.  Must match the exact value stored in "
            "market_data_daily.field_name, NOT the playbook metric_id."
        ),
    )


class OISRateLevelMetrics(BaseModel):
    """Snapshot metrics for the most recent trade date."""
    as_of_date: str
    curve_family: str
    tenor: str
    current_rate_pct: Optional[float] = Field(None, description="Latest par swap rate (percent).")
    daily_change_bps: Optional[float] = Field(None, description="1-trading-day change in bps.")
    weekly_change_bps: Optional[float] = Field(None, description="5-trading-day change in bps.")
    monthly_change_bps: Optional[float] = Field(None, description="22-trading-day (~1 month) change in bps.")
    z_score: Optional[float] = Field(None, description="Rolling 252-day z-score of the rate vs its own history.")
    high_252d_pct: Optional[float] = Field(None, description="Trailing 252-day high (percent).")
    low_252d_pct: Optional[float] = Field(None, description="Trailing 252-day low (percent).")
    percentile_252d: Optional[float] = Field(None, description="Where the current rate sits within the 252-day range (0-100).")
    observation_count: int = Field(..., description="Number of observations in the display window.")


class OISRateLevelOutput(BaseModel):
    """Top-level response the MCP server returns."""
    current_metrics: OISRateLevelMetrics

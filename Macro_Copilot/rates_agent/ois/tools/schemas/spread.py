"""Pydantic schemas for the OIS curve spread tool."""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class OISCurveSpreadInput(BaseModel):
    """Parameters the LLM must extract from the user to run an OIS
    curve-spread calculation.  Every field maps directly to a filter
    column on ``macro_data.v_market_data_daily_enriched``."""

    curve_family: str = Field(
        ...,
        description=(
            "OIS curve family identifier as stored in instrument_master. "
            "Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', 'GBP_SONIA_OIS', "
            "'JPY_OIS', 'AUD_OIS', 'CAD_OIS'."
        ),
    )
    short_tenor: str = Field(
        ...,
        description=(
            "Short leg of the spread.  OIS curves have a dense short-end "
            "grid: '1W', '1M', '2M', '3M', '6M', '9M', '1Y', '2Y', '3Y'."
        ),
    )
    long_tenor: str = Field(
        ...,
        description=(
            "Long leg of the spread.  Examples: '2Y', '5Y', '10Y', '20Y', '30Y'."
        ),
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Number of calendar days of displayed history in the time_series "
            "output.  Defaults to 365 (1 year).  The z-score rolling window "
            "is always a fixed 252 trading days regardless of this value."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description=(
            "The observation field to use.  Defaults to 'PX_LAST' — the "
            "mid par swap rate quote from Bloomberg.  Other valid values: "
            "'PX_BID', 'PX_ASK'.  Must match the exact value in the "
            "database, NOT the playbook metric_id."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_differ(self) -> "OISCurveSpreadInput":
        if self.short_tenor == self.long_tenor:
            raise ValueError(
                f"short_tenor and long_tenor must be different, "
                f"but both are '{self.short_tenor}'."
            )
        return self


class OISCurveSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics returned for the most recent trade date."""
    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    spread_label: str = Field(..., description="Human-readable label, e.g. '2s10s'.")
    current_spread_bps: float
    daily_change_bps: Optional[float] = Field(None, description="1-day change; None if fewer than 2 observations.")
    current_z_score: Optional[float] = Field(None, description="Rolling z-score of the spread vs its own trailing window.")
    rolling_window_days: int = Field(..., description="Window used for the z-score calculation.")
    short_tenor_rate: Optional[float] = Field(None, description="Latest par swap rate on the short leg (percent).")
    long_tenor_rate: Optional[float] = Field(None, description="Latest par swap rate on the long leg (percent).")


class OISCurveSpreadTimeSeriesRow(BaseModel):
    """Single row in the OIS spread time-series array."""
    date: str
    spread_bps: float
    z_score: Optional[float] = None


class OISCurveSpreadOutput(BaseModel):
    """Top-level response the MCP server returns to the orchestrator."""
    current_metrics: OISCurveSpreadCurrentMetrics
    time_series: List[OISCurveSpreadTimeSeriesRow]

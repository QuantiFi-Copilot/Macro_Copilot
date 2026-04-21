"""Pydantic schemas for the OIS cross-market spread tool."""
from __future__ import annotations
from typing import List, Optional
from pydantic import BaseModel, Field, model_validator


class OISCrossMarketSpreadInput(BaseModel):
    """Parameters for computing the rate differential between the same
    tenor on two different OIS curves (e.g. SOFR 2Y − ESTR 2Y)."""

    curve_family_1: str = Field(
        ...,
        description=(
            "First (numerator) OIS curve.  spread = curve_family_1 − "
            "curve_family_2.  Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', "
            "'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', 'CAD_OIS'."
        ),
    )
    curve_family_2: str = Field(
        ...,
        description=(
            "Second (denominator) OIS curve.  Must be different from "
            "curve_family_1."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor point to compare.  Examples: '1M', '3M', '6M', "
            "'1Y', '2Y', '5Y', '10Y'."
        ),
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Calendar days of displayed history in the time_series "
            "output.  The rolling z-score window is always a fixed 252 "
            "trading days regardless of this value."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description=(
            "Observation field to use.  Defaults to 'PX_LAST' (mid par "
            "swap rate).  Other valid: 'PX_BID', 'PX_ASK'."
        ),
    )

    @model_validator(mode="after")
    def _curves_must_differ(self) -> "OISCrossMarketSpreadInput":
        if self.curve_family_1 == self.curve_family_2:
            raise ValueError(
                f"curve_family_1 and curve_family_2 must be different, "
                f"but both are '{self.curve_family_1}'."
            )
        return self


class OISCrossMarketSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for the most recent trade date."""
    as_of_date: str
    curve_family_1: str
    curve_family_2: str
    tenor: str
    spread_label: str = Field(..., description="Human-readable label, e.g. 'USD_SOFR_OIS-EUR_ESTR_OIS 2Y'.")
    current_spread_bps: Optional[float]
    daily_change_bps: Optional[float] = None
    weekly_change_bps: Optional[float] = None
    monthly_change_bps: Optional[float] = None
    current_z_score: Optional[float] = None
    rolling_window_days: int
    high_252d_bps: Optional[float] = None
    low_252d_bps: Optional[float] = None
    percentile_252d: Optional[float] = None
    curve_family_1_rate: Optional[float] = Field(None, description="Latest par swap rate on curve_family_1 (percent).")
    curve_family_2_rate: Optional[float] = Field(None, description="Latest par swap rate on curve_family_2 (percent).")


class OISCrossMarketSpreadTimeSeriesRow(BaseModel):
    """Single row in the spread time-series array."""
    date: str
    spread_bps: float
    z_score: Optional[float] = None


class OISCrossMarketSpreadOutput(BaseModel):
    """Top-level response the MCP server returns."""
    current_metrics: OISCrossMarketSpreadCurrentMetrics
    time_series: List[OISCrossMarketSpreadTimeSeriesRow]

"""
Pydantic input/output schemas for the Rates Agent math tools.

These schemas define the strict contract between the LLM orchestration layer
(via MCP) and the deterministic Python math functions.  The LLM extracts
parameters from the user, validates them against these models, and passes them
to the tool.  No free-form text reaches the database layer.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


# ============================================================================
# CURVE SPREAD TOOL
# ============================================================================

class CurveSpreadInput(BaseModel):
    """Parameters the LLM must extract from the user to run a curve-spread
    calculation.  Every field maps directly to a filter column on
    ``macro_data.v_market_data_daily_enriched``."""

    curve_family: str = Field(
        ...,
        description=(
            "Curve family identifier as stored in instrument_master. "
            "Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT'."
        ),
    )
    short_tenor: str = Field(
        ...,
        description="The short leg of the spread, e.g. '2Y', '3Y', '5Y'.",
    )
    long_tenor: str = Field(
        ...,
        description="The long leg of the spread, e.g. '10Y', '20Y', '30Y'.",
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,  # ~20 years hard ceiling
        description=(
            "Number of calendar days of displayed history in the time_series "
            "output.  Defaults to 365 (1 year).  The z-score rolling window "
            "is always a fixed 252 trading days regardless of this value."
        ),
    )
    field_name: str = Field(
        default="YLD_YTM_MID",
        description=(
            "The observation field to use.  Defaults to mid yield-to-maturity "
            "(YLD_YTM_MID) — this is the Bloomberg field mnemonic as stored in "
            "market_data_daily.field_name after extraction.  Must match the "
            "exact value in the database, NOT the playbook metric_id."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_differ(self) -> "CurveSpreadInput":
        if self.short_tenor == self.long_tenor:
            raise ValueError(
                f"short_tenor and long_tenor must be different, "
                f"but both are '{self.short_tenor}'."
            )
        return self


class CurveSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics returned for the most recent trade date."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    spread_label: str = Field(
        ..., description="Human-readable label, e.g. '2s10s'."
    )
    current_spread_bps: float
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change; None if fewer than 2 observations."
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the spread vs its own trailing window.  "
            "None when insufficient history."
        ),
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation."
    )
    short_tenor_yield: Optional[float] = Field(
        None, description="Latest yield on the short leg (percent)."
    )
    long_tenor_yield: Optional[float] = Field(
        None, description="Latest yield on the long leg (percent)."
    )


class CurveSpreadTimeSeriesRow(BaseModel):
    """Single row in the spread time-series array."""

    date: str
    spread_bps: float
    z_score: Optional[float] = None


class CurveSpreadOutput(BaseModel):
    """Top-level response the MCP server returns to the orchestrator."""

    current_metrics: CurveSpreadCurrentMetrics
    time_series: List[CurveSpreadTimeSeriesRow]


# ============================================================================
# YIELD LEVEL TOOL
# ============================================================================

class YieldLevelInput(BaseModel):
    """Parameters for querying a single yield point on a curve."""

    curve_family: str = Field(
        ...,
        description=(
            "Curve family identifier as stored in instrument_master. "
            "Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT', "
            "'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "The tenor point to query, e.g. '1Y', '2Y', '3Y', '5Y', "
            "'7Y', '10Y', '20Y', '30Y'."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history to use for z-score, high/low, and "
            "percentile calculations.  Defaults to 365."
        ),
    )
    field_name: str = Field(
        default="YLD_YTM_MID",
        description=(
            "The observation field to use.  Defaults to mid yield-to-maturity "
            "(YLD_YTM_MID).  Must match the exact value in the database."
        ),
    )


class YieldLevelMetrics(BaseModel):
    """Deterministic snapshot for a single yield point."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    tenor: str
    current_yield_pct: float = Field(
        ..., description="Current yield in percent (e.g. 4.25 = 4.25%)."
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in basis points."
    )
    weekly_change_bps: Optional[float] = Field(
        None, description="5-trading-day change in basis points."
    )
    monthly_change_bps: Optional[float] = Field(
        None, description="21-trading-day change in basis points."
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-trading-day z-score of the yield level.  "
            "None when insufficient history."
        ),
    )
    high_252d_pct: Optional[float] = Field(
        None, description="Highest yield over the trailing 252 trading days (percent)."
    )
    low_252d_pct: Optional[float] = Field(
        None, description="Lowest yield over the trailing 252 trading days (percent)."
    )
    percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank of the current yield within the trailing "
            "252-trading-day range (0 = at the low, 100 = at the high)."
        ),
    )
    observation_count: int = Field(
        ..., description="Number of trading days in the calculation window."
    )


class YieldLevelOutput(BaseModel):
    """Top-level response for the yield level tool."""

    current_metrics: YieldLevelMetrics

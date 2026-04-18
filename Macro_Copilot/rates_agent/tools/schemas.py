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
        le=7300,
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


# ============================================================================
# CROSS-MARKET SPREAD TOOL
# ============================================================================

class CrossMarketSpreadInput(BaseModel):
    """Parameters for computing the yield differential between the same tenor
    on two different sovereign curves (e.g. UST 10Y − BUND 10Y)."""

    curve_family_1: str = Field(
        ...,
        description=(
            "The first (numerator) curve family.  The spread is computed as "
            "curve_family_1 − curve_family_2.  Convention: for BTP-Bund spread, "
            "use curve_family_1='IT_BTP'.  "
            "Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT', "
            "'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'."
        ),
    )
    curve_family_2: str = Field(
        ...,
        description=(
            "The second (denominator) curve family.  Convention: for BTP-Bund "
            "spread, use curve_family_2='DE_BUND'.  "
            "Examples: same as curve_family_1."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "The tenor point to compare across markets, e.g. '2Y', '5Y', "
            "'10Y', '30Y'."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of displayed history.  Defaults to 365.  "
            "The z-score always uses a fixed 252-trading-day rolling window."
        ),
    )
    field_name: str = Field(
        default="YLD_YTM_MID",
        description=(
            "The observation field to use.  Defaults to 'YLD_YTM_MID'.  "
            "Must match the exact value in the database."
        ),
    )

    @model_validator(mode="after")
    def _curves_must_differ(self) -> "CrossMarketSpreadInput":
        if self.curve_family_1 == self.curve_family_2:
            raise ValueError(
                f"curve_family_1 and curve_family_2 must be different, "
                f"but both are '{self.curve_family_1}'.  "
                f"For same-curve spreads, use the curve_spread tool instead."
            )
        return self


class CrossMarketSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for a cross-market yield differential."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family_1: str
    curve_family_2: str
    tenor: str
    spread_label: str = Field(
        ..., description="Human-readable label, e.g. 'IT_BTP-DE_BUND 10Y'."
    )
    current_spread_bps: float = Field(
        ..., description="Current yield differential in basis points."
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in the spread (bps)."
    )
    weekly_change_bps: Optional[float] = Field(
        None, description="5-trading-day change in the spread (bps)."
    )
    monthly_change_bps: Optional[float] = Field(
        None, description="21-trading-day change in the spread (bps)."
    )
    current_z_score: Optional[float] = Field(
        None, description="Rolling 252-day z-score of the spread."
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation."
    )
    high_252d_bps: Optional[float] = Field(
        None, description="Highest spread over the trailing 252 trading days (bps)."
    )
    low_252d_bps: Optional[float] = Field(
        None, description="Lowest spread over the trailing 252 trading days (bps)."
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within the trailing 252-day range (0-100).",
    )
    curve_family_1_yield: Optional[float] = Field(
        None, description="Latest yield on curve_family_1 (percent)."
    )
    curve_family_2_yield: Optional[float] = Field(
        None, description="Latest yield on curve_family_2 (percent)."
    )


class CrossMarketSpreadTimeSeriesRow(BaseModel):
    """Single row in the cross-market spread time-series."""

    date: str
    spread_bps: float
    z_score: Optional[float] = None


class CrossMarketSpreadOutput(BaseModel):
    """Top-level response for the cross-market spread tool."""

    current_metrics: CrossMarketSpreadCurrentMetrics
    time_series: List[CrossMarketSpreadTimeSeriesRow]


# ============================================================================
# BUTTERFLY / CURVATURE TOOL
# ============================================================================

class ButterflyInput(BaseModel):
    """Parameters for computing the 3-point butterfly (curvature) on a
    single sovereign curve.

    butterfly = 2 × belly − short − long (in bps).
    A positive butterfly means the belly is cheap (yielding more than
    the linear interpolation of the wings)."""

    curve_family: str = Field(
        ...,
        description=(
            "Curve family identifier as stored in instrument_master. "
            "Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT'."
        ),
    )
    short_tenor: str = Field(
        ...,
        description="The short wing, e.g. '2Y'.",
    )
    belly_tenor: str = Field(
        ...,
        description="The belly (body) of the butterfly, e.g. '5Y'.",
    )
    long_tenor: str = Field(
        ...,
        description="The long wing, e.g. '10Y'.",
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of displayed history.  Defaults to 365.  "
            "The z-score always uses a fixed 252-trading-day rolling window."
        ),
    )
    field_name: str = Field(
        default="YLD_YTM_MID",
        description=(
            "The observation field to use.  Defaults to 'YLD_YTM_MID'.  "
            "Must match the exact value in the database."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_all_differ(self) -> "ButterflyInput":
        tenors = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(tenors)) != 3:
            raise ValueError(
                f"short_tenor, belly_tenor, and long_tenor must all be different, "
                f"but got {tenors}."
            )
        return self


class ButterflyCurrentMetrics(BaseModel):
    """Snapshot metrics for a 3-point butterfly."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    butterfly_label: str = Field(
        ..., description="Human-readable label, e.g. '2s5s10s'."
    )
    current_butterfly_bps: float = Field(
        ...,
        description=(
            "Current butterfly in bps.  Positive = belly is cheap, "
            "negative = belly is rich."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in the butterfly (bps)."
    )
    current_z_score: Optional[float] = Field(
        None, description="Rolling 252-day z-score of the butterfly."
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation."
    )
    high_252d_bps: Optional[float] = Field(
        None, description="Highest butterfly over the trailing 252 trading days (bps)."
    )
    low_252d_bps: Optional[float] = Field(
        None, description="Lowest butterfly over the trailing 252 trading days (bps)."
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within the trailing 252-day range (0-100).",
    )
    wing_short_bps: Optional[float] = Field(
        None,
        description=(
            "Component spread: belly − short (bps).  Measures front-end "
            "steepness contribution to the butterfly."
        ),
    )
    wing_long_bps: Optional[float] = Field(
        None,
        description=(
            "Component spread: long − belly (bps).  Measures back-end "
            "steepness contribution to the butterfly."
        ),
    )
    short_tenor_yield: Optional[float] = Field(
        None, description="Latest yield on the short wing (percent)."
    )
    belly_tenor_yield: Optional[float] = Field(
        None, description="Latest yield on the belly (percent)."
    )
    long_tenor_yield: Optional[float] = Field(
        None, description="Latest yield on the long wing (percent)."
    )


class ButterflyTimeSeriesRow(BaseModel):
    """Single row in the butterfly time-series."""

    date: str
    butterfly_bps: float
    z_score: Optional[float] = None


class ButterflyOutput(BaseModel):
    """Top-level response for the butterfly tool."""

    current_metrics: ButterflyCurrentMetrics
    time_series: List[ButterflyTimeSeriesRow]


# ============================================================================
# CURVE MOVE CLASSIFIER TOOL
# ============================================================================

class CurveRegimeInput(BaseModel):
    """Parameters for deterministic curve-move classification."""

    curve_family: str = Field(
        ...,
        description=(
            "Curve family identifier as stored in instrument_master. "
            "Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT'."
        ),
    )
    front_tenor: str = Field(
        default="2Y",
        description=(
            "The front-end (short) leg of the curve for classification. "
            "Defaults to '2Y'.  Change to '3Y' or '5Y' for different "
            "curve segments."
        ),
    )
    back_tenor: str = Field(
        default="10Y",
        description=(
            "The back-end (long) leg of the curve for classification. "
            "Defaults to '10Y'.  Change to '30Y' for ultra-long analysis."
        ),
    )
    lookback_period: str = Field(
        default="1d",
        description=(
            "The period over which to measure the move.  "
            "Valid values: '1d' (today's move), '5d' (weekly), "
            "'22d' (monthly)."
        ),
    )
    field_name: str = Field(
        default="YLD_YTM_MID",
        description=(
            "The observation field to use.  Defaults to 'YLD_YTM_MID'.  "
            "Must match the exact value in the database."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_differ(self) -> "CurveRegimeInput":
        if self.front_tenor == self.back_tenor:
            raise ValueError(
                f"front_tenor and back_tenor must be different, "
                f"but both are '{self.front_tenor}'."
            )
        return self


class CurveRegimeCurrentMetrics(BaseModel):
    """Deterministic curve-move classification with supporting numbers."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    prior_date: str = Field(
        ..., description="The comparison date at the start of the lookback."
    )
    curve_family: str
    lookback_period: str = Field(
        ..., description="The lookback period used: '1d', '5d', or '22d'."
    )
    spread_label: str = Field(
        ..., description="Human-readable label, e.g. '2s10s'."
    )
    regime_tag: str = Field(
        ...,
        description=(
            "Deterministic classification: BULL_STEEPENER, BEAR_STEEPENER, "
            "BULL_FLATTENER, BEAR_FLATTENER, PARALLEL_SHIFT, or TWIST."
        ),
    )
    regime_description: str = Field(
        ..., description="Plain-English explanation of the regime."
    )
    front_tenor: str
    back_tenor: str
    front_yield_current: Optional[float] = Field(
        None, description="Current yield on the front leg (percent)."
    )
    back_yield_current: Optional[float] = Field(
        None, description="Current yield on the back leg (percent)."
    )
    front_yield_prior: Optional[float] = Field(
        None, description="Prior yield on the front leg (percent)."
    )
    back_yield_prior: Optional[float] = Field(
        None, description="Prior yield on the back leg (percent)."
    )
    front_change_bps: Optional[float] = Field(
        None, description="Change in the front leg over the lookback (bps)."
    )
    back_change_bps: Optional[float] = Field(
        None, description="Change in the back leg over the lookback (bps)."
    )
    spread_current_bps: Optional[float] = Field(
        None, description="Current spread (back − front) in bps."
    )
    spread_prior_bps: Optional[float] = Field(
        None, description="Prior spread (back − front) in bps."
    )
    spread_change_bps: Optional[float] = Field(
        None, description="Change in the spread over the lookback (bps)."
    )


class CurveRegimeOutput(BaseModel):
    """Top-level response for the curve regime classifier."""

    current_metrics: CurveRegimeCurrentMetrics


# ============================================================================
# SCANNER / EXTREME-MOVE TOOL
# ============================================================================

class ScannerInput(BaseModel):
    """Parameters for the z-score scanner that screens all sovereign
    instruments for statistical extremes."""

    curve_families: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of curve families to scan.  If None, scans ALL "
            "sovereign benchmark curves in the database.  "
            "Examples: ['UST', 'DE_BUND', 'UK_GILT'] to scan only those."
        ),
    )
    top_n: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of top extreme results to return (default 10).",
    )
    min_abs_z_score: float = Field(
        default=1.5,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold.  Only instruments with "
            "|z-score| >= this value are included.  Defaults to 1.5."
        ),
    )
    field_name: str = Field(
        default="YLD_YTM_MID",
        description=(
            "The observation field to scan.  Defaults to 'YLD_YTM_MID'.  "
            "Must match the exact value in the database."
        ),
    )


class ScannerResultRow(BaseModel):
    """Single ranked result from the scanner."""

    rank: int = Field(..., description="Rank by absolute z-score (1 = most extreme).")
    curve_family: str
    tenor: str
    as_of_date: str
    current_yield_pct: Optional[float] = Field(
        None, description="Current yield in percent."
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in basis points."
    )
    z_score: Optional[float] = Field(
        None, description="252-day rolling z-score."
    )
    high_252d_pct: Optional[float] = Field(
        None, description="252-day trailing high (percent)."
    )
    low_252d_pct: Optional[float] = Field(
        None, description="252-day trailing low (percent)."
    )
    percentile_252d: Optional[float] = Field(
        None, description="Percentile within 252-day range (0-100)."
    )
    signal: str = Field(
        ...,
        description=(
            "'EXTREME_HIGH' if z-score is positive (yield above average), "
            "'EXTREME_LOW' if z-score is negative (yield below average)."
        ),
    )


class ScannerOutput(BaseModel):
    """Top-level response for the scanner tool."""

    scan_summary: str = Field(
        ..., description="Human-readable summary of the scan results."
    )
    results: List[ScannerResultRow]

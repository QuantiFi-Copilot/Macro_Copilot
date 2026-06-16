"""Pydantic schemas for the beta_adjusted_spread tool.

Per the v6 sprint plan (docs/architecture/tool_architecture.md, A13):
the user-facing input surface is intentionally narrow — the central
knob is ``regression_window_days``, plus the four flat scalar fields
identifying the target / regressor (curve_family + tenor each).  Every
other methodology ancillary lives in YAML and is NOT user-overridable
in V1.

Transport
---------
Per the v6 plan Delta H + Delta N: beta_adjusted_spread takes only
flat scalar inputs (no nested ``SeriesSpec``), so it ships with both
**MCP and FastAPI GET** this sprint — same transport shape as
zscore_custom.

Output uses the shared ``TimeSeries`` shape from ``shared.schemas``.
The residual is emitted in BPS (after the explicit *100
conversion in compute.py); the residual z-score is unitless.

Spread direction convention
---------------------------
The hedge-adjusted spread is ``target − β·regressor − α``.  Match
cross_market_spread's `cf1 − cf2` convention by passing
target=peripheral / cf1, regressor=core / cf2 (e.g., target=BTP,
regressor=Bund for "beta-adjusted BTP-Bund").  This is the caller's
responsibility — locked in methodology.assumptions in the YAML.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries


class BetaAdjustedSpreadInput(BaseModel):
    """Parameters the LLM extracts to compute a beta-adjusted spread.

    The single methodological choice exposed to the user is
    ``regression_window_days`` (plus the structural target / regressor
    identification).  Every other knob is YAML-locked.
    """

    model_config = ConfigDict(extra="forbid")

    target_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "The y in y on x.  Curve family identifier — e.g. 'IT_BTP', "
            "'UST', 'FR_OAT', 'ES_BONO'.  Pair with target_tenor to "
            "identify a single sovereign yield series."
        ),
    )
    target_tenor: str = Field(
        ...,
        min_length=1,
        description="Target tenor — e.g. '2Y', '10Y', '30Y'.",
    )
    regressor_curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "The x in y on x.  Curve family identifier — typically the "
            "core / hedge curve in a peripheral-vs-core RV (e.g. "
            "'DE_BUND' for BTP-Bund or OAT-Bund).  Cannot equal "
            "target_curve_family + target_tenor (that's a regression "
            "of a series on itself)."
        ),
    )
    regressor_tenor: str = Field(
        ...,
        min_length=1,
        description="Regressor tenor — e.g. '10Y'.",
    )
    regression_window_days: int = Field(
        ...,
        ge=10,
        le=2520,
        description=(
            "Trailing-window length in trading-day rows for each "
            "rolling fit.  This is the tool's central methodological "
            "choice — set per request.  Typical desk values: 60 "
            "(tactical hedge ratio), 252 (annual), 504 (two-year).  "
            "Constrained to [10, 2520].  compute() returns a "
            "controlled error envelope when "
            "regression_window_days < regression_min_periods (the YAML "
            "convention), mirroring rolling_regression's and "
            "zscore_custom's small-window guards."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of *displayed* history.  Does NOT control "
            "the rolling window length — that is "
            "regression_window_days, the central knob.  Also does "
            "NOT control the residual z-score's rolling window, "
            "which is fixed at 252 in V1 (see z_score_window_days "
            "in config.yaml)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field — applies to BOTH target "
            "and regressor legs.  When None (default), the tool falls "
            "through to ``default_field_name`` from config.yaml "
            "(currently 'YLD_YTM_MID').  LLM/HTTP wrappers MUST "
            "translate their wire-level sentinel (empty string for "
            "MCP, missing param for FastAPI) to None before "
            "constructing this input — otherwise the YAML default is "
            "silently shadowed."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @model_validator(mode="after")
    def _target_must_differ_from_regressor(
        self,
    ) -> "BetaAdjustedSpreadInput":
        """Reject (curve_family, tenor) collisions between target and
        regressor.  A regression of a series on itself is degenerate
        (perfect-fit beta=1 alpha=0 residual=0); surface as a clean
        validation error."""
        if (self.target_curve_family, self.target_tenor) == (
            self.regressor_curve_family, self.regressor_tenor
        ):
            raise ValueError(
                f"target ({self.target_curve_family} "
                f"{self.target_tenor}) cannot equal regressor "
                f"({self.regressor_curve_family} "
                f"{self.regressor_tenor}); a regression of a series "
                "on itself is degenerate."
            )
        return self


class BetaAdjustedSpreadMetrics(BaseModel):
    """Snapshot metrics for a beta-adjusted RV query."""

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    target_curve_family: str
    target_tenor: str
    regressor_curve_family: str
    regressor_tenor: str
    spread_label: str = Field(
        ...,
        description=(
            "Human-readable label, e.g. 'BTP-Bund 10Y (beta-adjusted, "
            "252d)'."
        ),
    )
    current_beta: Optional[float] = Field(
        None,
        description=(
            "Current hedge ratio — unitless (target-percent per "
            "regressor-percent).  None when the latest window has "
            "fewer than regression_min_periods valid rows OR the "
            "regressor was constant in-window."
        ),
    )
    current_alpha_pct: Optional[float] = Field(
        None,
        description=(
            "Current intercept (in yield-percent units), rounded per "
            "``alpha_round_decimals``.  None when the latest fit "
            "couldn't be computed."
        ),
    )
    current_residual_bps: Optional[float] = Field(
        None,
        description=(
            "Current residual `target − β·regressor − α`, converted "
            "to bps via *100, rounded per ``bps_round_decimals``.  "
            "Positive ⇒ target yield ABOVE the regression-implied "
            "fair value, i.e. target is CHEAP relative to the hedge "
            "line; negative ⇒ target is RICH (yield below the line).  "
            "None when the fit couldn't be computed."
        ),
    )
    current_residual_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the bps residual (252-day window in V1).  "
            "Rounded per ``z_score_round_decimals``.  None during the "
            "warmup period or when the fit at t couldn't be computed."
        ),
    )
    current_r_squared: Optional[float] = Field(
        None,
        description=(
            "In-window R² for the latest fit, in [0, 1].  Mirrors "
            "rolling_regression's R²."
        ),
    )
    current_condition_flag: int = Field(
        ...,
        ge=0,
        le=1,
        description=(
            "Quality flag for the latest row.  0 = OK, 1 = the design "
            "matrix's condition number exceeded the YAML's "
            "``condition_number_warning_threshold`` (regressor "
            "constant in-window; coefficients suppressed) OR lstsq "
            "raised numerically."
        ),
    )
    regression_window_days_used: int = Field(
        ...,
        description="Echoes the user's input central knob.",
    )
    regression_min_periods_used: int = Field(
        ...,
        description=(
            "min_periods used for the rolling fit — sourced from "
            "YAML, echoed for transparency."
        ),
    )
    z_score_window_days_used: int = Field(
        ...,
        description=(
            "Window used for the residual z-score — sourced from YAML "
            "(fixed at 252 in V1), echoed for transparency."
        ),
    )
    add_constant_used: bool = Field(
        ...,
        description=(
            "Whether the rolling fit included an intercept term — "
            "sourced from YAML, echoed for transparency."
        ),
    )
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of trading days in the displayed lookback_days "
            "window after alignment + ffill."
        ),
    )


class BetaAdjustedSpreadOutput(BaseModel):
    """Top-level response for the beta_adjusted_spread tool."""

    model_config = ConfigDict(extra="forbid")

    current_metrics: BetaAdjustedSpreadMetrics
    time_series_beta: TimeSeries = Field(
        ...,
        description=(
            "Hedge-ratio time series.  units = 'ratio'."
        ),
    )
    time_series_residual: TimeSeries = Field(
        ...,
        description=(
            "Per-row residual `target − β·regressor − α` converted to "
            "BPS via *100.  units = 'bps'."
        ),
    )
    time_series_residual_z_score: TimeSeries = Field(
        ...,
        description=(
            "Rolling z-score of the bps residual (252d window in V1).  "
            "units = 'z_score'."
        ),
    )


__all__ = [
    "BetaAdjustedSpreadInput",
    "BetaAdjustedSpreadMetrics",
    "BetaAdjustedSpreadOutput",
]

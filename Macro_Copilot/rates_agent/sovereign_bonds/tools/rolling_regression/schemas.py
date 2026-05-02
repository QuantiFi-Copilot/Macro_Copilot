"""Pydantic schemas for the rolling_regression tool.

Per the v6 sprint plan (docs/architecture/tool_architecture.md, A13):
the user-facing input surface is intentionally narrow — the central
knob is ``regression_window_days``, plus the structural inputs
``target_spec`` and ``regressor_specs``.  Every other methodology
ancillary (`min_periods`, `add_constant`, `solver`, `condition`-
threshold, ffill, rounding) lives in YAML and is NOT user-overridable
in V1.

Transport
---------
Per Delta H of the v6 plan: rolling_regression has nested inputs
(`SeriesSpec`, `List[SeriesSpec]`) so it ships with **MCP only this
sprint**.  No FastAPI route until the UI-integration PR.

Output uses the shared ``TimeSeries`` shape from ``shared.schemas`` —
this tool emits multiple time series (one beta series per regressor,
plus residual / R² / condition_flag) so the snapshot returns
``time_series_betas`` (a list of ``TimeSeries`` keyed by regressor
label) plus three named ``TimeSeries`` for residual / R² / flag.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import SeriesSpec, TimeSeries


class RollingRegressionInput(BaseModel):
    """Parameters the LLM extracts to run a rolling-OLS regression.

    The single methodological choice exposed to the user is
    ``regression_window_days`` (plus the structural specs).  Every
    other knob is YAML-locked.
    """

    model_config = ConfigDict(extra="forbid")

    target_spec: SeriesSpec = Field(
        ...,
        description=(
            "The target series — y in the regression of y on X.  "
            "Identifies a single (curve_family, tenor) sovereign yield "
            "series.  ``field_name=None`` falls through to the YAML's "
            "default_field_name."
        ),
    )
    regressor_specs: List[SeriesSpec] = Field(
        ...,
        min_length=1,
        description=(
            "One or more regressor series — the columns of X.  Each is "
            "a (curve_family, tenor) sovereign yield series with optional "
            "field_name.  Order is preserved in the output betas."
        ),
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
            "Constrained to [10, 2520] to keep the rolling fit "
            "meaningful (below ~10 the design matrix is rank-deficient "
            "with even one regressor + constant; above ~2520 the data "
            "history may not support the window).  compute() returns a "
            "controlled error envelope when "
            "regression_window_days < regression_min_periods (the YAML "
            "convention), mirroring the zscore_custom small-window "
            "guard's pattern."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of *displayed* history.  Does NOT control "
            "the rolling window length — that is "
            "regression_window_days, the central knob."
        ),
    )

    @model_validator(mode="after")
    def _target_must_differ_from_each_regressor(
        self,
    ) -> "RollingRegressionInput":
        """Reject (curve_family, tenor) collisions between target and
        any regressor.  A regression of a series on itself is
        degenerate (perfect-fit beta=1 alpha=0) and almost certainly
        a caller mistake; surface it as a clean validation error.

        We compare on (curve_family, tenor) — NOT field_name — because
        it's the same underlying series whether the field_name differs
        or not, and a regression on the same series with two different
        field flavours (e.g. mid vs bid) is still a degenerate use-
        case for V1.
        """
        target_key = (self.target_spec.curve_family, self.target_spec.tenor)
        for i, r in enumerate(self.regressor_specs):
            if (r.curve_family, r.tenor) == target_key:
                raise ValueError(
                    f"regressor_specs[{i}] is the same (curve_family, "
                    f"tenor) as target_spec ({target_key}); a regression "
                    "of a series on itself is degenerate.  Drop the "
                    "duplicate or pick a different regressor."
                )
        return self


class RollingRegressionMetrics(BaseModel):
    """Snapshot metrics for a rolling-OLS regression query."""

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    target_label: str = Field(
        ...,
        description=(
            "Canonical label for the target series — e.g. 'UST_10Y' "
            "or 'IT_BTP_10Y'."
        ),
    )
    regressor_labels: List[str] = Field(
        ...,
        description=(
            "Canonical labels for the regressors, in the same order as "
            "the input regressor_specs."
        ),
    )
    current_alpha_pct: Optional[float] = Field(
        None,
        description=(
            "Current intercept (in yield-percent units), rounded per "
            "``alpha_round_decimals``.  None when the latest window "
            "has fewer than `regression_min_periods` valid rows OR "
            "the design matrix's condition number exceeds the YAML "
            "threshold."
        ),
    )
    current_betas: dict = Field(
        ...,
        description=(
            "Current betas, keyed by regressor label.  Each value is "
            "a unitless ratio (target-units per regressor-unit; both "
            "are yield-percent so the beta is dimensionless).  Values "
            "are None when the latest window is too short or the "
            "design matrix is near-singular."
        ),
    )
    current_residual_pct: Optional[float] = Field(
        None,
        description=(
            "Current fit residual (in yield-percent units, NOT bps).  "
            "Downstream tools that need bps (beta_adjusted_spread) "
            "convert via *100 explicitly."
        ),
    )
    current_r_squared: Optional[float] = Field(
        None,
        description=(
            "In-window R² for the latest fit, in [0, 1].  None for "
            "warmup rows or when the target series is constant in the "
            "window (SS_tot = 0)."
        ),
    )
    current_condition_flag: int = Field(
        ...,
        ge=0,
        le=1,
        description=(
            "Quality flag for the latest row.  0 = OK, 1 = the design "
            "matrix's condition number exceeded the YAML's "
            "``condition_number_warning_threshold`` (multicollinearity "
            "detected; coefficients suppressed) OR lstsq raised "
            "numerically."
        ),
    )
    regression_window_days_used: int = Field(
        ...,
        description="Echoes the user's input central knob.",
    )
    regression_min_periods_used: int = Field(
        ...,
        description=(
            "min_periods used for the rolling fit — sourced from YAML, "
            "echoed for transparency."
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


class RollingRegressionOutput(BaseModel):
    """Top-level response for the rolling_regression tool."""

    model_config = ConfigDict(extra="forbid")

    current_metrics: RollingRegressionMetrics
    time_series_betas: List[TimeSeries] = Field(
        ...,
        description=(
            "One TimeSeries per regressor, keyed by series_name = "
            "regressor label + suffix.  units = 'ratio' (betas are "
            "unitless)."
        ),
    )
    time_series_alpha: TimeSeries = Field(
        ...,
        description=(
            "Intercept time series.  units = 'percent' (yield-percent "
            "space)."
        ),
    )
    time_series_residual: TimeSeries = Field(
        ...,
        description=(
            "Per-row residual = y[t] - alpha[t] - X[t] @ betas[t].  "
            "units = 'percent'."
        ),
    )
    time_series_r_squared: TimeSeries = Field(
        ...,
        description="In-window R² time series.  units = 'ratio'.",
    )
    time_series_condition_flag: TimeSeries = Field(
        ...,
        description=(
            "Per-row 0/1 quality flag.  units = 'count' (1 means the "
            "row was suppressed because the design matrix was near-"
            "singular).  Useful for downstream UIs to mask suspect "
            "regions of the displayed series."
        ),
    )


__all__ = [
    "RollingRegressionInput",
    "RollingRegressionMetrics",
    "RollingRegressionOutput",
]

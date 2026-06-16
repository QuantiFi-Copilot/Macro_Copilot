"""Pydantic schemas for the half_life tool.

Per the v6 sprint plan (docs/architecture/tool_architecture.md, A13):
the user-facing input surface is intentionally narrow.  The central
methodological choice IS which series to fit — surfaced through a
union of three Optional input shapes (series_spec, pair_spec,
pasted_series); a model-validator enforces "exactly one".  Every
methodology ancillary (`min_observations`, `confidence_level`,
`min_abs_beta_for_half_life`, `ffill_limit_days`, rounding) is
YAML-locked.

Transport
---------
Per Delta H of the v6 plan: half_life takes nested inputs (any of
SeriesSpec / PairSpec / PastedTimeSeries), so it ships **MCP only
this sprint**.  No FastAPI route until the UI-integration PR.

Output is unit-honest (carrying forward the v6 lesson Codex flagged
on rolling_regression / butterfly): the snapshot includes
``series_units`` (the closed enum from shared.schemas) and the
yield-/bps-/native-space scalars use the ``_native`` suffix so a
downstream consumer reads units without inferring from the spec.

No time-series output
---------------------
half_life is a pure snapshot tool — the input series IS the time-
series content.  Emitting a "rolling half-life" series would be a
sibling tool, not a parameter override of this one.  Pinned by
``test_no_time_series_output``.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import PairSpec, PastedTimeSeries, SeriesSpec, TimeSeriesUnits


class HalfLifeInput(BaseModel):
    """Parameters the LLM extracts to fit OU half-life on a series.

    Exactly one of (series_spec, pair_spec, pasted_series) must be
    supplied — enforced by ``_exactly_one_input``.  No top-level
    ``field_name``: it lives inside SeriesSpec / PairSpec; pasted
    series carry their own units.
    """

    model_config = ConfigDict(extra="forbid")

    series_spec: Optional[SeriesSpec] = Field(
        default=None,
        description=(
            "Identify a single (curve_family, tenor, field_name) "
            "sovereign yield series for the OU fit.  Resulting "
            "series_units = 'percent'.  ``field_name=None`` falls "
            "through to the YAML's default_field_name."
        ),
    )
    pair_spec: Optional[PairSpec] = Field(
        default=None,
        description=(
            "Cross-market spread shortcut: produces "
            "``(cf1_yield − cf2_yield) * 100`` in bps, matching "
            "cross_market_spread's direction convention.  Resulting "
            "series_units = 'bps'.  Both legs use the same "
            "field_name (sentinel: None falls through to YAML "
            "default)."
        ),
    )
    pasted_series: Optional[PastedTimeSeries] = Field(
        default=None,
        description=(
            "Caller-supplied series — useful for chaining the "
            "output of a prior tool (e.g., a residual) into this "
            "tool without round-tripping through the database.  "
            "``units`` flows through to the snapshot's "
            "``series_units`` field; rows are consumed AS-IS (no "
            "ffill applied — caller is responsible for cleaning "
            "before pasting)."
        ),
    )
    lookback_days: int = Field(
        default=1825,
        ge=252,
        le=7300,
        description=(
            "Calendar days of history fetched for the series_spec / "
            "pair_spec paths.  Default 1825 (~5 years) because OU CIs "
            "at 252 obs are wide; 1250-2520 is the typical desk range "
            "for half-life work.  Lower bound 252 mirrors the YAML's "
            "min_observations floor — any tighter and the OU "
            "primitive's controlled-error envelope fires immediately.  "
            "On the pasted_series path the caller has already chosen "
            "the window (the rows IS the input); compute() does NOT "
            "read this field for that path, but the bound here still "
            "applies at validation time so the input contract stays "
            "uniform across paths.  If a caller passes "
            "pasted_series with fewer than min_observations rows, the "
            "controlled-error envelope from the OU primitive fires "
            "with a clear message naming both numbers."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view.  "
                     "Applies to the series_spec / pair_spec DB-backed paths only; "
                     "the pasted_series path carries the caller's own dates and "
                     "ignores this field."),
    )

    @model_validator(mode="after")
    def _exactly_one_input(self) -> "HalfLifeInput":
        """Reject zero or two-or-more input shapes — only ONE of
        series_spec / pair_spec / pasted_series may be supplied."""
        provided = sum(
            x is not None for x in (
                self.series_spec, self.pair_spec, self.pasted_series,
            )
        )
        if provided != 1:
            raise ValueError(
                f"exactly one of series_spec, pair_spec, pasted_series "
                f"must be provided; got {provided}."
            )
        return self


class HalfLifeMetrics(BaseModel):
    """Snapshot metrics for an OU half-life fit.

    Field naming uses the ``_native`` suffix on quantities whose
    units come from the input (yield-percent for series_spec, bps
    for pair_spec, caller-declared for pasted_series).  ``series_units``
    declares what those native units are so a downstream consumer
    doesn't need to inspect the input.
    """

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    series_label: str = Field(
        ...,
        description=(
            "Canonical label for the fitted series — e.g. "
            "'UST_10Y' (series_spec), 'IT_BTP-DE_BUND_10Y' "
            "(pair_spec), or the ``series_name`` field of a "
            "pasted_series."
        ),
    )
    series_units: TimeSeriesUnits = Field(
        ...,
        description=(
            "Closed enum of the native units of the input series.  "
            "PERCENT for series_spec; BPS for pair_spec; pasted_"
            "series's declared units for that path.  Downstream "
            "consumers that render long_run_mean_native / "
            "current_value_native / current_deviation_native MUST "
            "read this field to know the unit."
        ),
    )
    point_estimate_mean_reverting: bool = Field(
        ...,
        description=(
            "POINT-ESTIMATE structural sign: ``β < 0``.  This is the "
            "SIGN of the OLS estimate, NOT a significance verdict — a "
            "pure random walk reads True ~96% of the time from "
            "finite-sample bias.  Read ``unit_root_rejected`` / "
            "``unit_root_pvalue`` for the honest statistical verdict "
            "(renamed from is_mean_reverting per the M2 over-claim fix)."
        ),
    )
    unit_root_rejected: Optional[bool] = Field(
        None,
        description=(
            "Whether the Dickey–Fuller unit-root null (β = 0, a random "
            "walk with NO mean reversion) is REJECTED at 5%.  This — "
            "NOT point_estimate_mean_reverting — is whether the data "
            "statistically supports mean reversion.  False/None means "
            "the half-life is NOT statistically significant and must "
            "not be read as a confident mean-reversion finding.  None "
            "when the β standard error is undefined (singular design)."
        ),
    )
    unit_root_pvalue: Optional[float] = Field(
        None,
        description=(
            "MacKinnon one-sided p-value for the Dickey–Fuller "
            "unit-root null.  Low (< 0.05) ⇒ reject the unit root ⇒ "
            "genuine, significant mean reversion.  High ⇒ the series "
            "is statistically indistinguishable from a random walk and "
            "the half-life is spurious.  None when undefined."
        ),
    )
    half_life_days: Optional[float] = Field(
        None,
        description=(
            "Half-life in trading-day units, rounded per "
            "``half_life_round_decimals``.  None when "
            "point_estimate_mean_reverting=False, when β <= -1 "
            "(oscillating divergence — formula undefined), or when |β| "
            "is below ``min_abs_beta_for_half_life`` (numerical "
            "instability).  WARNING: a non-None half-life with "
            "unit_root_rejected=False is statistically a random walk — "
            "read unit_root_rejected before trusting it."
        ),
    )
    half_life_ci_lower_days: Optional[float] = Field(
        None,
        description=(
            "Two-sided delta-method CI lower bound (trading days).  "
            "None when half_life_days is None or β std-error is "
            "undefined."
        ),
    )
    half_life_ci_upper_days: Optional[float] = Field(
        None,
        description="Two-sided delta-method CI upper bound (trading days).",
    )
    long_run_mean_native: Optional[float] = Field(
        None,
        description=(
            "OU long-run mean = ``-α / β`` in series_units.  None "
            "when half-life is None."
        ),
    )
    current_value_native: float = Field(
        ...,
        description=(
            "Latest observation of the fitted series in series_units.  "
            "Always defined (the series is non-empty by precondition)."
        ),
    )
    current_deviation_native: Optional[float] = Field(
        None,
        description=(
            "``current_value_native − long_run_mean_native``.  Sign "
            "interpretation depends on series_units; the consumer's "
            "domain knowledge interprets 'too rich' / 'too cheap' "
            "from the sign and the spread / level direction."
        ),
    )
    beta: float = Field(
        ...,
        description=(
            "OLS β coefficient on the AR(1) form ``Δx = α + β · "
            "x_{t-1} + ε``.  Unitless drift coefficient; rounded per "
            "``ou_beta_round_decimals`` (the OU-specific rounding "
            "convention — distinct from the regression-family "
            "``beta_round_decimals`` because OU drift β values are "
            "much smaller in magnitude).  β < 0 is the point-estimate "
            "sign of mean reversion; read unit_root_rejected for "
            "whether it is statistically significant."
        ),
    )
    beta_ci_lower: Optional[float] = Field(
        None,
        description=(
            "Two-sided CI lower bound on β.  None when (X'X) is "
            "singular (zero-variance regressor)."
        ),
    )
    beta_ci_upper: Optional[float] = Field(
        None,
        description="Two-sided CI upper bound on β.",
    )
    r_squared: Optional[float] = Field(
        None,
        description=(
            "In-sample R² of the OU fit.  None when SS_tot == 0 "
            "(degenerate target)."
        ),
    )
    observation_count: int = Field(
        ...,
        ge=1,
        description=(
            "Number of observations used in the fit (after dropna)."
        ),
    )
    confidence_level_used: float = Field(
        ...,
        description=(
            "Two-sided confidence level used for both β and "
            "half-life CIs — sourced from YAML, echoed for "
            "transparency."
        ),
    )


class HalfLifeOutput(BaseModel):
    """Top-level response for the half_life tool."""

    model_config = ConfigDict(extra="forbid")

    current_metrics: HalfLifeMetrics


__all__ = [
    "HalfLifeInput",
    "HalfLifeMetrics",
    "HalfLifeOutput",
]

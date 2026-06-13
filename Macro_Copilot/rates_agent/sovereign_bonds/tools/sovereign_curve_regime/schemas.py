"""Pydantic I/O schemas for the sovereign_curve_regime primitive.

The first Bucket-2 model-state primitive: its output surfaces THE FIT
(the fitted HMM model state read back from the operator's lineage), not
just a current snapshot.

Per-query inputs only in ``SovereignCurveRegimeInput``; the feature
windows, the realized-vol convention, and the regime-naming rule live in
``config.yaml`` (not re-exposed as fields).
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries


class SovereignCurveRegimeInput(BaseModel):
    """Per-query inputs for the sovereign curve-regime fit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Sovereign curve family (e.g. 'UST', 'DE_BUND').  The level / "
            "slope / curvature / realized-vol features are all built from "
            "THIS curve's tenor yields."
        ),
    )
    short_tenor: str = Field(
        default="2Y",
        description="The short wing (the '2s' of 2s10s), e.g. '2Y'.",
    )
    belly_tenor: str = Field(
        default="5Y",
        description="The belly (the curvature pivot), e.g. '5Y'.",
    )
    long_tenor: str = Field(
        default="10Y",
        description=(
            "The long wing (the '10s' of 2s10s) AND the 'level' feature, "
            "e.g. '10Y'."
        ),
    )
    n_states: int = Field(
        default=2,
        ge=2,
        le=6,
        description=(
            "Number of HMM regimes K (default 2 — the calm/stressed read). "
            "More states resolve finer regime structure but need more "
            "history; the operator floors the fit at max(12, K*10) rows."
        ),
    )
    lookback_days: int = Field(
        default=2520,
        ge=120,
        le=10950,
        description=(
            "Calendar days of history to fit on (default ~10y of business "
            "days).  A longer window gives the HMM more regime episodes to "
            "separate; the fit is full-sample over this window."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  None (default) falls through to "
            "config.yaml's default_field_name (YLD_YTM_MID)."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_all_differ(self) -> "SovereignCurveRegimeInput":
        tenors = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(tenors)) != 3:
            raise ValueError(
                "short_tenor, belly_tenor, long_tenor must all be "
                f"different (got {tenors})."
            )
        return self


class RegimeFeatureMeans(BaseModel):
    """The natural-unit feature means of ONE fitted regime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    regime_index: int
    regime_name: str
    n_observations: int
    level_pct: Optional[float] = None
    slope_bps: Optional[float] = None
    curvature_bps: Optional[float] = None
    realized_vol_pct: Optional[float] = None


class SovereignCurveRegimeCurrentMetrics(BaseModel):
    """Snapshot of the current regime + the fitted-model state.

    The Bucket-2 "model state is a first-class output" surface: every
    field below ``current_regime_name`` is read back from the
    fit_regime_hmm operator's lineage step.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str
    curve_family: str
    current_regime_index: Optional[int]
    current_regime_name: Optional[str]
    regime_persistence: Optional[float]  # transition_matrix[current][current]
    n_states: int
    loglik: float
    n_iter: int
    converged: bool
    fit_scope: str
    feature_columns: List[str]
    n_core_rows: int
    per_regime: List[RegimeFeatureMeans]
    methodology_label: str


class SovereignCurveRegimeTimeSeriesRow(BaseModel):
    """One row of the regime time series."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    regime_index: Optional[int] = None
    regime_name: Optional[str] = None


class SovereignCurveRegimeOutput(BaseModel):
    """Top-level response for the sovereign curve-regime fit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: SovereignCurveRegimeCurrentMetrics
    time_series: List[SovereignCurveRegimeTimeSeriesRow]
    # Canonical composable output — the Series bridge lifts this into a
    # FACTOR_LEVEL (categorical) regime-label Series for the open DAG.
    time_series_regime: TimeSeries
    methodology_disclosures: List[str]


__all__ = [
    "SovereignCurveRegimeInput",
    "RegimeFeatureMeans",
    "SovereignCurveRegimeCurrentMetrics",
    "SovereignCurveRegimeTimeSeriesRow",
    "SovereignCurveRegimeOutput",
]

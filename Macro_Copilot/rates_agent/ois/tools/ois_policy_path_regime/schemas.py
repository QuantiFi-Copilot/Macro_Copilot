"""Pydantic I/O schemas for ois_policy_path_regime (Bucket-2).

Surfaces the FIT (the HMM model state read back from the operator's
lineage) + the current PRICED policy regime — not just a snapshot.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries

_STRIP_FAMILIES = ("SOFR_FUT", "SONIA_FUT", "EUR_SHORT_RATE_FUT")


class OISPolicyPathRegimeInput(BaseModel):
    """Per-query inputs for the priced policy-regime fit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: Literal["SOFR_FUT", "SONIA_FUT", "EUR_SHORT_RATE_FUT"] = Field(
        ...,
        description=(
            "The policy-futures strip family.  RFR vs IBOR are different "
            "short-rate objects — one family per fit (no mixing)."
        ),
    )
    n_states: int = Field(
        default=3,
        ge=2,
        le=6,
        description=(
            "Number of priced-regime states K (default 3 — easing / neutral "
            "/ tightening priced)."
        ),
    )
    lookback_days: int = Field(
        default=2520,
        ge=120,
        le=10950,
        description="Calendar days of strip history to fit on (~10y).",
    )


class PolicyRegimeFeatureMeans(BaseModel):
    """The natural-unit feature means of ONE priced regime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    regime_index: int
    regime_name: str
    n_observations: int
    front_level_pct: Optional[float] = None
    strip_slope_bps: Optional[float] = None
    strip_curvature_bps: Optional[float] = None
    realized_vol_pct: Optional[float] = None


class OISPolicyPathRegimeCurrentMetrics(BaseModel):
    """Snapshot of the current priced regime + the fitted HMM model state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str
    curve_family: str
    current_regime_index: Optional[int]
    current_regime_name: Optional[str]
    regime_persistence: Optional[float]
    n_states: int
    loglik: float
    n_iter: int
    converged: bool
    fit_scope: str
    feature_columns: List[str]
    n_core_rows: int
    per_regime: List[PolicyRegimeFeatureMeans]
    methodology_label: str


class OISPolicyPathRegimeTimeSeriesRow(BaseModel):
    """One row of the priced-regime time series."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    regime_index: Optional[int] = None
    regime_name: Optional[str] = None


class OISPolicyPathRegimeOutput(BaseModel):
    """Top-level response for the priced policy-regime fit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: OISPolicyPathRegimeCurrentMetrics
    time_series: List[OISPolicyPathRegimeTimeSeriesRow]
    time_series_regime: TimeSeries
    methodology_disclosures: List[str]


__all__ = [
    "OISPolicyPathRegimeInput",
    "PolicyRegimeFeatureMeans",
    "OISPolicyPathRegimeCurrentMetrics",
    "OISPolicyPathRegimeTimeSeriesRow",
    "OISPolicyPathRegimeOutput",
]

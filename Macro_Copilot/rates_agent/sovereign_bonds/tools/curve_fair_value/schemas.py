"""Pydantic I/O schemas for the curve_fair_value primitive (Bucket-2).

Its output surfaces the FIT (the PCA model state read back from the
operator's lineage) + the per-tenor richness ranking — not just a
snapshot.  Per-query inputs only; n_components and the field default live
in config.yaml.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries

_DEFAULT_TENORS = ["2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]


class CurveFairValueInput(BaseModel):
    """Per-query inputs for the curve PCA fair-value read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description="Sovereign curve family, e.g. 'UST', 'DE_BUND'.",
    )
    tenors: List[str] = Field(
        default_factory=lambda: list(_DEFAULT_TENORS),
        min_length=4,
        description=(
            "The curve points to fit PCA across (>= 4 distinct tenors; "
            "default 2Y/3Y/5Y/7Y/10Y/30Y).  More points give a richer "
            "fair-value read; n_components (config) must be <= len(tenors)."
        ),
    )
    focus_tenor: str = Field(
        default="10Y",
        description=(
            "Which tenor's residual history is emitted as the composable "
            "`time_series_residual` Series (must be one of `tenors`).  The "
            "snapshot ranks ALL tenors regardless."
        ),
    )
    lookback_days: int = Field(
        default=2520,
        ge=120,
        le=10950,
        description="Calendar days of history to fit the PCA over (~10y).",
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field; None (default) → config default_field_name "
            "(YLD_YTM_MID)."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @model_validator(mode="after")
    def _validate(self) -> "CurveFairValueInput":
        if len(set(self.tenors)) != len(self.tenors):
            raise ValueError(f"tenors must be distinct (got {self.tenors}).")
        if self.focus_tenor not in self.tenors:
            raise ValueError(
                f"focus_tenor {self.focus_tenor!r} must be one of tenors "
                f"{self.tenors}."
            )
        return self


class TenorRichness(BaseModel):
    """The current fair-value richness of one tenor."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tenor: str
    residual_bps: Optional[float]   # +ve = cheap, -ve = rich
    residual_zscore: Optional[float]
    verdict: Optional[str]          # 'cheap' | 'rich' | 'fair' (descriptive)


class CurveFairValueCurrentMetrics(BaseModel):
    """Snapshot of the per-tenor richness + the fitted PCA model state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str
    curve_family: str
    tenors_used: List[str]
    n_components: int
    per_tenor: List[TenorRichness]
    cheapest_tenor: Optional[str]
    cheapest_residual_bps: Optional[float]
    richest_tenor: Optional[str]
    richest_residual_bps: Optional[float]
    explained_variance_ratio: List[float]
    near_degenerate: bool
    feature_columns: List[str]
    n_complete_rows: int
    fit_scope: str
    methodology_label: str


class CurveFairValueTimeSeriesRow(BaseModel):
    """One row of one tenor's residual history (bps)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    residual_bps: Optional[float] = None


class CurveFairValueOutput(BaseModel):
    """Top-level response for the curve PCA fair-value read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: CurveFairValueCurrentMetrics
    # Per-tenor residual histories (frontend / charting).
    time_series_by_tenor: List[CurveFairValueTimeSeriesRow]
    # Composable headline: the focus tenor's residual history (bps).  The
    # Series bridge lifts this for the open DAG.
    time_series_residual: TimeSeries
    methodology_disclosures: List[str]


__all__ = [
    "CurveFairValueInput",
    "TenorRichness",
    "CurveFairValueCurrentMetrics",
    "CurveFairValueTimeSeriesRow",
    "CurveFairValueOutput",
]

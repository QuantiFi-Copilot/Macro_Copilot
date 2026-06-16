"""Pydantic I/O schemas for pca_neutral_butterfly_weights (§7-C, Bucket-1B)."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries

_DEFAULT_FIT_TENORS = ["2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]


class PcaNeutralButterflyWeightsInput(BaseModel):
    """Per-query inputs for the PCA-neutral fly weights."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(..., min_length=1, description="Sovereign curve family.")
    short_tenor: str = Field(default="2Y", description="The short wing.")
    belly_tenor: str = Field(default="5Y", description="The belly (body).")
    long_tenor: str = Field(default="10Y", description="The long wing.")
    fit_tenors: List[str] = Field(
        default_factory=lambda: list(_DEFAULT_FIT_TENORS),
        min_length=3,
        description=(
            "The broad tenor set the PCA is fit on (>= 3).  The three fly "
            "tenors are auto-unioned in.  Default 2Y/3Y/5Y/7Y/10Y/30Y."
        ),
    )
    n_pcs_to_neutralize: int = Field(
        default=2,
        ge=1,
        le=2,
        description=(
            "Number of principal components to neutralize: 2 (default) zeroes "
            "PC1+PC2; 1 zeroes PC1 with a cash-neutral wing normalization."
        ),
    )
    lookback_days: int = Field(
        default=2520, ge=120, le=10950,
        description="Calendar days of history to fit the PCA over (~10y).",
    )
    field_name: Optional[str] = Field(
        default=None, description="Bloomberg field; None → config default.")

    @model_validator(mode="after")
    def _validate(self) -> "PcaNeutralButterflyWeightsInput":
        fly = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(fly)) != 3:
            raise ValueError(f"the three fly tenors must be distinct (got {fly}).")
        return self


class PcResidualExposure(BaseModel):
    """The fly's net loading on one principal component (≈0 if neutralized)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    pc: int                     # 1-indexed
    neutralized: bool
    fly_loading: Optional[float]
    explained_variance_ratio: Optional[float]


class PcaNeutralButterflyWeightsCurrentMetrics(BaseModel):
    """Snapshot of the solved PCA-neutral fly weights + diagnostics."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str
    curve_family: str
    short_tenor: str
    belly_tenor: str
    long_tenor: str
    belly_weight: float
    # short_weight / long_weight are assigned via _round (Optional[float]) —
    # the det-guard (abs(det(A)) < 1e-12 -> error envelope) makes the solve
    # finite in practice, but the annotation matches the _round provenance
    # and current_fly_bps for type honesty (m40 / PR14).
    short_weight: Optional[float]
    long_weight: Optional[float]
    n_pcs_neutralized: int
    residual_pc_exposures: List[PcResidualExposure]
    current_fly_bps: Optional[float]
    fit_tenors: List[str]
    n_complete_rows: int
    near_degenerate: bool


class PcaNeutralButterflyWeightsTimeSeriesRow(BaseModel):
    """One row of the PCA-neutral fly history (bps)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    neutral_fly_bps: Optional[float] = None


class PcaNeutralButterflyWeightsOutput(BaseModel):
    """Top-level response for the PCA-neutral fly weights."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: PcaNeutralButterflyWeightsCurrentMetrics
    time_series: List[PcaNeutralButterflyWeightsTimeSeriesRow]
    # Composable headline: the PCA-neutral fly history (bps).
    time_series_neutral_fly: TimeSeries
    methodology_disclosures: List[str]


__all__ = [
    "PcaNeutralButterflyWeightsInput",
    "PcResidualExposure",
    "PcaNeutralButterflyWeightsCurrentMetrics",
    "PcaNeutralButterflyWeightsTimeSeriesRow",
    "PcaNeutralButterflyWeightsOutput",
]

"""Pydantic I/O schemas for the rates_vol_regime primitive (Bucket-2).

Surfaces the FIT (the GARCH model state read back from the operator's
lineage) + the current vol-regime classification — not just a snapshot.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class RatesVolRegimeInput(BaseModel):
    """Per-query inputs for the GARCH vol-regime read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description="Sovereign curve family, e.g. 'UST', 'DE_BUND'.",
    )
    tenor: str = Field(
        default="10Y",
        description="The tenor whose yield-change volatility to model.",
    )
    lookback_days: int = Field(
        default=2520,
        ge=200,
        le=10950,
        description="Calendar days of history to fit the GARCH over (~10y).",
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


class RatesVolRegimeCurrentMetrics(BaseModel):
    """Snapshot of the current vol state + the fitted GARCH model state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str
    curve_family: str
    tenor: str
    current_conditional_vol_daily_bps: float
    current_conditional_vol_annualized_bps: float
    vol_percentile: float           # 0–100, where today's vol sits in history
    regime_label: str               # calm | normal | elevated
    # The Bucket-2 GARCH model state (read from fit_garch's lineage):
    persistence: float              # alpha + beta
    omega: float
    alpha: float
    beta: float
    mu: float
    loglik: float
    near_integrated: bool           # sticky / near-unit-root vol
    is_vol_mean_reverting: bool     # persistence < 1
    fit_scope: str
    n_core_rows: int
    methodology_label: str


class RatesVolRegimeTimeSeriesRow(BaseModel):
    """One row of the conditional-vol history (daily bps)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    conditional_vol_bps: Optional[float] = None


class RatesVolRegimeOutput(BaseModel):
    """Top-level response for the GARCH vol-regime read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: RatesVolRegimeCurrentMetrics
    time_series: list[RatesVolRegimeTimeSeriesRow]
    # Composable: the conditional-vol history (daily bps).
    time_series_conditional_vol: TimeSeries
    methodology_disclosures: list[str]


__all__ = [
    "RatesVolRegimeInput",
    "RatesVolRegimeCurrentMetrics",
    "RatesVolRegimeTimeSeriesRow",
    "RatesVolRegimeOutput",
]

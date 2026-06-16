"""Pydantic schemas for the breakeven_inflation tool.

Phase 1 PR 19.

TIPS-vs-Nominal analog to swap_spread.  Both legs are sovereign-
family instruments so the schema deliberately mirrors
``CurveSpreadInput`` shape (curve-family on each side + matched
tenor) rather than the cross-domain swap_spread shape.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas import TimeSeries


BreakevenConvention = Literal["nominal_breakeven", "inflation_swap_breakeven"]


_NOMINAL_CURVE_FAMILIES = frozenset({
    "UST",       # paired with USD_TIPS — the canonical V1 pair
    "UK_GILT",   # paired with UK_LINKER (future ingestion)
    "DE_BUND",   # paired with DE_BUND_LINKER (future ingestion)
    "JGB",       # paired with JPY_LINKER (future ingestion)
})

_REAL_CURVE_FAMILIES = frozenset({
    "USD_TIPS",
    "UK_LINKER",  # placeholders for the V2 cross-currency support
    "DE_BUND_LINKER",
    "JPY_LINKER",
})

# Each nominal family pairs with exactly one real family.  Cross-pair
# requests (e.g. UST nominal + UK_LINKER real) are rejected because
# the breakeven sign / scale would be cross-currency, not cross-
# instrument — distinct economics.
_VALID_PAIRS = frozenset({
    ("UST", "USD_TIPS"),
    ("UK_GILT", "UK_LINKER"),
    ("DE_BUND", "DE_BUND_LINKER"),
    ("JGB", "JPY_LINKER"),
})


class BreakevenInflationInput(BaseModel):
    """Parameters the LLM extracts for a breakeven_inflation calculation."""

    model_config = ConfigDict(extra="forbid")

    nominal_curve_family: str = Field(
        ...,
        description=(
            "Sovereign nominal curve family.  Must be one of: "
            f"{sorted(_NOMINAL_CURVE_FAMILIES)}."
        ),
    )
    real_curve_family: str = Field(
        ...,
        description=(
            "Sovereign real-yield (inflation-linker) curve family.  "
            f"Must be one of: {sorted(_REAL_CURVE_FAMILIES)}.  Must "
            "pair with the nominal_curve_family (e.g. UST + USD_TIPS)."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor for the matched-tenor breakeven (e.g. '10Y').  "
            "Same tenor used on both nominal and real legs."
        ),
    )
    convention: Optional[BreakevenConvention] = Field(
        default=None,
        description=(
            "Breakeven computation method.  None → resolved from "
            "config.yaml (``nominal_breakeven``).  V1 implements "
            "nominal_breakeven only; ``inflation_swap_breakeven`` "
            "raises NotImplementedError."
        ),
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Calendar-day window of displayed time-series history.  "
            "The z-score rolling window is fixed by the config.yaml "
            "convention regardless of this value."
        ),
    )
    nominal_field_name: str = Field(
        default="YLD_YTM_MID",
        description="Bloomberg field for the nominal leg.",
    )
    real_field_name: str = Field(
        default="YLD_YTM_MID",
        description="Bloomberg field for the real leg.",
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @model_validator(mode="after")
    def _validate_curve_pair(self) -> "BreakevenInflationInput":
        if self.nominal_curve_family not in _NOMINAL_CURVE_FAMILIES:
            raise ValueError(
                f"nominal_curve_family={self.nominal_curve_family!r} "
                f"is not a recognised nominal family.  Allowed: "
                f"{sorted(_NOMINAL_CURVE_FAMILIES)}."
            )
        if self.real_curve_family not in _REAL_CURVE_FAMILIES:
            raise ValueError(
                f"real_curve_family={self.real_curve_family!r} is not "
                f"a recognised real family.  Allowed: "
                f"{sorted(_REAL_CURVE_FAMILIES)}."
            )
        pair = (self.nominal_curve_family, self.real_curve_family)
        if pair not in _VALID_PAIRS:
            raise ValueError(
                f"Pair ({self.nominal_curve_family}, "
                f"{self.real_curve_family}) is not a valid currency-"
                "matched breakeven pair.  Allowed pairs: "
                f"{sorted(_VALID_PAIRS)}."
            )
        return self


class BreakevenInflationCurrentMetrics(BaseModel):
    """Snapshot at the latest observation."""

    as_of_date: str
    nominal_curve_family: str
    real_curve_family: str
    tenor: str
    current_breakeven_bps: float
    daily_change_bps: Optional[float] = None
    current_z_score: Optional[float] = None
    rolling_window_days: int
    nominal_yield_pct: Optional[float] = None
    real_yield_pct: Optional[float] = None


class BreakevenInflationTimeSeriesRow(BaseModel):
    """Single wire-frozen row for the bespoke time-series array."""

    date: str
    breakeven_bps: float
    z_score: Optional[float] = None


class BreakevenInflationOutput(BaseModel):
    """Top-level response.

    Carries the bespoke wire-frozen array (frontend backward-compat)
    AND two canonical TimeSeries fields (BPS + Z_SCORE) matching
    the v6 sovereign-primitive convention.
    """

    current_metrics: BreakevenInflationCurrentMetrics
    time_series: List[BreakevenInflationTimeSeriesRow]
    time_series_breakeven: TimeSeries = Field(
        ...,
        description=(
            "Historical breakeven series (nominal − real, in BPS).  "
            "Closed-enum TimeSeriesUnits.BPS."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Rolling z-score of the breakeven vs its own trailing "
            "window.  Closed-enum TimeSeriesUnits.Z_SCORE."
        ),
    )
    methodology_disclosures: List[str] = Field(
        default_factory=list,
        description=(
            "Methodology disclosure surfaced on the workspace card.  "
            "V1 includes 'nominal − real breakeven; inflation-swap "
            "breakeven (with seasonal CPI adjustments) requires "
            "data not yet ingested'."
        ),
    )


__all__ = [
    "BreakevenConvention",
    "BreakevenInflationInput",
    "BreakevenInflationCurrentMetrics",
    "BreakevenInflationTimeSeriesRow",
    "BreakevenInflationOutput",
]

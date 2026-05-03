"""Pydantic schemas for the curve_spread tool.

Canonical (and only) location for these models.  The legacy
``rates_agent.sovereign_bonds.tools.schemas.spread`` re-export shim
that briefly preserved the old import path was removed in commit 5
of the tool-config pilot.

Callers reach these classes via either:
  - ``from rates_agent.sovereign_bonds.tools.curve_spread import CurveSpreadInput``
    (direct re-export from the package init), or
  - ``from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput``
    (the sovereign-bonds re-export hub, which now imports from this
    file rather than from the deleted shim).

Conventions like ``z_score_window_days`` and ``ffill_limit_days`` live
in ``config.yaml`` (alongside this file), not on the Pydantic input.
The Pydantic input only carries fields the LLM picks per query —
``curve_family``, ``short_tenor``, ``long_tenor``, ``lookback_days``,
``field_name``.  Validators that encode an invariant (``short_tenor !=
long_tenor``) stay here in code; they are not configurable.

Canonical TimeSeries output
---------------------------
Per the legacy-sovereign TimeSeries tech-debt cleanup: this tool also
emits a ``canonical_time_series: List[TimeSeries]`` field using the
closed-enum ``shared.schemas.time_series.TimeSeries`` shape.  The
existing ``time_series: List[CurveSpreadTimeSeriesRow]`` field stays
for frontend backward-compat (``RatesView.tsx`` reads it directly);
the canonical field is what the upcoming primitive-to-operator
bridge (Phase 1B) consumes.  Both fields are computed from the same
underlying spread series — they cannot drift.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


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
        default=365, ge=30, le=7300,
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
    spread_label: str = Field(..., description="Human-readable label, e.g. '2s10s'.")
    current_spread_bps: float
    daily_change_bps: Optional[float] = Field(None, description="1-day change; None if fewer than 2 observations.")
    current_z_score: Optional[float] = Field(None, description="Rolling z-score of the spread vs its own trailing window.")
    rolling_window_days: int = Field(..., description="Window used for the z-score calculation.")
    short_tenor_yield: Optional[float] = Field(None, description="Latest yield on the short leg (percent).")
    long_tenor_yield: Optional[float] = Field(None, description="Latest yield on the long leg (percent).")


class CurveSpreadTimeSeriesRow(BaseModel):
    """Single row in the spread time-series array."""
    date: str
    spread_bps: float
    z_score: Optional[float] = None


class CurveSpreadOutput(BaseModel):
    """Top-level response the MCP server returns to the orchestrator.

    ``time_series`` is the wire-frozen bespoke shape
    (``CurveSpreadTimeSeriesRow``) the frontend has consumed since
    this tool shipped.  ``canonical_time_series`` was added by the
    legacy-TimeSeries tech-debt cleanup to give the upcoming
    primitive-to-operator bridge a uniform closed-enum shape to
    consume.  Both are computed from the same underlying display
    DataFrame — they cannot drift.
    """

    current_metrics: CurveSpreadCurrentMetrics
    time_series: List[CurveSpreadTimeSeriesRow]
    canonical_time_series: List[TimeSeries] = Field(
        default_factory=list,
        description=(
            "Historical curve spread (long_tenor − short_tenor, in BPS) "
            "over the displayed window.  Uses the canonical "
            "``shared.schemas.time_series.TimeSeries`` shape with "
            "closed-enum units (BPS).  One series:\n"
            "  - units = BPS\n"
            "  - series_name = "
            "    '<curve_family_lower>_<short>_<long>_spread'\n"
            "  - values match ``time_series[i].spread_bps`` 1-to-1 "
            "    by construction."
        ),
    )


__all__ = [
    "CurveSpreadInput",
    "CurveSpreadCurrentMetrics",
    "CurveSpreadTimeSeriesRow",
    "CurveSpreadOutput",
]

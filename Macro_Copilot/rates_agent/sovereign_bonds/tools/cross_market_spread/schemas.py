"""Pydantic schemas for the cross-market spread tool.

Migrated from ``rates_agent/sovereign_bonds/tools/schemas/cross_market.py``
into the per-tool-folder pattern (docs/architecture/tool_architecture.md).

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_field_name`` convention".  Callers can still
  override per-query.  ``compute()`` is the one place that resolves
  the sentinel against the active config, so editing
  ``default_field_name`` in YAML actually changes runtime behaviour.
  Mirrors the pattern established by curve_move_classifier (commit
  9f741ea), tightened by commit b2605ee, and applied uniformly to
  yield_levels and butterfly.

- ``lookback_days`` semantics are unchanged from the legacy tool: it
  controls the *displayed* window only.  The rolling z-score window
  and the trailing range window are independent conventions in
  config.yaml.

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  for frontend backward-compat (CrossMarketView.tsx reads them by
  name).  See config.yaml's ``planned_extensions`` for the path to
  making the trailing window configurable.

Validators that encode invariants stay here in code: the
``_curves_must_differ`` validator is a structural input check, not a
methodology choice, so it is NOT a config knob.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


class CrossMarketSpreadInput(BaseModel):
    """Parameters for computing the yield differential between the same
    tenor on two different sovereign curves (e.g. UST 10Y − BUND 10Y).

    spread = (cf1_yield − cf2_yield) × 100  (bps)
    """

    curve_family_1: str = Field(
        ...,
        description=(
            "The first (numerator) curve family.  The spread is computed "
            "as curve_family_1 − curve_family_2.  Examples: 'UST', "
            "'DE_BUND', 'IT_BTP', 'FR_OAT', 'ES_BONO', 'UK_GILT', 'JGB'."
        ),
    )
    curve_family_2: str = Field(
        ...,
        description="The second (denominator) curve family.",
    )
    tenor: str = Field(
        ...,
        description=(
            "The tenor point to compare across markets, e.g. '2Y', "
            "'5Y', '10Y', '30Y'."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of *displayed* history.  Does NOT control "
            "the rolling z-score window (config: z_score_window_days, "
            "currently 252) or the trailing range window (config: "
            "trailing_range_window_days, locked at 252 in V1)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None (default), the "
            "tool falls through to ``default_field_name`` from "
            "config.yaml (currently 'YLD_YTM_MID').  Pass an explicit "
            "field name to override per query.  LLM/HTTP wrappers MUST "
            "translate their wire-level sentinel (empty string for MCP, "
            "missing param for FastAPI) to None before constructing "
            "this input — otherwise the YAML default is silently "
            "shadowed."
        ),
    )
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @model_validator(mode="after")
    def _curves_must_differ(self) -> "CrossMarketSpreadInput":
        if self.curve_family_1 == self.curve_family_2:
            raise ValueError(
                f"curve_family_1 and curve_family_2 must be different, but "
                f"both are '{self.curve_family_1}'.  For same-curve "
                "spreads, use the curve_spread tool instead."
            )
        return self


class CrossMarketSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics for a cross-market yield differential."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family_1: str
    curve_family_2: str
    tenor: str
    spread_label: str = Field(
        ..., description="Human-readable label, e.g. 'IT_BTP-DE_BUND 10Y'."
    )
    current_spread_bps: float = Field(
        ..., description="Current yield differential in basis points."
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in the spread (bps)."
    )
    weekly_change_bps: Optional[float] = Field(
        None, description="5-trading-day change in the spread (bps)."
    )
    monthly_change_bps: Optional[float] = Field(
        None, description="22-trading-day change in the spread (bps)."
    )
    current_z_score: Optional[float] = Field(
        None, description="Rolling 252-day z-score of the spread."
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation."
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description="Highest spread over trailing 252 trading days (bps).",
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description="Lowest spread over trailing 252 trading days (bps).",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    curve_family_1_yield: Optional[float] = Field(
        None, description="Latest yield on curve_family_1 (percent)."
    )
    curve_family_2_yield: Optional[float] = Field(
        None, description="Latest yield on curve_family_2 (percent)."
    )


class CrossMarketSpreadTimeSeriesRow(BaseModel):
    """Single row in the cross-market spread time-series."""

    date: str
    spread_bps: float
    z_score: Optional[float] = None


class CrossMarketSpreadOutput(BaseModel):
    """Top-level response for the cross-market spread tool.

    ``time_series`` is the wire-frozen bespoke shape
    (``CrossMarketSpreadTimeSeriesRow``) the frontend has consumed
    since this tool shipped.  ``time_series_spread`` and
    ``time_series_zscore`` were added by the legacy-TimeSeries tech-
    debt cleanup so the upcoming primitive-to-operator bridge has
    uniform closed-enum shapes to consume.  All three are computed
    from the same underlying display DataFrame — they cannot drift.
    """

    current_metrics: CrossMarketSpreadCurrentMetrics
    time_series: List[CrossMarketSpreadTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical cross-market spread "
            "(curve_family_1 − curve_family_2 yield, in BPS) over the "
            "displayed window.  Closed-enum ``TimeSeriesUnits.BPS``; "
            "series_name = '<cf1_lower>_<cf2_lower>_<tenor_lower>_spread'.  "
            "Values match ``time_series[i].spread_bps`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the cross-market spread vs "
            "its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name = "
            "'<cf1_lower>_<cf2_lower>_<tenor_lower>_zscore'.  Values "
            "match ``time_series[i].z_score`` 1-to-1 (None for rows in "
            "the rolling-window warmup)."
        ),
    )


__all__ = [
    "CrossMarketSpreadInput",
    "CrossMarketSpreadCurrentMetrics",
    "CrossMarketSpreadTimeSeriesRow",
    "CrossMarketSpreadOutput",
]

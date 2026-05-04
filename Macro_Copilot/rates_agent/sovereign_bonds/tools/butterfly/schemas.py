"""Pydantic schemas for the butterfly (curvature) tool.

Migrated from ``rates_agent/sovereign_bonds/tools/schemas/butterfly.py``
into the per-tool-folder pattern established by the tool-config refactor
pilot (docs/architecture/tool_architecture.md).

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_field_name`` convention".  Callers can still
  override per-query.  ``compute()`` is the one place that resolves
  the sentinel against the active config, so editing
  ``default_field_name`` in YAML actually changes runtime behaviour.
  Mirrors the pattern established by curve_move_classifier (commit
  9f741ea) and tightened by commit b2605ee.

- ``lookback_days`` semantics are unchanged from the legacy tool: it
  controls the *displayed* window only.  The rolling z-score window
  and the trailing range window are independent conventions in
  config.yaml.

- ``high_252d_bps`` / ``low_252d_bps`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  for frontend backward-compat (ButterflyView.tsx reads them by
  name).  See config.yaml's ``planned_extensions`` for the path to
  making the trailing window configurable.

Validators that encode invariants stay here in code: the
``_tenors_must_all_differ`` validator is a structural input check, not
a methodology choice, so it is NOT a config knob.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


class ButterflyInput(BaseModel):
    """Parameters for computing the 3-point butterfly (curvature) on a
    single sovereign curve.

    butterfly = (2 × belly − short − long) × 100  (bps)
    """

    curve_family: str = Field(
        ...,
        description=(
            "Curve family identifier.  Examples: 'UST', 'DE_BUND', "
            "'UK_GILT', 'JGB', 'FR_OAT', 'IT_BTP', 'ES_BONO'."
        ),
    )
    short_tenor: str = Field(..., description="The short wing, e.g. '2Y'.")
    belly_tenor: str = Field(
        ...,
        description="The belly (body) of the butterfly, e.g. '5Y'.",
    )
    long_tenor: str = Field(..., description="The long wing, e.g. '10Y'.")
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

    @model_validator(mode="after")
    def _tenors_must_all_differ(self) -> "ButterflyInput":
        tenors = [self.short_tenor, self.belly_tenor, self.long_tenor]
        if len(set(tenors)) != 3:
            raise ValueError(
                f"short_tenor, belly_tenor, and long_tenor must all be "
                f"different, but got {tenors}."
            )
        return self


class ButterflyCurrentMetrics(BaseModel):
    """Snapshot metrics for a 3-point butterfly."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    butterfly_label: str = Field(
        ..., description="Human-readable label, e.g. '2s5s10s'."
    )
    current_butterfly_bps: float = Field(
        ...,
        description=(
            "Current butterfly in bps.  Positive = belly is cheap, "
            "negative = belly is rich."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in the butterfly (bps)."
    )
    current_z_score: Optional[float] = Field(
        None, description="Rolling 252-day z-score of the butterfly."
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation."
    )
    high_252d_bps: Optional[float] = Field(
        None,
        description="Highest butterfly over trailing 252 trading days (bps).",
    )
    low_252d_bps: Optional[float] = Field(
        None,
        description="Lowest butterfly over trailing 252 trading days (bps).",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    wing_short_bps: Optional[float] = Field(
        None, description="Component spread: belly − short (bps)."
    )
    wing_long_bps: Optional[float] = Field(
        None, description="Component spread: long − belly (bps)."
    )
    short_tenor_yield: Optional[float] = Field(
        None, description="Latest yield on the short wing (percent)."
    )
    belly_tenor_yield: Optional[float] = Field(
        None, description="Latest yield on the belly (percent)."
    )
    long_tenor_yield: Optional[float] = Field(
        None, description="Latest yield on the long wing (percent)."
    )


class ButterflyTimeSeriesRow(BaseModel):
    """Single row in the butterfly time-series."""

    date: str
    butterfly_bps: float
    z_score: Optional[float] = None


class ButterflyOutput(BaseModel):
    """Top-level response for the butterfly tool.

    ``time_series`` is the wire-frozen bespoke shape
    (``ButterflyTimeSeriesRow``) the frontend has consumed since this
    tool shipped.  ``time_series_butterfly`` and ``time_series_zscore``
    were added by the legacy-TimeSeries tech-debt cleanup so the
    upcoming primitive-to-operator bridge has uniform closed-enum
    shapes to consume.  All three are computed from the same
    underlying display DataFrame — they cannot drift.
    """

    current_metrics: ButterflyCurrentMetrics
    time_series: List[ButterflyTimeSeriesRow]
    time_series_butterfly: TimeSeries = Field(
        ...,
        description=(
            "Historical butterfly value "
            "(long_wing − 2*belly + short_wing, in BPS) over the "
            "displayed window.  Closed-enum ``TimeSeriesUnits.BPS``; "
            "series_name = "
            "'<curve_family_lower>_<short>_<belly>_<long>_butterfly'.  "
            "Values match ``time_series[i].butterfly_bps`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the butterfly vs its own "
            "trailing window.  Closed-enum ``TimeSeriesUnits.Z_SCORE``; "
            "series_name = "
            "'<curve_family_lower>_<short>_<belly>_<long>_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None for "
            "rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "ButterflyInput",
    "ButterflyCurrentMetrics",
    "ButterflyTimeSeriesRow",
    "ButterflyOutput",
]

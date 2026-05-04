"""Pydantic schemas for the OIS curve-spread tool.

Migrated from ``rates_agent/ois/tools/schemas/spread.py`` into the
per-tool-folder pattern established by the tool-config refactor pilot
(see ``docs/architecture/tool_architecture.md``) and used by every
sovereign tool + OIS rate_level.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_swap_rate_field`` convention".  Callers can
  still override per-query.  ``compute()`` is the one place that
  resolves the sentinel against the active config, so editing
  ``default_swap_rate_field`` in YAML actually changes runtime
  behaviour.  Mirrors the pattern established by sovereign
  curve_move_classifier (commit 9f741ea / b2605ee) and OIS
  rate_level.

- ``lookback_days`` controls the displayed history in the
  ``time_series`` output AND the cutoff for the snapshot.  It does
  NOT control the rolling z-score window (fixed by
  ``z_score_window_days``, currently 252).

Validators that encode invariants stay here in code: ``short_tenor !=
long_tenor`` is mathematical truth, not configuration, and lives as a
``@model_validator``.

Canonical TimeSeries output
---------------------------
Per the legacy-sovereign TimeSeries tech-debt cleanup pattern: this
tool emits TWO canonical ``shared.schemas.time_series.TimeSeries``
fields matching the v6 sovereign-primitive naming convention
(``time_series_<purpose>: TimeSeries``):

  - ``time_series_spread: TimeSeries`` (units = BPS) — the historical
    spread values (long_tenor − short_tenor in bps).
  - ``time_series_zscore: TimeSeries`` (units = Z_SCORE) — the rolling
    z-score of the spread vs its own trailing window.

The wire-frozen ``time_series: List[OISCurveSpreadTimeSeriesRow]``
field stays for backward-compat with the legacy single-file tool's
output shape; the canonical fields are what the upcoming
primitive-to-operator bridge (Phase 1B) consumes.  All three series
are computed from the same underlying display DataFrame and cannot
drift — proven by point-by-point parity tests.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


class OISCurveSpreadInput(BaseModel):
    """Parameters the LLM must extract from the user to run an OIS
    curve-spread calculation.  Every field maps directly to a filter
    column on ``macro_data.v_market_data_daily_enriched``."""

    curve_family: str = Field(
        ...,
        description=(
            "OIS curve family identifier as stored in instrument_master. "
            "Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', 'GBP_SONIA_OIS', "
            "'JPY_OIS', 'AUD_OIS', 'CAD_OIS'."
        ),
    )
    short_tenor: str = Field(
        ...,
        description=(
            "Short leg of the spread.  OIS curves have a dense short-end "
            "grid: '1W', '1M', '2M', '3M', '6M', '9M', '1Y', '2Y', '3Y'."
        ),
    )
    long_tenor: str = Field(
        ...,
        description=(
            "Long leg of the spread.  Examples: '2Y', '5Y', '10Y', '20Y', '30Y'."
        ),
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Number of calendar days of displayed history in the "
            "time_series output.  Defaults to 365 (1 year).  The z-score "
            "rolling window is always a fixed 252 trading days regardless "
            "of this value (fixed by config convention "
            "z_score_window_days)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field.  When None (default), the "
            "tool falls through to ``default_swap_rate_field`` from "
            "config.yaml (currently 'PX_LAST').  Pass an explicit "
            "field name to override per query.  LLM/HTTP wrappers MUST "
            "translate their wire-level sentinel (empty string for "
            "MCP, missing param for FastAPI) to None before "
            "constructing this input — otherwise the YAML default is "
            "silently shadowed.  See the curve_move_classifier "
            "wrapper-shadowing fix (commit b2605ee) for the canonical "
            "pattern."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_differ(self) -> "OISCurveSpreadInput":
        if self.short_tenor == self.long_tenor:
            raise ValueError(
                f"short_tenor and long_tenor must be different, "
                f"but both are '{self.short_tenor}'."
            )
        return self


class OISCurveSpreadCurrentMetrics(BaseModel):
    """Snapshot metrics returned for the most recent trade date.

    Field names ``short_tenor_rate`` / ``long_tenor_rate`` use "rate"
    rather than "yield" because OIS quotes are par swap rates, not
    bond yields.  Wire-frozen for backward-compat with the legacy
    single-file OIS curve_spread tool.
    """
    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    spread_label: str = Field(..., description="Human-readable label, e.g. '2s10s' or '3M/2Y'.")
    current_spread_bps: float
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change; None if fewer than 2 observations.",
    )
    current_z_score: Optional[float] = Field(
        None, description="Rolling z-score of the spread vs its own trailing window.",
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    short_tenor_rate: Optional[float] = Field(
        None, description="Latest par swap rate on the short leg (percent).",
    )
    long_tenor_rate: Optional[float] = Field(
        None, description="Latest par swap rate on the long leg (percent).",
    )


class OISCurveSpreadTimeSeriesRow(BaseModel):
    """Single row in the bespoke wire-frozen OIS spread time-series array."""
    date: str
    spread_bps: float
    z_score: Optional[float] = None


class OISCurveSpreadOutput(BaseModel):
    """Top-level response the MCP server returns to the orchestrator.

    ``time_series`` is the wire-frozen bespoke shape
    (``OISCurveSpreadTimeSeriesRow``) preserved for backward-compat
    with the legacy single-file tool's output shape.
    ``time_series_spread`` and ``time_series_zscore`` are the canonical
    closed-enum payloads added by this migration so the upcoming
    primitive-to-operator bridge has uniform shapes to consume.  All
    three are computed from the same underlying display DataFrame —
    they cannot drift.
    """

    current_metrics: OISCurveSpreadCurrentMetrics
    time_series: List[OISCurveSpreadTimeSeriesRow]
    time_series_spread: TimeSeries = Field(
        ...,
        description=(
            "Historical OIS curve spread (long_tenor − short_tenor, in "
            "BPS) over the displayed window.  Closed-enum "
            "``TimeSeriesUnits.BPS``; series_name = "
            "'<curve_family_lower>_<short>_<long>_ois_spread'.  Values "
            "match ``time_series[i].spread_bps`` 1-to-1 by construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the OIS spread vs its own "
            "trailing window.  Closed-enum ``TimeSeriesUnits.Z_SCORE``; "
            "series_name = '<curve_family_lower>_<short>_<long>_ois_zscore'.  "
            "Values match ``time_series[i].z_score`` 1-to-1 (None for "
            "rows in the rolling-window warmup)."
        ),
    )


__all__ = [
    "OISCurveSpreadInput",
    "OISCurveSpreadCurrentMetrics",
    "OISCurveSpreadTimeSeriesRow",
    "OISCurveSpreadOutput",
]

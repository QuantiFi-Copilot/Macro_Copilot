"""Pydantic schemas for the OIS forward-rate tool.

Migrated from ``rates_agent/ois/tools/schemas/forward_rate.py`` into
the per-tool-folder pattern.  Fourth OIS tool brought onto the
pattern (after rate_level / curve_spread / cross_market_spread).

Validation layering
-------------------
- Two equivalent input modes: tenor-based (start_tenor, end_tenor) OR
  date-based (start_date, end_date).  The
  ``_validate_window_mode`` validator enforces mutual exclusion.
  Mathematical truth, not configuration — stays in code.

- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_swap_rate_field`` convention".  Mirrors the
  pattern established by the prior three OIS tools.

- ``lookback_days`` controls the *displayed* window only.  The
  rolling z-score window and the trailing range window are
  independent conventions in config.yaml.

- ``high_252d_pct`` / ``low_252d_pct`` / ``percentile_252d`` field
  names embed the trailing-range window length and are wire-frozen
  for backward-compat with the legacy single-file tool.  See
  config.yaml's ``planned_extensions`` for the path to making the
  trailing window configurable.

Canonical TimeSeries output
---------------------------
Per the legacy-sovereign TimeSeries tech-debt cleanup pattern: this
tool emits TWO canonical ``shared.schemas.time_series.TimeSeries``
fields:

  - ``time_series_forward: TimeSeries`` (units = PERCENT) — the
    historical forward-rate values in percent.
  - ``time_series_zscore: TimeSeries`` (units = Z_SCORE) — the
    rolling z-score of the forward rate vs its own trailing window.

The wire-frozen ``time_series: List[OISForwardRateTimeSeriesRow]``
field stays for backward-compat with the legacy single-file tool's
output shape; the canonical fields are what the upcoming
primitive-to-operator bridge consumes.  All three series are computed
from the same underlying display window and cannot drift —
proven by point-by-point parity tests.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator

from shared.schemas import TimeSeries


class OISForwardRateInput(BaseModel):
    """Parameters for computing an implied forward rate on an OIS curve.

    Two equivalent input modes:

    1. **Tenor-based** (common case) — supply ``start_tenor`` and
       ``end_tenor`` (e.g. 1Y/2Y for "1Y1Y", or 5Y/10Y for "5Y5Y").
       Year fractions are constants every day.

    2. **Date-based** — supply ``start_date`` and ``end_date`` in
       ISO-8601 form.  Used for ad-hoc custom windows like "forward
       between Dec 2026 and Jun 2027".  Year fractions re-anchor per
       trade date using the curve's day-count basis (ACT/360 or
       ACT/365).

    Exactly ONE of the two modes must be supplied; the validator
    enforces this.
    """

    curve_family: str = Field(
        ...,
        description=(
            "OIS curve family.  Examples: 'USD_SOFR_OIS', "
            "'EUR_ESTR_OIS', 'GBP_SONIA_OIS', 'JPY_OIS', 'AUD_OIS', "
            "'CAD_OIS'."
        ),
    )

    # ------------------------------------------------------------------
    # MODE 1: tenor pair
    # ------------------------------------------------------------------
    start_tenor: Optional[str] = Field(
        default=None,
        description=(
            "Start tenor of the forward window.  For '1Y1Y' use '1Y'; "
            "for '5Y5Y' use '5Y'; for '2Y1Y' use '2Y'.  Must be present "
            "on the curve.  Mutually exclusive with start_date."
        ),
    )
    end_tenor: Optional[str] = Field(
        default=None,
        description=(
            "End tenor of the forward window.  For '1Y1Y' use '2Y' "
            "(start=1Y + forward=1Y); for '5Y5Y' use '10Y'; for '2Y1Y' "
            "use '3Y'.  Must be present on the curve.  Mutually "
            "exclusive with end_date."
        ),
    )

    # ------------------------------------------------------------------
    # MODE 2: date window
    # ------------------------------------------------------------------
    start_date: Optional[str] = Field(
        default=None,
        description=(
            "Start date of the forward window in ISO-8601 format "
            "(YYYY-MM-DD).  Mutually exclusive with start_tenor.  "
            "Must be on or after the curve's as-of date."
        ),
    )
    end_date: Optional[str] = Field(
        default=None,
        description=(
            "End date of the forward window in ISO-8601 format.  "
            "Must be strictly after start_date.  Mutually exclusive "
            "with end_tenor."
        ),
    )

    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description=(
            "Calendar days of displayed history in the time_series "
            "output.  The rolling z-score window is always a fixed 252 "
            "trading days regardless of this value (fixed by config "
            "convention z_score_window_days)."
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
    as_of_date: Optional[date] = Field(
        default=None,
        description=("Optional as-of date (YYYY-MM-DD): compute as of this trade "
                     "date instead of the latest available data.  None → latest "
                     "(live snapshot).  Supply a date for a historical, replayable view."),
    )

    @model_validator(mode="after")
    def _validate_window_mode(self) -> "OISForwardRateInput":
        has_tenors = bool(self.start_tenor) and bool(self.end_tenor)
        has_dates = bool(self.start_date) and bool(self.end_date)
        partial_tenors = bool(self.start_tenor) ^ bool(self.end_tenor)
        partial_dates = bool(self.start_date) ^ bool(self.end_date)

        if partial_tenors:
            raise ValueError(
                "Both start_tenor and end_tenor must be supplied together."
            )
        if partial_dates:
            raise ValueError(
                "Both start_date and end_date must be supplied together."
            )
        if has_tenors and has_dates:
            raise ValueError(
                "Supply EITHER (start_tenor, end_tenor) OR "
                "(start_date, end_date), not both."
            )
        if not has_tenors and not has_dates:
            raise ValueError(
                "Supply (start_tenor, end_tenor) for tenor-based "
                "forwards like 1Y1Y, or (start_date, end_date) for a "
                "custom window."
            )
        return self


class OISForwardRateCurrentMetrics(BaseModel):
    """Snapshot metrics for the most recent trade date.

    Field names ``high_252d_pct`` / ``low_252d_pct`` /
    ``percentile_252d`` embed the trailing-range window length and
    are wire-frozen.
    """
    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    forward_label: str = Field(
        ..., description="Human-readable label, e.g. 'SOFR 1Y1Y'.",
    )
    start_years: float = Field(
        ...,
        description="Start of the forward window in years from the curve's as-of date.",
    )
    end_years: float = Field(
        ...,
        description="End of the forward window in years from the curve's as-of date.",
    )
    forward_rate_pct: Optional[float] = Field(
        None, description="Implied forward rate (percent).",
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in the forward rate (bps).",
    )
    current_z_score: Optional[float] = Field(
        None,
        description="Rolling 252-day z-score of the forward rate.",
    )
    rolling_window_days: int = Field(
        ..., description="Window used for the z-score calculation.",
    )
    high_252d_pct: Optional[float] = Field(
        None, description="Trailing 252-day high of the forward rate (percent).",
    )
    low_252d_pct: Optional[float] = Field(
        None, description="Trailing 252-day low of the forward rate (percent).",
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within the trailing 252-day range (0-100).",
    )
    start_spot_rate_pct: Optional[float] = Field(
        None,
        description="Par OIS rate at start_years (interpolated, percent).",
    )
    end_spot_rate_pct: Optional[float] = Field(
        None,
        description="Par OIS rate at end_years (interpolated, percent).",
    )


class OISForwardRateTimeSeriesRow(BaseModel):
    """Single row in the bespoke wire-frozen OIS forward-rate
    time-series array.
    """
    date: str
    forward_rate_pct: float
    z_score: Optional[float] = None


class OISForwardRateOutput(BaseModel):
    """Top-level response the MCP server returns to the orchestrator.

    ``time_series`` is the wire-frozen bespoke shape preserved for
    backward-compat with the legacy single-file tool.
    ``time_series_forward`` and ``time_series_zscore`` are the
    canonical closed-enum payloads added by this migration so the
    upcoming primitive-to-operator bridge has uniform shapes to
    consume.  All three are computed from the same underlying display
    window — they cannot drift.
    """

    current_metrics: OISForwardRateCurrentMetrics
    time_series: List[OISForwardRateTimeSeriesRow]
    time_series_forward: TimeSeries = Field(
        ...,
        description=(
            "Historical OIS forward-rate values (percent) over the "
            "displayed window.  Closed-enum "
            "``TimeSeriesUnits.PERCENT``; series_name carries an "
            "OIS-specific ``_ois_forward`` suffix.  Values match "
            "``time_series[i].forward_rate_pct`` 1-to-1 by "
            "construction."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Historical rolling z-score of the OIS forward rate vs "
            "its own trailing window.  Closed-enum "
            "``TimeSeriesUnits.Z_SCORE``; series_name carries an "
            "OIS-specific ``_ois_forward_zscore`` suffix.  Values "
            "match ``time_series[i].z_score`` 1-to-1 (None for rows "
            "in the rolling-window warmup)."
        ),
    )


__all__ = [
    "OISForwardRateInput",
    "OISForwardRateCurrentMetrics",
    "OISForwardRateTimeSeriesRow",
    "OISForwardRateOutput",
]

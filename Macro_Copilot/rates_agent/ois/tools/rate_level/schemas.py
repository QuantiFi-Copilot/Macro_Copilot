"""Pydantic schemas for the OIS rate-level tool.

Migrated from ``rates_agent/ois/tools/schemas/rate_level.py`` into the
per-tool-folder pattern established by the tool-config refactor pilot
(see ``docs/architecture/tool_architecture.md``) and used by every
sovereign tool.  This is the FIRST OIS tool brought onto that pattern
— follow-on OIS tools (curve_spread, forward_rate, cross_market_spread,
scanner) will follow.

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_swap_rate_field`` convention".  Callers can
  still override per-query.  ``compute()`` is the one place that
  resolves the sentinel against the active config, so editing
  ``default_swap_rate_field`` in YAML actually changes runtime
  behaviour.  Mirrors the pattern established by the
  curve_move_classifier sovereign migration (commit 9f741ea) and
  tightened by commit b2605ee.

- ``lookback_days`` controls the fetch window AND the
  ``observation_count`` cutoff window.  It does NOT control the
  rolling z-score window (fixed by ``z_score_window_days``, currently
  252) or the trailing range window (fixed by
  ``trailing_range_window_days``, locked at 252 in V1).

Validators that encode invariants stay here in code; this tool has no
cross-field invariants beyond what Pydantic's basic Field constraints
already enforce.

Canonical TimeSeries output
---------------------------
Per the legacy-sovereign TimeSeries tech-debt cleanup pattern: this
tool emits the canonical ``shared.schemas.time_series.TimeSeries``
shape for the historical par-swap-rate levels via
``time_series: TimeSeries`` (singular, matching the v6 sovereign
primitive convention used by ``yield_levels`` / ``zscore_custom`` etc.).
Each row is rounded with the same ``yield_round_decimals`` convention
the snapshot uses, so the snapshot's ``current_rate_pct`` equals
``time_series.rows[-1].value`` exactly (not just within tolerance).

This unblocks the upcoming primitive-to-operator bridge work
(Phase 1B) — operators need uniform, closed-unit time-series payloads
to consume.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from shared.schemas import TimeSeries


class OISRateLevelInput(BaseModel):
    """Parameters the LLM must extract from the user to fetch an OIS
    rate level.  Every field maps directly to a filter column on
    ``macro_data.v_market_data_daily_enriched``.
    """

    curve_family: str = Field(
        ...,
        description=(
            "OIS curve family identifier as stored in instrument_master. "
            "Examples: 'USD_SOFR_OIS', 'EUR_ESTR_OIS', 'GBP_SONIA_OIS', "
            "'JPY_OIS', 'AUD_OIS', 'CAD_OIS'."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "Tenor point on the OIS curve.  OIS curves have a dense "
            "short-end grid: '1W', '1M', '2M', '3M', '6M', '9M', '1Y', "
            "'2Y', '3Y', '5Y', '10Y', '20Y', '30Y'."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB and used to "
            "scope the observation_count window in the output.  Does "
            "NOT control the rolling z-score window (fixed by config "
            "convention z_score_window_days, currently 252) or the "
            "trailing range window (fixed by "
            "trailing_range_window_days, locked at 252 in V1)."
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


class OISRateLevelMetrics(BaseModel):
    """Deterministic snapshot for a single OIS rate point.

    Field names ``high_252d_pct``, ``low_252d_pct``, and
    ``percentile_252d`` embed the trailing-range window length (252)
    in their identifiers and are therefore wire-frozen in V1.  See
    config.yaml's ``planned_extensions`` for the path to making the
    trailing window configurable.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    tenor: str
    current_rate_pct: float = Field(
        ..., description="Current par swap rate in percent."
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-trading-day change in basis points."
    )
    weekly_change_bps: Optional[float] = Field(
        None, description="5-trading-day change in basis points."
    )
    monthly_change_bps: Optional[float] = Field(
        None, description="22-trading-day (~1 month) change in basis points."
    )
    z_score: Optional[float] = Field(
        None,
        description="Rolling 252-trading-day z-score of the rate vs its own history.",
    )
    high_252d_pct: Optional[float] = Field(
        None, description="Highest rate over trailing 252 trading days (percent)."
    )
    low_252d_pct: Optional[float] = Field(
        None, description="Lowest rate over trailing 252 trading days (percent)."
    )
    percentile_252d: Optional[float] = Field(
        None,
        description="Percentile rank within trailing 252-day range (0-100).",
    )
    observation_count: int = Field(
        ..., description="Number of trading days in the lookback_days window."
    )


class OISRateLevelOutput(BaseModel):
    """Top-level response for the OIS rate_level tool.

    ``current_metrics`` is the wire-frozen snapshot the legacy
    single-file tool returned.  ``time_series`` is added by this
    migration so the upcoming primitive-to-operator bridge has a
    uniform closed-enum shape to consume — see module docstring.
    Singular ``TimeSeries`` field matches the v6 sovereign primitive
    convention (see ``yield_levels`` / ``zscore_custom``).
    """

    current_metrics: OISRateLevelMetrics
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Historical OIS par-swap-rate levels at the requested "
            "tenor over the display window (last ``lookback_days`` "
            "calendar days).  Closed-enum ``TimeSeriesUnits.PERCENT`` "
            "units; series_name = "
            "'<curve_family_lower>_<tenor_lower>_ois_rate'.  Each row "
            "is rounded with the same ``yield_round_decimals`` "
            "convention the snapshot uses, so the snapshot's "
            "``current_rate_pct`` equals "
            "``time_series.rows[-1].value`` STRICTLY (not just within "
            "tolerance)."
        ),
    )


__all__ = [
    "OISRateLevelInput",
    "OISRateLevelMetrics",
    "OISRateLevelOutput",
]

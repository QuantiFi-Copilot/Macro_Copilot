"""Pydantic schemas for the yield_levels tool.

Migrated from ``rates_agent/sovereign_bonds/tools/schemas/yield_level.py``
into the per-tool folder pattern established by the tool-config
refactor pilot (docs/architecture/tool_architecture.md).

Validation layering
-------------------
- ``field_name`` defaults to ``None`` — the sentinel that means "use
  the YAML's ``default_field_name`` convention".  Callers can still
  override per-query.  ``compute()`` is the one place that resolves
  the sentinel against the active config, so editing
  ``default_field_name`` in YAML actually changes runtime behaviour.
  Mirrors the pattern established by the curve_move_classifier
  migration (commit 9f741ea) and tightened by commit b2605ee.

- ``lookback_days`` semantics are unchanged from the legacy tool:
  it controls the fetch window and the observation_count's cutoff,
  NOT the rolling z-score window or the trailing range window.  An
  earlier migration plan considered repurposing it as the
  trailing-window override; that idea was dropped because the field
  names ``high_252d_pct`` / ``low_252d_pct`` / ``percentile_252d`` are
  wire-frozen for frontend backward-compat.  The misleading
  description on the legacy schema is corrected here.

Validators that encode invariants stay here in code; this tool has
no cross-field invariants beyond what Pydantic's basic Field
constraints already enforce.

Canonical TimeSeries output
---------------------------
Per the legacy-sovereign TimeSeries tech-debt cleanup: every legacy
tool with a natural time series now emits a ``canonical_time_series:
List[TimeSeries]`` field using the closed-enum
``shared.schemas.time_series.TimeSeries`` shape.  This unblocks the
upcoming primitive-to-operator bridge work (Phase 1B) — operators
need uniform, closed-unit time-series payloads to consume.

For ``yield_levels`` specifically, the canonical series carries the
historical yield levels at the requested tenor in PERCENT units,
covering the same display window as the bespoke metrics and using
the same cleaned (ffill'd) underlying data the snapshot uses.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from shared.schemas import TimeSeries


class YieldLevelInput(BaseModel):
    """Parameters the LLM extracts to query a single yield point."""

    curve_family: str = Field(
        ...,
        description=(
            "Curve family identifier as stored in instrument_master. "
            "Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT', "
            "'IT_BTP', 'ES_BONO', 'CANADA_GOVT', 'AU_GOVT'."
        ),
    )
    tenor: str = Field(
        ...,
        description=(
            "The tenor point to query, e.g. '1Y', '2Y', '3Y', '5Y', "
            "'7Y', '10Y', '20Y', '30Y'."
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
            "trailing range window (fixed by trailing_range_window_days, "
            "locked at 252 in V1)."
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
            "shadowed.  See the curve_move_classifier wrapper-shadowing "
            "fix (commit b2605ee) for the canonical pattern."
        ),
    )


class YieldLevelMetrics(BaseModel):
    """Deterministic snapshot for a single yield point.

    Field names ``high_252d_pct``, ``low_252d_pct``, and
    ``percentile_252d`` embed the trailing-range window length (252)
    in their identifiers and are therefore wire-frozen in V1.  See
    config.yaml's ``planned_extensions`` for the path to making the
    trailing window configurable.
    """

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    tenor: str
    current_yield_pct: float = Field(..., description="Current yield in percent.")
    daily_change_bps: Optional[float] = Field(None, description="1-day change in basis points.")
    weekly_change_bps: Optional[float] = Field(None, description="5-trading-day change in basis points.")
    monthly_change_bps: Optional[float] = Field(None, description="22-trading-day change in basis points.")
    z_score: Optional[float] = Field(None, description="Rolling 252-trading-day z-score.")
    high_252d_pct: Optional[float] = Field(None, description="Highest yield over trailing 252 trading days.")
    low_252d_pct: Optional[float] = Field(None, description="Lowest yield over trailing 252 trading days.")
    percentile_252d: Optional[float] = Field(None, description="Percentile rank within trailing 252-day range (0-100).")
    observation_count: int = Field(..., description="Number of trading days in the lookback_days window.")


class YieldLevelOutput(BaseModel):
    """Top-level response for the yield_levels tool.

    ``current_metrics`` is the wire-frozen snapshot the frontend has
    consumed since the legacy tool shipped.  ``canonical_time_series``
    was added by the legacy-TimeSeries tech-debt cleanup so the
    upcoming primitive-to-operator bridge has a uniform shape to
    consume — see module docstring.
    """

    current_metrics: YieldLevelMetrics
    canonical_time_series: List[TimeSeries] = Field(
        default_factory=list,
        description=(
            "Historical yield levels at the requested tenor over the "
            "display window (last ``lookback_days`` calendar days).  "
            "Uses the canonical ``shared.schemas.time_series.TimeSeries`` "
            "shape with closed-enum units.  One series:\n"
            "  - units = PERCENT\n"
            "  - series_name = '<curve_family_lower>_<tenor_lower>_yield'\n"
            "  - values match ``current_metrics.current_yield_pct`` at "
            "    the latest row by construction (same cleaned series "
            "    the snapshot was computed from)."
        ),
    )


__all__ = [
    "YieldLevelInput",
    "YieldLevelMetrics",
    "YieldLevelOutput",
]

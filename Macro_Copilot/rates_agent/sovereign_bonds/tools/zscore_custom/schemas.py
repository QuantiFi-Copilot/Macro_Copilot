"""Pydantic schemas for the zscore_custom tool.

Per the v6 sprint plan (docs/architecture/tool_architecture.md, A13):
the user-facing input surface is intentionally narrow — only
`curve_family`, `tenor`, the central knob `z_score_window_days`, and
the display window `lookback_days` are exposed.  Methodology
ancillaries (`min_periods`, `ddof`, `buffer_multiplier`,
`ffill_limit_days`, rounding) live in YAML and are NOT user-
overridable in V1.

Time-series output uses the shared ``TimeSeries`` shape from
``shared.schemas`` — this tool is the first new tool in the v6 sprint
to consume that contract.  Existing migrated tools keep their
bespoke wire formats; the retro-fit is deferred to a separate
cleanup PR.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from shared.schemas import TimeSeries


class ZscoreCustomInput(BaseModel):
    """Parameters the LLM extracts to compute a custom-window z-score.

    The single methodological choice exposed to the user is
    ``z_score_window_days``.  Every other knob is YAML-locked.
    """

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description=(
            "Curve family identifier.  Examples: 'UST', 'DE_BUND', "
            "'UK_GILT', 'JGB', 'FR_OAT', 'IT_BTP', 'ES_BONO', "
            "'CANADA_GOVT', 'AU_GOVT'."
        ),
    )
    tenor: str = Field(
        ...,
        min_length=1,
        description=(
            "Tenor point — e.g. '1Y', '2Y', '3Y', '5Y', '7Y', '10Y', "
            "'20Y', '30Y'."
        ),
    )
    z_score_window_days: int = Field(
        ...,
        ge=20,
        le=1260,
        description=(
            "Rolling window length in trading days for the z-score.  "
            "This is the tool's central methodological choice — set per "
            "request.  Typical desk values: 60 for tactical signals, "
            "126 for quarterly, 252 for annual, 504 for two-year.  "
            "Constrained to [20, 1260] to keep the rolling stat "
            "meaningful (below ~20 the warm-up dominates the displayed "
            "series given the YAML's min_periods=60; above ~1260 the "
            "data history may not support the window)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of *displayed* z-score history.  Does NOT "
            "control the rolling window length (that is "
            "z_score_window_days, the central knob)."
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


class ZscoreCustomMetrics(BaseModel):
    """Snapshot metrics for a custom-window z-score query."""

    model_config = ConfigDict(extra="forbid")

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    curve_family: str
    tenor: str
    current_yield_pct: Optional[float] = Field(
        None,
        description=(
            "Latest yield observation (percent), rounded per "
            "``yield_round_decimals``.  None if the cleaned series is "
            "empty after fetch."
        ),
    )
    current_z_score: Optional[float] = Field(
        None,
        description=(
            "Current z-score, rounded per ``z_score_round_decimals``.  "
            "None when the rolling window has fewer than the YAML's "
            "``z_score_min_periods`` valid observations at the latest "
            "trade date."
        ),
    )
    z_score_window_days_used: int = Field(
        ...,
        description=(
            "The rolling-window length actually used (echoes the user's "
            "input)."
        ),
    )
    z_score_min_periods_used: int = Field(
        ...,
        description=(
            "min_periods used for the rolling stat — sourced from YAML, "
            "echoed for transparency."
        ),
    )
    z_score_ddof_used: int = Field(
        ...,
        description=(
            "Degrees of freedom used for the rolling std — sourced from "
            "YAML, echoed for transparency."
        ),
    )
    observation_count: int = Field(
        ...,
        ge=0,
        description=(
            "Number of trading days in the displayed lookback_days "
            "window after cleaning + ffill."
        ),
    )


class ZscoreCustomOutput(BaseModel):
    """Top-level response for the zscore_custom tool.

    Two TimeSeries fields are exposed, both carrying the SAME payload:
    the rolling z-score over the displayed lookback_days window.  The
    duplication is a substrate-naming-convention adapter, not a
    semantic split.

    Why both fields exist
    ---------------------
    ``time_series`` was the V1 field name (kept for backward
    compatibility — every backtest/event_study test prior to this PR
    binds it).  ``time_series_zscore`` is the canonical name used by
    every other z-score-emitting primitive in the codebase
    (curve_spread, cross_market_spread, swap_spread, butterfly,
    breakeven_inflation, OIS forward_rate).  Adding it here closes the
    naming inconsistency that caused the workflow_router to bind
    ``time_series_zscore`` for zscore_custom and crash at the bridge
    lift step.

    Callers should prefer ``time_series_zscore`` going forward; the
    legacy ``time_series`` field remains valid and identical.
    """

    model_config = ConfigDict(extra="forbid")

    current_metrics: ZscoreCustomMetrics
    time_series: TimeSeries = Field(
        ...,
        description=(
            "Rolling z-score over the displayed lookback_days window.  "
            "Uses the shared ``TimeSeries`` shape (see "
            "shared.schemas.time_series); units = 'z_score'.  Legacy "
            "field name retained for backward compatibility — "
            "equivalent to ``time_series_zscore``."
        ),
    )
    time_series_zscore: TimeSeries = Field(
        ...,
        description=(
            "Rolling z-score over the displayed lookback_days window — "
            "canonical naming alias for ``time_series``.  Identical "
            "payload by construction; both fields are populated from "
            "the same computation.  Matches the ``time_series_zscore`` "
            "convention used by every other z-score-emitting primitive."
        ),
    )


__all__ = [
    "ZscoreCustomInput",
    "ZscoreCustomMetrics",
    "ZscoreCustomOutput",
]

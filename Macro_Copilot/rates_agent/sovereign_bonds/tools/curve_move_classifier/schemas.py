"""Pydantic schemas for the curve_move_classifier tool.

Renamed from the legacy ``CurveRegime*`` family.  The previous name
overclaimed: this tool classifies a single observed move, not a
persistence state.

The Input schema's ``lookback_period`` validator enforces the discrete
enum drawn from ``config.yaml``'s ``allowed_lookback_periods``
convention — invalid labels are rejected with a clear "see config.yaml
for the supported set" message rather than silently accepted.

Validators that encode invariants (front/back tenors must differ) stay
here in code; they are not configurable.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, model_validator


class CurveMoveInput(BaseModel):
    """Parameters the LLM extracts to classify a curve move."""

    curve_family: str = Field(
        ...,
        description=(
            "Curve family identifier as stored in instrument_master. "
            "Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT'."
        ),
    )
    front_tenor: str = Field(
        default="2Y",
        description="The front-end (short) leg.  Default '2Y'.",
    )
    back_tenor: str = Field(
        default="10Y",
        description="The back-end (long) leg.  Default '10Y'.",
    )
    lookback_period: str = Field(
        default="1d",
        description=(
            "Discrete period over which the move is measured: '1d' "
            "(today's move), '5d' (weekly), '22d' (monthly), '63d' "
            "(quarterly).  The full set of allowed values comes from "
            "the tool's config.yaml — see allowed_lookback_periods."
        ),
    )
    field_name: str = Field(
        default="YLD_YTM_MID",
        description=(
            "Bloomberg observation field.  Default mid yield-to-maturity."
        ),
    )

    @model_validator(mode="after")
    def _tenors_must_differ(self) -> "CurveMoveInput":
        if self.front_tenor == self.back_tenor:
            raise ValueError(
                f"front_tenor and back_tenor must be different, but "
                f"both are '{self.front_tenor}'."
            )
        return self


class CurveMoveCurrentMetrics(BaseModel):
    """Snapshot classification + supporting numbers."""

    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    prior_date: str = Field(..., description="The comparison date at the start of the lookback.")
    curve_family: str
    lookback_period: str = Field(..., description="The lookback period used.")
    spread_label: str = Field(..., description="Human-readable label, e.g. '2s10s'.")

    # NOTE: the field name is ``classification`` rather than the legacy
    # ``regime_tag``.  Same content; the rename completes the move
    # away from the misleading "regime" word.  ``regime_tag`` is NOT
    # provided as a backward-compat alias — every caller that read
    # ``regime_tag`` is being migrated in this same commit.
    classification: str = Field(
        ...,
        description=(
            "One of BULL_STEEPENER, BEAR_STEEPENER, BULL_FLATTENER, "
            "BEAR_FLATTENER, PARALLEL_SHIFT, TWIST."
        ),
    )
    description: str = Field(
        ...,
        description="Plain-English explanation of the classification.",
    )

    front_tenor: str
    back_tenor: str
    front_yield_current: Optional[float] = Field(None, description="Current yield on the front leg (percent).")
    back_yield_current: Optional[float] = Field(None, description="Current yield on the back leg (percent).")
    front_yield_prior: Optional[float] = Field(None, description="Prior yield on the front leg (percent).")
    back_yield_prior: Optional[float] = Field(None, description="Prior yield on the back leg (percent).")
    front_change_bps: Optional[float] = Field(None, description="Change in the front leg over the lookback (bps).")
    back_change_bps: Optional[float] = Field(None, description="Change in the back leg over the lookback (bps).")
    spread_current_bps: Optional[float] = Field(None, description="Current spread (back − front) in bps.")
    spread_prior_bps: Optional[float] = Field(None, description="Prior spread (back − front) in bps.")
    spread_change_bps: Optional[float] = Field(None, description="Change in the spread over the lookback (bps).")


class CurveMoveOutput(BaseModel):
    """Top-level response for the curve-move classifier."""

    current_metrics: CurveMoveCurrentMetrics


__all__ = [
    "CurveMoveInput",
    "CurveMoveCurrentMetrics",
    "CurveMoveOutput",
]

"""Pydantic schemas for the curve regime classifier tool."""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, model_validator


class CurveRegimeInput(BaseModel):
    """Parameters for deterministic curve-move classification."""
    curve_family: str = Field(..., description="Curve family identifier. Examples: 'UST', 'DE_BUND', 'UK_GILT', 'JGB', 'FR_OAT'.")
    front_tenor: str = Field(default="2Y", description="The front-end (short) leg. Defaults to '2Y'.")
    back_tenor: str = Field(default="10Y", description="The back-end (long) leg. Defaults to '10Y'.")
    lookback_period: str = Field(default="1d", description="Period to measure: '1d' (today), '5d' (weekly), '22d' (monthly).")
    field_name: str = Field(default="YLD_YTM_MID", description="The observation field to use. Must match the exact value in the database.")

    @model_validator(mode="after")
    def _tenors_must_differ(self) -> "CurveRegimeInput":
        if self.front_tenor == self.back_tenor:
            raise ValueError(f"front_tenor and back_tenor must be different, but both are '{self.front_tenor}'.")
        return self


class CurveRegimeCurrentMetrics(BaseModel):
    """Deterministic curve-move classification with supporting numbers."""
    as_of_date: str = Field(..., description="Most recent trade date (YYYY-MM-DD).")
    prior_date: str = Field(..., description="The comparison date at the start of the lookback.")
    curve_family: str
    lookback_period: str = Field(..., description="The lookback period used: '1d', '5d', or '22d'.")
    spread_label: str = Field(..., description="Human-readable label, e.g. '2s10s'.")
    regime_tag: str = Field(..., description="Deterministic classification: BULL_STEEPENER, BEAR_STEEPENER, BULL_FLATTENER, BEAR_FLATTENER, PARALLEL_SHIFT, or TWIST.")
    regime_description: str = Field(..., description="Plain-English explanation of the regime.")
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


class CurveRegimeOutput(BaseModel):
    """Top-level response for the curve regime classifier."""
    current_metrics: CurveRegimeCurrentMetrics

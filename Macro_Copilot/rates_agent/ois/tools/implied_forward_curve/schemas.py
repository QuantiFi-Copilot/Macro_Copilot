"""Pydantic I/O schemas for implied_forward_curve (§7-C, Bucket-1B).

Emits the forward STRIP as a typed Panel artifact (the composable output)
plus a snapshot of the latest forward-curve cross-section.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel

_DEFAULT_ANCHORS = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]


class ImpliedForwardCurveInput(BaseModel):
    """Per-query inputs for the implied forward strip."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    curve_family: str = Field(
        ...,
        min_length=1,
        description="OIS curve family, e.g. 'USD_SOFR_OIS'.",
    )
    forward_horizon: Optional[str] = Field(
        default=None,
        description=(
            "Forward window length (e.g. '1Y' → the 1y-forward curve).  "
            "None (default) → config default_forward_horizon."
        ),
    )
    anchor_tenors: List[str] = Field(
        default_factory=lambda: list(_DEFAULT_ANCHORS),
        min_length=2,
        description=(
            "The anchor tenors across the grid (>= 2 distinct).  For each "
            "anchor a, the forward over (a, a+horizon) is computed.  Default "
            "1Y/2Y/3Y/5Y/7Y/10Y."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description="Calendar days of forward-strip history.",
    )
    field_name: Optional[str] = Field(
        default=None,
        description="OIS rate field; None → config default_swap_rate_field.",
    )

    @model_validator(mode="after")
    def _anchors_distinct(self) -> "ImpliedForwardCurveInput":
        if len(set(self.anchor_tenors)) != len(self.anchor_tenors):
            raise ValueError(
                f"anchor_tenors must be distinct (got {self.anchor_tenors})."
            )
        return self


class ForwardCurvePoint(BaseModel):
    """One point of the latest forward-curve cross-section."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    anchor_tenor: str
    forward_label: str           # e.g. "1y1y"
    forward_rate_pct: Optional[float]


class ImpliedForwardCurveCurrentMetrics(BaseModel):
    """Snapshot of the latest forward curve."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    as_of_date: str
    curve_family: str
    forward_horizon: str
    n_anchors: int
    n_observations: int
    forward_curve: List[ForwardCurvePoint]


class ImpliedForwardCurveOutput(BaseModel):
    """Top-level response for the implied forward strip."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    current_metrics: ImpliedForwardCurveCurrentMetrics
    methodology_disclosures: List[str]
    # The composable typed Panel artifact (rows = dates, columns =
    # <anchor>_fwd).  The MCP layer drops this before serialising for the
    # LLM; the workflow executor's Panel bridge extracts it.
    forward_strip: Panel


__all__ = [
    "ImpliedForwardCurveInput",
    "ForwardCurvePoint",
    "ImpliedForwardCurveCurrentMetrics",
    "ImpliedForwardCurveOutput",
]

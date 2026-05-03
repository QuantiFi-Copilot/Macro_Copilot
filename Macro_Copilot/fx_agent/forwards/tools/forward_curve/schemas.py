from __future__ import annotations

from pydantic import BaseModel, Field


class FXForwardCurveInput(BaseModel):
    """Parameters for a single-pair FX forward curve snapshot."""

    pair: str = Field(
        ...,
        description="FX pair identifier, e.g. 'EURUSD', 'GBPUSD', 'USDJPY'.",
    )


class FXForwardCurveRow(BaseModel):
    pair: str
    spot_date: str
    forward_date: str
    spot: float
    tenor: str
    tenor_days: int
    forward_points: float
    forward_points_spot_units: float
    outright_forward: float
    carry_bps_spot: float
    carry_annualized_pct: float


class FXForwardCurveOutput(BaseModel):
    pair: str
    rows: list[FXForwardCurveRow]

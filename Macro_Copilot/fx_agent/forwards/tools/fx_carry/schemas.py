"""
Pydantic input/output schemas for the FX carry tool.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FXCarryInput(BaseModel):
    """Parameters for FX carry analytics."""

    tenor: str = Field(
        default="1M",
        description="Forward tenor used for carry calculation, e.g. 1W, 1M, 3M, 6M.",
    )


class FXCarryRow(BaseModel):
    pair: str
    spot_date: str
    forward_date: str
    spot: float
    tenor: str
    forward_points: float
    forward_points_spot_units: float
    outright_forward: float
    carry_bps_spot: float
    carry_annualized_pct: float
    carry_signal: str


class FXCarryOutput(BaseModel):
    tenor: str
    rows: list[FXCarryRow]
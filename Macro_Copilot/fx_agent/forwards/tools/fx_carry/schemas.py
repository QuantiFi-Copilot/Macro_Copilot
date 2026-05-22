"""
Pydantic input/output schemas for the FX carry tool.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Closed set of supported forward tenors. Mirrors the keys in
# ``fx_agent/forwards/tools/fx_carry/config.yaml`` (tenor_<n>_days) and
# the universe shipped via ``fx_agent/playbooks/fx_forwards.yml`` v2.0.
# Adding a new tenor = add it here AND add ``tenor_<n>_days`` in the
# config; ``compute.py::_tenor_days_from_config`` reads both.
FXCarryTenor = Literal["1W", "1M", "3M", "6M", "12M"]


class FXCarryInput(BaseModel):
    """Parameters for FX carry analytics."""

    tenor: FXCarryTenor = Field(
        default="1M",
        description=(
            "Forward tenor used for the carry calculation. Must be one "
            "of: 1W, 1M, 3M, 6M, 12M. Default 1M. The annualisation "
            "factor reads the corresponding ``tenor_<n>_days`` from "
            "the tool config — see config.yaml."
        ),
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
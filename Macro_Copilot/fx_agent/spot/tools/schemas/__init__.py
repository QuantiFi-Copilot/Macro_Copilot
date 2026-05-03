"""
Shared schemas for FX spot tools.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FXScannerInput(BaseModel):
    """Parameters for scanning FX spot pairs."""

    market_scope: Optional[str] = Field(
        default=None,
        description="Optional market filter, e.g. 'G10'.",
    )
    top_n: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of pairs to return.",
    )
    field_name: str = Field(
        default="PX_LAST",
        description="Bloomberg field to query.",
    )


class FXScannerRow(BaseModel):
    """One row in the FX scanner output."""

    pair: str
    ticker: str
    as_of_date: str
    current_spot: float
    daily_change_pct: Optional[float] = None
    weekly_change_pct: Optional[float] = None
    monthly_change_pct: Optional[float] = None
    momentum_1m_pct: Optional[float] = None
    momentum_3m_pct: Optional[float] = None
    z_score: Optional[float] = None
    signal: str = "Neutral"


class FXScannerOutput(BaseModel):
    """Top-level response for the FX scanner tool."""

    rows: list[FXScannerRow]
"""Pydantic schemas for the sovereign z-score scanner tool.

Schemas live in the canonical per-tool-folder location at
``rates_agent/sovereign_bonds/tools/scan_extremes/schemas.py``.  The
schemas hub at ``rates_agent/sovereign_bonds/tools/schemas/__init__.py``
re-exports these names so existing
``from rates_agent.sovereign_bonds.tools.schemas import ScannerInput``
imports continue to work.

Migrated from the legacy flat file
``rates_agent/sovereign_bonds/tools/schemas/scanner.py`` (deleted in
the same commit).  Field definitions are byte-identical to the legacy
shape — no breaking change for callers.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ScannerInput(BaseModel):
    """Parameters for the z-score scanner that screens all sovereign
    instruments for statistical extremes."""

    curve_families: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of curve families to scan. If None, scans ALL "
            "sovereign benchmark curves."
        ),
    )
    top_n: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of top extreme results to return (default 10).",
    )
    min_abs_z_score: float = Field(
        default=1.5,
        ge=0.0,
        description="Minimum absolute z-score threshold. Defaults to 1.5.",
    )
    field_name: str = Field(
        default="YLD_YTM_MID",
        description=(
            "The observation field to scan. Must match the exact value in "
            "the database."
        ),
    )


class ScannerResultRow(BaseModel):
    """Single ranked result from the scanner."""

    rank: int = Field(..., description="Rank by absolute z-score (1 = most extreme).")
    curve_family: str
    tenor: str
    as_of_date: str
    current_yield_pct: Optional[float] = Field(
        None, description="Current yield in percent."
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1-day change in basis points."
    )
    z_score: Optional[float] = Field(None, description="252-day rolling z-score.")
    high_252d_pct: Optional[float] = Field(
        None, description="252-day trailing high (percent)."
    )
    low_252d_pct: Optional[float] = Field(
        None, description="252-day trailing low (percent)."
    )
    percentile_252d: Optional[float] = Field(
        None, description="Percentile within 252-day range (0-100)."
    )
    signal: str = Field(
        ...,
        description="'EXTREME_HIGH' if z-score is positive, 'EXTREME_LOW' if negative.",
    )


class ScannerOutput(BaseModel):
    """Top-level response for the scanner tool."""

    scan_summary: str = Field(
        ..., description="Human-readable summary of the scan results."
    )
    results: List[ScannerResultRow]

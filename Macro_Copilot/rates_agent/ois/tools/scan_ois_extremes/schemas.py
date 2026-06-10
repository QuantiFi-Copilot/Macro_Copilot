"""Pydantic schemas for the OIS z-score scanner tool.

Schemas live in the canonical per-tool-folder location at
``rates_agent/ois/tools/scan_ois_extremes/schemas.py``.  The schemas
hub at ``rates_agent/ois/tools/schemas/__init__.py`` re-exports these
names so existing
``from rates_agent.ois.tools.schemas import OISScannerInput`` imports
continue to work.

Migrated from the legacy flat file
``rates_agent/ois/tools/schemas/scanner.py`` (deleted in the same
commit).  Field definitions are byte-identical to the legacy shape —
no breaking change for callers.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class OISScannerInput(BaseModel):
    """Parameters the LLM must extract to scan the OIS universe for
    statistically extreme observations."""

    curve_families: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of OIS curves to scope the scan to.  If "
            "None or empty, scans every OIS curve in the database.  "
            "Examples: ['USD_SOFR_OIS'], ['USD_SOFR_OIS', 'EUR_ESTR_OIS']."
        ),
    )
    top_n: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Number of top-ranked (by |z-score|) results to return.",
    )
    min_abs_z_score: float = Field(
        default=1.5,
        ge=0.0,
        description=(
            "Minimum absolute z-score threshold.  Only instruments with "
            "|z-score| >= this value are included.  Set to 0 to see "
            "the full ranked list."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description=(
            "Observation field to use.  Defaults to 'PX_LAST' (mid par "
            "swap rate).  Other valid: 'PX_BID', 'PX_ASK'."
        ),
    )


class OISScannerResultRow(BaseModel):
    """One ranked extreme on the OIS curve universe."""

    rank: int
    curve_family: str
    tenor: str
    as_of_date: str
    current_rate_pct: Optional[float] = Field(
        None, description="Latest par swap rate (percent)."
    )
    daily_change_bps: Optional[float] = None
    z_score: Optional[float] = None
    high_252d_pct: Optional[float] = None
    low_252d_pct: Optional[float] = None
    percentile_252d: Optional[float] = None
    signal: str = Field(
        ...,
        description="'EXTREME_HIGH' (positive z) or 'EXTREME_LOW' (negative z).",
    )


class OISScannerOutput(BaseModel):
    """Top-level scan response."""

    scan_summary: str
    results: List[OISScannerResultRow]

"""Pydantic schemas for the FX cross-currency basis scanner.

Phase F1 (2026-05-27).

Ranks the V1 closed pair set {EURUSD, GBPUSD, USDJPY, AUDUSD,
USDCAD} by current basis level, absolute basis, or absolute
z-score. Sign convention inherited from cross_currency_basis /
fx_basis_panel: NEGATIVE basis = USD scarcity.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR13.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


FXBasisScannerTenor = Literal["1W", "1M", "3M", "6M", "12M"]
FXBasisScannerRankBy = Literal["basis_signed", "abs_basis", "abs_z_score"]


class FXBasisScannerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenor: FXBasisScannerTenor = Field(
        default="1M",
        description="Basis tenor (same on FX and OIS legs internally).",
    )
    rank_by: FXBasisScannerRankBy = Field(
        default="basis_signed",
        description=(
            "'basis_signed' = ascending by basis level (most negative "
            "first = greatest USD scarcity). 'abs_basis' = descending "
            "by |basis|. 'abs_z_score' = descending by |z|."
        ),
    )
    top_n: Optional[int] = Field(
        default=None, ge=1,
        description="Truncate to top-N after sorting. None ⇒ all V1 pairs.",
    )
    lookback_days: int = Field(
        default=365, ge=30, le=7300,
        description="DB fetch window passed to fx_basis_panel.",
    )


class FXBasisScannerRow(BaseModel):
    pair: str
    tenor: str
    as_of_date: str
    current_basis_bps: float
    z_score: Optional[float]
    percentile_252d: Optional[float]
    high_252d_bps: Optional[float]
    low_252d_bps: Optional[float]
    observation_count: int
    rank: int


class FXBasisScannerOutput(BaseModel):
    tenor: str
    market_scope: str = Field(default="G10_BASIS_V1")
    sign_convention: str = Field(default="bloomberg_bcrx_usd_scarcity_negative")
    rows: List[FXBasisScannerRow]


__all__ = [
    "FXBasisScannerInput",
    "FXBasisScannerOutput",
    "FXBasisScannerRow",
    "FXBasisScannerTenor",
    "FXBasisScannerRankBy",
]

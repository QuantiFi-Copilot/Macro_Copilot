"""Pydantic schemas for the calculate_fx_basis_panel tool.

Phase F1 (2026-05-27).

Composition primitive that assembles a cross-sectional ``Panel``
of CIP basis (in BPS) for the V1 closed pair set ``{EURUSD,
GBPUSD, USDJPY, AUDUSD, USDCAD}`` at one tenor. Sign convention
inherited from cross_currency_basis: Bloomberg BCRX-style,
NEGATIVE = USD scarcity.

V1 SCOPE: G10_BASIS_V1 only (5 pairs with both FX-forward and
local/USD OIS coverage). Extending requires:
  1. Add (local_currency, OIS curve_family) entry to
     ``_LOCAL_CURRENCY_TO_OIS_CURVE`` in compute.py
  2. Validate OIS substrate ingestion via load_audit
  3. Add pair to the Literal in this file

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR12, PR13.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel


FXBasisPanelMarketScope = Literal["G10_BASIS_V1"]
FXBasisPanelTenor = Literal["1W", "1M", "3M", "6M", "12M"]


class FXBasisPanelInput(BaseModel):
    """Parameters the LLM extracts to assemble an FX CIP basis panel.

    market_scope is wire-frozen at 'G10_BASIS_V1' in V1 — the closed
    pair set has only one realisation. Future extensions to EM or
    extended G10 (USDCHF, NZDUSD, etc.) will introduce new scope tags.
    """

    model_config = ConfigDict(extra="forbid")

    market_scope: FXBasisPanelMarketScope = Field(
        default="G10_BASIS_V1",
        description=(
            "V1 closed pair set with FX-forward + OIS coverage: "
            "EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD. Wire-frozen "
            "single value in V1; extending V1 requires new scope tag."
        ),
    )
    start_date: date = Field(
        ...,
        description="Inclusive lower bound on trade_date.",
    )
    end_date: Optional[date] = Field(
        default=None,
        description="Inclusive upper bound. None ⇒ latest in DB.",
    )
    tenor: FXBasisPanelTenor = Field(
        default="1M",
        description=(
            "Basis tenor. Same tenor applied to FX leg AND OIS leg "
            "(FX 12M aliased to OIS 1Y internally)."
        ),
    )
    missing_data_policy: Optional[str] = Field(
        default=None,
        description=(
            "How to handle NaN cells after forward-fill. None ⇒ "
            "config default. Allowed: ['raise', 'forward_fill_only', "
            "'drop_rows_any_missing']."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override applied to FX (spot, forward) "
            "AND OIS legs. None falls through to PX_LAST."
        ),
    )

    @model_validator(mode="after")
    def _date_range_sensible(self) -> "FXBasisPanelInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} cannot be before start_date "
                f"{self.start_date}."
            )
        return self


class FXBasisPanelOutput(BaseModel):
    """Top-level response carrying the typed Panel artifact.

    Mirror of ``FXPanelOutput`` shape with the basis-specific
    ``tenor`` and ``sign_convention`` echoes added. The MCP layer
    drops ``panel`` (the typed artifact) before serialising for the LLM.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    as_of_start: str
    as_of_end: str
    market_scope: str = Field(
        ...,
        description="Echo of the input market_scope.",
    )
    tenor: str = Field(
        ...,
        description="Echo of the input tenor (e.g. '1M').",
    )
    sign_convention: str = Field(
        ...,
        description=(
            "HARD-LOCKED 'bloomberg_bcrx_usd_scarcity_negative'. "
            "Inherited from cross_currency_basis primitive."
        ),
    )
    columns: List[str] = Field(
        ...,
        description=(
            "Column names in the assembled Panel — one per pair, "
            "ordered alphabetically. Pair names, NOT vendor tickers."
        ),
    )
    n_observations: int = Field(
        ...,
        description="Number of rows in the assembled Panel.",
    )
    n_pairs: int = Field(
        ...,
        description="Number of columns in the assembled Panel.",
    )
    units_by_column: dict = Field(
        ...,
        description=(
            "Per-column unit tag — 'bps' for every basis column "
            "(closed-enum from TimeSeriesUnits)."
        ),
    )
    methodology_disclosures: List[str] = Field(
        default_factory=list,
        description="Inline disclosure block.",
    )
    panel: Optional[Panel] = Field(
        default=None,
        description=(
            "The typed Panel artifact (rows = dates, columns = "
            "pair names, values = CIP basis in bps). The MCP layer "
            "drops this field before serialising for the LLM."
        ),
    )


__all__ = [
    "FXBasisPanelInput",
    "FXBasisPanelOutput",
    "FXBasisPanelMarketScope",
    "FXBasisPanelTenor",
]

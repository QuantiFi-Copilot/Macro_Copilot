"""Pydantic schemas for the calculate_fx_vol_panel tool.

Phase F1 (2026-05-27).

Mirror of ``fx_agent.spot.tools.fx_panel`` schemas for the fx_vol
substrate. Assembles a typed ``Panel`` artifact for a
(market_scope, tenor, smile_point) slice of the vol universe.

Codex Q2 lock: the output carries a real typed ``Panel`` artifact
via ``shared.artifacts.types.Panel`` (Shape C). Not a dict, not a
DataFrame, not an ad-hoc namedtuple.

Operationalises: P3 (canonical Panel contract — mirrors fx_panel),
P5 (vol_pct unit explicit), P11 (vol primitives in fx_agent/vol/).
PR1, PR4, PR5, PR7, PR10, PR12, PR13.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel


FXVolPanelMarketScope = Literal["G10", "EM", "G10_CROSSES", "ALL"]
FXVolPanelTenor = Literal["1W", "1M", "3M", "6M", "12M"]
FXVolPanelSmilePoint = Literal["ATM", "25R", "25B", "10R", "10B"]


class FXVolPanelInput(BaseModel):
    """Parameters the LLM extracts to assemble an FX vol panel.

    The 3 selectors (market_scope, tenor, smile_point) define the
    Panel slice. Each call returns a Panel for ONE (tenor,
    smile_point) — for multi-slice analysis, the caller composes
    multiple calls.
    """

    model_config = ConfigDict(extra="forbid")

    market_scope: FXVolPanelMarketScope = Field(
        ...,
        description=(
            "Subset of the FX vol universe. G10 / EM / G10_CROSSES "
            "map to fx_family filter; ALL skips filter."
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
    tenor: FXVolPanelTenor = Field(
        default="1M",
        description="Vol tenor. One of '1W', '1M', '3M', '6M', '12M'.",
    )
    smile_point: FXVolPanelSmilePoint = Field(
        default="ATM",
        description=(
            "Smile point. 'ATM' → instrument_type='fx_vol'; "
            "'25R'/'25B'/'10R'/'10B' → instrument_type='fx_vol_smile'."
        ),
    )
    missing_data_policy: Optional[str] = Field(
        default=None,
        description=(
            "How to handle NaN cells after forward-fill. None ⇒ "
            "config default ('forward_fill_only'). Allowed: "
            "['raise', 'forward_fill_only', 'drop_rows_any_missing']."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description=(
            "Bloomberg field. PX_LAST default; PX_BID/PX_ASK "
            "supported when ingested."
        ),
    )

    @model_validator(mode="after")
    def _date_range_sensible(self) -> "FXVolPanelInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} cannot be before start_date "
                f"{self.start_date}."
            )
        return self


class FXVolPanelOutput(BaseModel):
    """Top-level response carrying the typed Panel artifact.

    Mirror of ``FXPanelOutput`` shape with vol-specific selectors
    (tenor, smile_point) added. The MCP layer drops ``panel`` (the
    typed artifact) before serialising to JSON for the LLM.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    as_of_start: str
    as_of_end: str
    market_scope: str = Field(
        ...,
        description="Echo of the input market_scope for downstream lineage.",
    )
    tenor: str = Field(
        ...,
        description="Echo of the input tenor (e.g. '1M').",
    )
    smile_point: str = Field(
        ...,
        description="Echo of the input smile_point (e.g. 'ATM', '25R').",
    )
    columns: List[str] = Field(
        ...,
        description=(
            "Column names in the assembled Panel — one per pair, "
            "ordered alphabetically (e.g. ['EURUSD', 'GBPUSD', ...]). "
            "Pair names, NOT vendor tickers."
        ),
    )
    n_observations: int = Field(
        ...,
        description="Number of rows in the assembled Panel.",
    )
    n_pairs: int = Field(
        ...,
        description="Number of columns in the assembled Panel (== len(columns)).",
    )
    units_by_column: dict = Field(
        ...,
        description=(
            "Per-column unit tag — 'percent' for every FX vol column "
            "(closed-enum value from TimeSeriesUnits; 8.0 = 8% "
            "annualized vol)."
        ),
    )
    methodology_disclosures: List[str] = Field(
        default_factory=list,
        description=(
            "Inline disclosure block. Mirrors the V1 closed-family "
            "discipline (declare known V1 limitations explicitly)."
        ),
    )
    panel: Optional[Panel] = Field(
        default=None,
        description=(
            "The typed Panel artifact (rows = dates, columns = pair "
            "names, values = vol pct). Present when compute "
            "succeeded; the MCP layer drops this field before "
            "serialising for the LLM."
        ),
    )


__all__ = [
    "FXVolPanelInput",
    "FXVolPanelOutput",
    "FXVolPanelMarketScope",
    "FXVolPanelTenor",
    "FXVolPanelSmilePoint",
]

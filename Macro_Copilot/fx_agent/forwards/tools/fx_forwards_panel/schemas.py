"""Pydantic schemas for the calculate_fx_forwards_panel tool.

Phase F1 (2026-05-27).

Mirror of ``fx_agent.spot.tools.fx_panel`` schemas for the
fx_forward substrate. Assembles a typed ``Panel`` of forward
points (in pips) for a (market_scope, tenor) slice of the
forwards universe.

Codex Q2 lock: the output carries a real typed ``Panel``
artifact (Shape C). Not a dict, not a DataFrame.

Operationalises: P3 (canonical Panel contract), P5 (units
explicit — PRICE for pips), P11 (forwards primitives in
fx_agent/forwards/). PR1, PR4, PR5, PR7, PR10, PR12, PR13.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel


FXForwardsPanelMarketScope = Literal["G10", "EM", "ALL"]
FXForwardsPanelTenor = Literal["1W", "1M", "3M", "6M", "12M"]


class FXForwardsPanelInput(BaseModel):
    """Parameters the LLM extracts to assemble an FX forwards panel.

    market_scope restricted to G10 / EM / ALL — the forwards
    substrate has no G10_CROSSES fx_family (matches the fetcher's
    ``_SCOPE_TO_FX_FORWARDS_FAMILIES`` closed map).
    """

    model_config = ConfigDict(extra="forbid")

    market_scope: FXForwardsPanelMarketScope = Field(
        ...,
        description=(
            "Subset of the FX forwards universe. G10 / EM map "
            "to fx_family filter; ALL skips filter."
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
    tenor: FXForwardsPanelTenor = Field(
        default="1M",
        description="Forward tenor. One of '1W', '1M', '3M', '6M', '12M'.",
    )
    missing_data_policy: Optional[str] = Field(
        default=None,
        description=(
            "How to handle NaN cells after forward-fill. None ⇒ "
            "config default. Allowed: ['raise', 'forward_fill_only', "
            "'drop_rows_any_missing']."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description="Bloomberg field. PX_LAST default.",
    )

    @model_validator(mode="after")
    def _date_range_sensible(self) -> "FXForwardsPanelInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} cannot be before start_date "
                f"{self.start_date}."
            )
        return self


class FXForwardsPanelOutput(BaseModel):
    """Top-level response carrying the typed Panel artifact.

    Mirror of ``FXPanelOutput`` shape with the forwards-specific
    ``tenor`` selector added. The MCP layer drops ``panel`` (the
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
            "Per-column unit tag — 'price' for every FX forward "
            "points column (closed-enum from TimeSeriesUnits). "
            "Forward points are absolute pip quotes."
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
            "pair names, values = forward points in pips). The "
            "MCP layer drops this field before serialising for the LLM."
        ),
    )


__all__ = [
    "FXForwardsPanelInput",
    "FXForwardsPanelOutput",
    "FXForwardsPanelMarketScope",
    "FXForwardsPanelTenor",
]

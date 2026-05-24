"""Pydantic schemas for the calculate_fx_panel tool.

Phase B (2026-05-25).

This primitive assembles a typed cross-sectional ``Panel`` of FX spot
levels for a market_scope subset of the universe. It is the cornerstone
primitive that every later cross-sectional FX tool consumes (returns
series, correlation matrix, carry basket, factor decomposition, regime
classifier).

Codex Q2 lock 2026-05-24: the output carries a real typed ``Panel``
artifact via ``shared.artifacts.types.Panel`` (Shape C). Not a dict,
not a DataFrame, not an ad-hoc namedtuple.
"""

from __future__ import annotations

from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.types import Panel


class FXPanelInput(BaseModel):
    """Parameters the LLM extracts to assemble an FX spot panel.

    The market_scope filter pulls from the same ``instrument_master``
    rows the readiness gate validates, so the universe identity is
    guaranteed consistent across the data contract and this primitive.
    """

    model_config = ConfigDict(extra="forbid")

    market_scope: Literal["G10", "EM", "G10_CROSSES", "ALL"] = Field(
        ...,
        description=(
            "Subset of the FX spot universe to assemble. "
            "'G10' = 9 G10 majors (EURUSD, GBPUSD, USDJPY, AUDUSD, "
            "USDCAD, USDCHF, NZDUSD, USDNOK, USDSEK). "
            "'EM' = 9 EM majors (USDMXN, USDBRL, USDZAR, USDTRY, "
            "USDPLN, USDHUF, USDKRW, USDIDR, USDPHP). "
            "'G10_CROSSES' = the 11 G10 crosses from fx_crosses.yml. "
            "'ALL' = all fx_spot instruments in the DB regardless of "
            "scope (mostly useful for diagnostics)."
        ),
    )
    start_date: date = Field(
        ...,
        description="Earliest trade_date to include (inclusive).",
    )
    end_date: Optional[date] = Field(
        default=None,
        description=(
            "Latest trade_date to include (inclusive). None ⇒ include "
            "every observation up to the latest in the DB."
        ),
    )
    missing_data_policy: Optional[str] = Field(
        default=None,
        description=(
            "How to handle NaN cells after forward-fill. None ⇒ "
            "resolved from config.yaml. Allowed: "
            "['raise', 'forward_fill_only', 'drop_rows_any_missing']."
        ),
    )
    field_name: str = Field(
        default="PX_LAST",
        description=(
            "Bloomberg field on market_data_daily. Default 'PX_LAST'. "
            "Other FX-relevant fields (PX_BID / PX_ASK) work too as "
            "long as they have been ingested."
        ),
    )

    @model_validator(mode="after")
    def _date_range_sensible(self) -> "FXPanelInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} cannot be before start_date "
                f"{self.start_date}."
            )
        return self


class FXPanelOutput(BaseModel):
    """Top-level response carrying the typed Panel artifact.

    The MCP-facing wire shape carries the panel's metadata (column
    list, date range, observation count, per-column units) so the LLM
    gets a summary without paying the full per-row token cost; the
    MCP layer strips ``panel`` (the typed artifact) before serialising
    to JSON for the LLM.

    Mirrors ``SovereignYieldPanelOutput`` shape exactly (Phase B
    compliance check — re-use Sreeram's pattern, do not invent a
    parallel).
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    as_of_start: str
    as_of_end: str
    market_scope: str = Field(
        ...,
        description="Echo of the input market_scope for downstream lineage.",
    )
    columns: List[str] = Field(
        ...,
        description=(
            "Column names in the assembled Panel — one per pair, "
            "ordered alphabetically (e.g. ['EURUSD', 'GBPUSD', "
            "'USDJPY', ...]). Pair names, NOT vendor tickers."
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
            "Per-column unit tag — 'price' for every FX spot column "
            "(closed-enum value from TimeSeriesUnits)."
        ),
    )
    methodology_disclosures: List[str] = Field(
        default_factory=list,
        description=(
            "Inline disclosure block surfaced on the workspace "
            "methodology card. Mirrors the V1 closed-family discipline "
            "(declare known V1 limitations explicitly)."
        ),
    )
    panel: Optional[Panel] = Field(
        default=None,
        description=(
            "The typed Panel artifact (rows = dates, columns = "
            "pair names). Present when compute succeeded; the MCP "
            "layer drops this field before serialising for the LLM."
        ),
    )


__all__ = [
    "FXPanelInput",
    "FXPanelOutput",
]

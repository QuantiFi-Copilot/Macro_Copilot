"""Pydantic schemas for the sovereign_yield_panel tool.

Phase 1 PR 19.

The primitive's *Input shape is a list of ``(curve_family, tenor)``
leg specs (each optionally overriding the default field name) plus a
date range.  Closed family invariant: every curve_family must be in
the sovereign family set declared below.  OIS curve families are
explicitly rejected — a sibling ``ois_rate_panel`` primitive lives
in the OIS agent for that case.
"""

from __future__ import annotations

from datetime import date
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# Closed-enum sovereign family set.  Matches the universe declared in
# the manifesto's sovereign_bonds playbook + the supervisor's domain
# signals.  Adding a new sovereign family (e.g. SE_GOVT) is a one-line
# edit here + an ingestion playbook update.
_SOVEREIGN_FAMILIES = frozenset({
    "UST",
    "USD_TIPS",       # inflation-linkers — sovereign-family per ingestion
    "DE_BUND",
    "UK_GILT",
    "FR_OAT",
    "IT_BTP",
    "ES_BONO",
    "JGB",
    "CANADA_GOVT",
    "AU_GOVT",
})


class SovereignYieldPanelLegSpec(BaseModel):
    """One leg of the requested panel."""

    model_config = ConfigDict(extra="forbid")

    curve_family: str = Field(
        ...,
        description=(
            "Sovereign curve family.  Must be one of: "
            f"{sorted(_SOVEREIGN_FAMILIES)}.  OIS families are rejected — "
            "use the ois_rate_panel primitive in the OIS agent for those."
        ),
    )
    tenor: str = Field(
        ...,
        description="Tenor identifier (e.g. '2Y', '5Y', '10Y', '30Y').",
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field name.  None → resolved from config.yaml "
            "default (YLD_YTM_MID for sovereign benchmarks)."
        ),
    )

    @model_validator(mode="after")
    def _curve_family_must_be_sovereign(self) -> "SovereignYieldPanelLegSpec":
        if self.curve_family not in _SOVEREIGN_FAMILIES:
            raise ValueError(
                f"curve_family={self.curve_family!r} is not a recognised "
                f"sovereign family.  Allowed: {sorted(_SOVEREIGN_FAMILIES)}.  "
                "For OIS curves use ois_rate_panel in the OIS agent."
            )
        return self


class SovereignYieldPanelInput(BaseModel):
    """Parameters the LLM extracts to assemble a sovereign yield panel."""

    model_config = ConfigDict(extra="forbid")

    legs: List[SovereignYieldPanelLegSpec] = Field(
        ...,
        min_length=1,
        max_length=20,
        description=(
            "Ordered list of (curve_family, tenor[, field_name]) leg "
            "specs.  Column order in the output Panel matches this "
            "list.  Max 20 legs in V1 to keep token budgets sane."
        ),
    )
    start_date: date = Field(
        ...,
        description="Earliest trade_date to include (inclusive).",
    )
    end_date: Optional[date] = Field(
        default=None,
        description=(
            "Latest trade_date to include (inclusive).  None → "
            "include every observation up to the latest in the DB."
        ),
    )
    missing_data_policy: Optional[str] = Field(
        default=None,
        description=(
            "How to handle NaN cells after forward-fill.  None → "
            "resolved from config.yaml.  Allowed: "
            "['raise', 'forward_fill_only', 'drop_rows_any_missing']."
        ),
    )

    @model_validator(mode="after")
    def _date_range_sensible(self) -> "SovereignYieldPanelInput":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError(
                f"end_date {self.end_date} cannot be before start_date "
                f"{self.start_date}."
            )
        # Enforce unique (curve_family, tenor) pairs — duplicate legs
        # would produce duplicate Panel columns which is a bug.
        seen = set()
        for leg in self.legs:
            key = (leg.curve_family, leg.tenor)
            if key in seen:
                raise ValueError(
                    f"Duplicate leg spec ({leg.curve_family}, {leg.tenor}); "
                    "every leg must be unique."
                )
            seen.add(key)
        return self


class SovereignYieldPanelOutput(BaseModel):
    """Top-level response.

    The MCP-facing wire shape carries the panel's metadata (column
    list, date range, observation count) so the LLM gets a summary
    without paying the full per-row token cost.  The full ``Panel``
    artifact is persisted via the closed-family artifact store; the
    workflow executor consumes it from there.
    """

    model_config = ConfigDict(extra="forbid")

    as_of_start: str
    as_of_end: str
    columns: List[str] = Field(
        ...,
        description="Column names in the assembled Panel (one per leg).",
    )
    n_observations: int = Field(
        ...,
        description="Number of rows in the assembled Panel.",
    )
    units_by_column: dict = Field(
        ...,
        description=(
            "Per-column unit tag (e.g. 'PERCENT').  Closed-enum "
            "values from ``TimeSeriesUnits``."
        ),
    )
    methodology_disclosures: List[str] = Field(
        default_factory=list,
        description=(
            "Inline disclosure block surfaced on the workspace "
            "methodology card.  Mirrors the V1 closed-family "
            "discipline (declare known V1 limitations explicitly)."
        ),
    )


__all__ = [
    "SovereignYieldPanelInput",
    "SovereignYieldPanelLegSpec",
    "SovereignYieldPanelOutput",
]

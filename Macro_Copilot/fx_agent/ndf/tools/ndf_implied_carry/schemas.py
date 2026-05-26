"""Pydantic schemas for the FX NDF implied carry tool.

Cross-sectional implied carry over the NDF universe at a single tenor.
Implied carry is derived from the NDF outright vs underlying spot:

    implied_carry_annualized_pct
        = (outright / spot - 1) * (annualization_days / tenor_days) * 100

Per-date join on trade_date (no lookahead). Outputs ranked rows mirroring
the fx_carry scanner shape so the LLM has a consistent contract across
deliverable-forward carry and NDF carry.

This module serves BOTH MCP surfaces:
  - calculate_fx_ndf_implied_carry_tool: returns every NDF in scope.
  - scan_fx_ndf_carry_tool: same compute, exposed with curated scanner
    defaults (top_n=5, rank_by='abs_z_score').
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from fx_agent.ndf._shared import NDFTenor


# How to rank the output rows. Mirror of FXCarryRankBy semantics so a
# cross-asset reader can compare deliverable-forward carry and NDF carry
# under the same rank_by convention.
FXNDFCarryRankBy = Literal["carry_signed", "abs_carry", "abs_z_score"]

# spot_convention: which spot pair to use as the denominator of the
# implied carry formula.
#
# - "settlement" (default): use the NDF's underlying settlement
#   reference. For CCN+ this is USDCNY (PBOC fix). For IRN+ it is
#   USDINR composite. This is the textbook implied-carry definition.
# - "offshore_tradable": use the offshore tradable proxy where it
#   exists. For CCN+ this swaps USDCNY -> USDCNH. For other pairs
#   that have no offshore proxy in the substrate (IRN+, BCN+, KWN+,
#   IHN+, NTN+), the tool falls back to the settlement reference for
#   that pair so output coverage stays uniform. The offshore mode
#   exposes the onshore-vs-offshore basis as a side-effect of the
#   carry differential (useful for CCN+ specifically).
FXNDFSpotConvention = Literal["settlement", "offshore_tradable"]


class FXNDFImpliedCarryInput(BaseModel):
    """Parameters for the FX NDF implied carry tool."""

    tenor: NDFTenor = Field(
        default="1M",
        description=(
            "NDF tenor for the carry calculation. One of '1W', '1M', "
            "'3M', '6M', '12M'. Default '1M' matches the deliverable-"
            "forwards fx_carry convention."
        ),
    )
    spot_convention: FXNDFSpotConvention = Field(
        default="settlement",
        description=(
            "Which underlying spot to use as the carry denominator. "
            "'settlement' (default) = NDF's official settlement reference "
            "(textbook implied carry — e.g. USDCNY for CCN+). "
            "'offshore_tradable' = offshore proxy where available "
            "(USDCNH for CCN+); other NDFs fall back to settlement. "
            "Switch to 'offshore_tradable' to surface the onshore-vs-"
            "offshore basis differential for CCN+ specifically."
        ),
    )
    rank_by: FXNDFCarryRankBy = Field(
        default="carry_signed",
        description=(
            "How to order the output rows. 'carry_signed' (default) "
            "ranks descending by signed implied carry. 'abs_carry' "
            "ranks by absolute carry magnitude. 'abs_z_score' ranks "
            "by absolute z-score of the historical carry series "
            "(extremeness vs own history — useful for scanner mode)."
        ),
    )
    top_n: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "Truncate to top-N rows after sorting. None (default) "
            "returns every NDF in scope (currently 6 families)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched to build the per-NDF "
            "implied-carry series used by the z-score / percentile / "
            "range calculations. Does NOT control the rolling 252-day "
            "z-score window itself (locked in config)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override applied to BOTH the spot and NDF "
            "legs. None (default) falls through to config defaults "
            "(PX_LAST). Use 'PX_BID' / 'PX_ASK' to compute carry on "
            "the bid / ask side respectively."
        ),
    )


class FXNDFImpliedCarryRow(BaseModel):
    ndf_code: str
    underlying_pair: str
    tenor: str
    spot_date: str
    forward_date: str
    spot_convention_used: str = Field(
        ...,
        description=(
            "Resolved spot_convention for THIS row. May differ from "
            "the input when 'offshore_tradable' is requested but the "
            "NDF has no offshore proxy — in that case this field will "
            "say 'settlement_fallback' so the row's carry remains "
            "interpretable."
        ),
    )
    spot: float
    outright: float
    implied_carry_annualized_pct: float
    carry_signal: str
    carry_z_score: Optional[float] = None
    carry_percentile_252d: Optional[float] = None
    carry_high_252d: Optional[float] = None
    carry_low_252d: Optional[float] = None
    carry_observation_count: int
    rank: int


class FXNDFImpliedCarryOutput(BaseModel):
    tenor: str
    spot_convention: str
    rows: list[FXNDFImpliedCarryRow]

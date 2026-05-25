"""Pydantic input/output schemas for the FX carry tool.

The carry tool is the cross-sectional snapshot primitive: for one
forward tenor, it returns one row per pair in the substrate with
spot, forward points, outright forward, carry-in-bps-of-spot, and
annualised carry %. From Phase A step 6 onwards it also functions as
a carry scanner — the rows can be sorted by signed carry, absolute
carry, or absolute carry z-score, and capped to the top-N — and
each row carries a rolling 252-day z-score / percentile / range on
its OWN annualised-carry historical series.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# Closed set of supported forward tenors. Mirrors
# ``fx_agent/forwards/_shared.py::SUPPORTED_FORWARD_TENORS`` exactly.
# Adding a new tenor = update _shared.py, the Literal here, the
# Literal in forward_curve/schemas.py, and the ``tenor_<n>_days``
# convention in every tool's config.yaml.
FXCarryTenor = Literal["1W", "1M", "3M", "6M", "12M"]


# How to rank the output rows when the tool is used as a scanner.
# All defaults remain backward-compatible: with ``rank_by="carry_signed"``
# and ``top_n=None`` the output is identical to the pre-scanner shape.
FXCarryRankBy = Literal["carry_signed", "abs_carry", "abs_z_score"]


# Closed set of supported market_scope values for the carry universe.
# - "G10" (default, preserves Phase A behaviour): G10 majors only, via
#   forwards filter fx_family='G10_FORWARDS'.
# - "EM": EM deliverable forwards only (fx_family='EM_FORWARDS'). Does
#   NOT include EM_NDF (NDFs are outright not points — separate compute
#   path, will be covered by Phase D's calculate_ndf_implied_carry).
# - "ALL": G10 + EM deliverable. Still excludes NDFs (same reason).
# Added in Phase B BBG batch 2026-05-25 to make Codex's "EM carry as
# explicit B+ decision" rule enforceable in the schema, not just in
# the SQL.
FXCarryMarketScope = Literal["G10", "EM", "ALL"]


class FXCarryInput(BaseModel):
    """Parameters for FX carry analytics."""

    tenor: FXCarryTenor = Field(
        default="1M",
        description=(
            "Forward tenor used for the carry calculation. Must be one "
            "of: 1W, 1M, 3M, 6M, 12M. Default 1M. The annualisation "
            "factor reads the corresponding ``tenor_<n>_days`` from "
            "the tool config — see config.yaml."
        ),
    )
    market_scope: FXCarryMarketScope = Field(
        default="G10",
        description=(
            "Universe filter. 'G10' (default) = 6 G10 majors only "
            "(EUR/GBP/JPY/AUD/CAD/CHF — Phase A behaviour preserved). "
            "'EM' = 6 EM deliverable forwards "
            "(MXN/ZAR/TRY/PLN/HUF/PHP — BBG batch 2026-05-25). 'ALL' = "
            "G10 + EM deliverable = 12 pairs at any given tenor. NDFs "
            "(BRL/KRW/IDR/CNY/INR) are NOT included in any market_scope "
            "value — NDFs are quoted outright (not points), so their "
            "carry compute path is separate and will land in Phase D as "
            "calculate_ndf_implied_carry."
        ),
    )
    rank_by: FXCarryRankBy = Field(
        default="carry_signed",
        description=(
            "How to order the output rows. ``carry_signed`` ranks "
            "descending by ``carry_annualized_pct`` (default — matches "
            "the pre-scanner behaviour). ``abs_carry`` ranks descending "
            "by absolute carry magnitude. ``abs_z_score`` ranks "
            "descending by absolute ``carry_z_score`` magnitude (rows "
            "with a None z-score are placed at the end)."
        ),
    )
    top_n: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "When set, truncate the output to the top-N rows after "
            "sorting. None (default) returns every pair in the "
            "substrate."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched from the DB to build the "
            "per-pair carry-annualised-pct series used by the z-score "
            "/ percentile / range calculations. Does NOT control the "
            "rolling z-score window (fixed at ``z_score_window_days`` "
            "in config.yaml, currently 252) nor the trailing range "
            "window (fixed at ``trailing_range_window_days``, locked "
            "at 252 in V1)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg observation field for BOTH the spot and forward "
            "points. When None (default), falls through to the YAML's "
            "``default_fx_spot_field`` / ``default_fx_forward_field`` "
            "(both PX_LAST)."
        ),
    )


class FXCarryRow(BaseModel):
    pair: str
    spot_date: str
    forward_date: str
    spot: float
    tenor: str
    forward_points: float
    forward_points_spot_units: float
    outright_forward: float
    carry_bps_spot: float
    carry_annualized_pct: float
    carry_signal: str
    # Scanner / z-score fields. Optional so the schema stays
    # backward-compatible when a pair / tenor combination has too
    # little history to support a z-score (see
    # z_score_min_periods in the tool config).
    carry_z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling z-score of the carry_annualized_pct series over "
            "the trailing z_score_window_days. None when the series "
            "has fewer than z_score_min_periods observations."
        ),
    )
    carry_percentile_252d: Optional[float] = Field(
        None,
        description=(
            "Percentile rank (0-100) of the current carry within the "
            "trailing 252-day series."
        ),
    )
    carry_high_252d: Optional[float] = Field(
        None,
        description="Highest carry_annualized_pct over the trailing 252 days.",
    )
    carry_low_252d: Optional[float] = Field(
        None,
        description="Lowest carry_annualized_pct over the trailing 252 days.",
    )
    carry_observation_count: int = Field(
        ...,
        description=(
            "Number of trading-day observations available for this "
            "pair / tenor in the lookback window after ffill cleaning. "
            "Useful for spotting sparse history that may inflate the "
            "z-score."
        ),
    )
    rank: int = Field(
        ...,
        description=(
            "1-indexed rank of this row in the ordered output. "
            "Determined by the ``rank_by`` input parameter."
        ),
    )


class FXCarryOutput(BaseModel):
    tenor: str
    rows: list[FXCarryRow]

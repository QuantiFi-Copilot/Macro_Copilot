"""Pydantic schemas for the FX vol smile snapshot tool.

The smile snapshot aggregates the full 5-point implied-vol smile for
one (pair, tenor): ATM (from fx_vol substrate) plus the four smile
differentials (25R, 25B, 10R, 10B) from fx_vol_smile substrate.

Each point carries the same snapshot + rolling 252-day stats as the
single-point risk_reversal / butterfly primitives. This tool is the
aggregate analog — it lets a caller pull the entire smile in one call
instead of N round-trips, and exposes the canonical smile shape (P1
canonical name; P4 desk-recognised structure).

PR8 note on methodology knob: vol_smile is a STRUCTURAL aggregate
primitive — its methodology is fully determined by the 5-point smile
shape. No single-knob central choice applies (per PR4 parsimony we
do NOT introduce a fake knob like `include_extended_tenors` — Codex
correction 2026-05-27 — since wing inclusion is a data shape question
not a methodology choice). The instrument selectors (pair, tenor)
fully define what the tool computes.

Operationalises: P3 (canonical smile contract; mirrors single-point
primitives), P5 (honest disclosure — vol-pt unit + smile-point map
in description), P11 (vol primitives live in fx_agent/vol/).
PR1 (concept-named), PR4 (parsimony — no fake knob), PR5 (new
concept), PR7 (YAML defaults), PR10 (provenance via output fields),
PR12 (registered source tags), PR13 (cross-config consistency),
PR16 (3-test triplet).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from fx_agent.vol._shared import FXVolStandardTenor


class FXSmilePointMetrics(BaseModel):
    """Per-smile-point snapshot block. Same shape across all 5 points."""

    smile_point: str = Field(
        ...,
        description="Smile point identifier: 'ATM', '25R', '25B', '10R', '10B'.",
    )
    vendor_ticker: str
    as_of_date: str = Field(
        ...,
        description="Per-series latest trade_date; the top-level as_of_date is the MIN.",
    )
    current_vol_pts: float = Field(
        ...,
        description=(
            "Current vol observation for this smile point in ABSOLUTE vol "
            "points. ATM is a vol LEVEL; 25R/25B/10R/10B are DIFFERENTIALS "
            "(per Bloomberg convention)."
        ),
    )
    daily_change_vol_pts: Optional[float] = Field(
        None, description="1 trading-day absolute change in vol points."
    )
    weekly_change_vol_pts: Optional[float] = Field(
        None, description="5 trading-day absolute change in vol points."
    )
    monthly_change_vol_pts: Optional[float] = Field(
        None, description="21 trading-day absolute change in vol points."
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-day z-score on this series. None when fewer than "
            "z_score_min_periods observations."
        ),
    )
    high_252d: Optional[float] = Field(None)
    low_252d: Optional[float] = Field(None)
    percentile_252d: Optional[float] = Field(None)
    observation_count: int


class FXVolSmileInput(BaseModel):
    """Per-query parameters for the FX vol smile aggregate snapshot.

    No central methodology knob — the smile shape is fully determined
    by (pair, tenor). pair / tenor are instrument selectors;
    lookback_days controls fetch window only; field_name is the
    standard PX_LAST/PX_BID/PX_ASK sentinel.
    """

    pair: str = Field(
        ...,
        description=(
            "FX pair, e.g. 'EURUSD', 'USDMXN'. Must have ATM + all 4 smile "
            "points (25R, 25B, 10R, 10B) ingested for the requested tenor."
        ),
    )
    tenor: FXVolStandardTenor = Field(
        default="1M",
        description=(
            "Smile tenor. One of '1W', '1M', '3M', '6M', '12M' (standard "
            "strip). Tenor 12M aliases to BBG '1Y' suffix internally."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched. Does NOT control the rolling "
            "252-day window (YAML-locked)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override. None falls through to PX_LAST. "
            "PX_BID / PX_ASK available for the 156 std-tenor smile "
            "instruments ingested in PR #228."
        ),
    )


class FXVolSmileMetrics(BaseModel):
    """Aggregate 5-point smile snapshot for one (pair, tenor)."""

    as_of_date: str = Field(
        ...,
        description=(
            "Common as_of_date = MIN across all 5 per-point latest dates. "
            "Conservative alignment so cross-point comparisons are honest."
        ),
    )
    pair: str
    tenor: str
    atm: FXSmilePointMetrics
    rr_25: FXSmilePointMetrics
    bf_25: FXSmilePointMetrics
    rr_10: FXSmilePointMetrics
    bf_10: FXSmilePointMetrics


class FXVolSmileOutput(BaseModel):
    current_metrics: FXVolSmileMetrics

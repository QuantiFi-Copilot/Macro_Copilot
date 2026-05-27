"""Pydantic schemas for the FX risk reversal snapshot tool.

A risk reversal (RR) is the implied-vol differential between the
out-of-the-money call and put at a given delta — a desk-recognized
skew indicator (PR5). Positive RR = call skew dominant (market pricing
upside risk); negative RR = put skew (downside risk). Quoted in vol
points.

Single-(pair, delta_anchor, tenor) snapshot primitive with rolling
252-day z-score / percentile / range. Analog of get_fx_atm_vol_level
for the smile substrate. Central knob (PR8) = delta_anchor ∈ {25, 10}.

Operationalises: P3 (consistency by contract — mirrors atm_vol_level
shape), P5 (honest disclosure — vol-point unit + skew sign convention
in description), P11 (vol primitives live in fx_agent/vol/).
PR1 (concept-named), PR4 (parsimony), PR5 (new concept), PR7 (YAML
defaults), PR8 (single knob = delta_anchor), PR10 (provenance via
output fields), PR12 (registered source tags), PR13 (cross-config
consistency), PR16 (3-test triplet).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from fx_agent.vol._shared import FXSmileDelta, FXVolStandardTenor


class FXRiskReversalInput(BaseModel):
    """Per-query parameters for the FX risk reversal snapshot.

    Per PR8 single-knob discipline: ``delta_anchor`` is the ONLY
    methodology knob (defines what the RR IS — 25Δ skew vs 10Δ skew
    are structurally different). pair / tenor are instrument
    selectors; lookback_days controls fetch window only;
    field_name is the standard PX_LAST/PX_BID/PX_ASK sentinel.
    """

    pair: str = Field(
        ...,
        description=(
            "FX pair, e.g. 'EURUSD', 'USDMXN'. Must have a "
            "fx_vol_smile entry for the requested delta_anchor × tenor."
        ),
    )
    delta_anchor: FXSmileDelta = Field(
        default=25,
        description=(
            "Delta anchor for the risk reversal. 25 = standard 25Δ RR "
            "(call_25Δ - put_25Δ vol differential). 10 = wing-side 10Δ "
            "RR. Default 25 — desk-standard skew monitor delta. This "
            "is the SINGLE central methodology knob (PR8)."
        ),
    )
    tenor: FXVolStandardTenor = Field(
        default="1M",
        description=(
            "Smile tenor. One of '1W', '1M', '3M', '6M', '12M' "
            "(standard strip). Tenor 1Y on BBG aliases to internal "
            "'12M' via shared smile_vendor_ticker."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched. Does NOT control the "
            "rolling 252-day window (YAML-locked)."
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


class FXRiskReversalMetrics(BaseModel):
    as_of_date: str
    pair: str
    delta_anchor: int
    smile_point: str = Field(
        ...,
        description="Resolved smile_point identifier ('25R' or '10R').",
    )
    tenor: str
    vendor_ticker: str
    current_risk_reversal_vol_pts: float = Field(
        ...,
        description=(
            "Current RR in vol points (positive = call skew dominant; "
            "negative = put skew dominant). Quoted in absolute vol "
            "points, NOT in % of spot or % of ATM."
        ),
    )
    daily_change_vol_pts: Optional[float] = Field(
        None, description="1 trading-day absolute change in RR vol points."
    )
    weekly_change_vol_pts: Optional[float] = Field(
        None, description="5 trading-day absolute change in RR vol points."
    )
    monthly_change_vol_pts: Optional[float] = Field(
        None, description="21 trading-day absolute change in RR vol points."
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-day z-score. None when the series has fewer "
            "than z_score_min_periods observations."
        ),
    )
    high_252d: Optional[float] = Field(None)
    low_252d: Optional[float] = Field(None)
    percentile_252d: Optional[float] = Field(None)
    observation_count: int


class FXRiskReversalOutput(BaseModel):
    current_metrics: FXRiskReversalMetrics

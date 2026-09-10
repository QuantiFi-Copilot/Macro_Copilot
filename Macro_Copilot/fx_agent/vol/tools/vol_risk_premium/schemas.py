"""Pydantic schemas for the FX vol risk premium snapshot tool.

The vol risk premium (VRP) is the differential between ATM implied
vol and realized vol over a matched horizon — the canonical vol-
carry concept. Positive VRP = implied rich vs realized (vol-seller
positive carry); negative VRP = implied cheap (vol-buyer positive
carry).

Single-(pair, tenor) snapshot primitive combining the fx_vol implied
substrate with realized vol computed from the fx_spot substrate.
Central knob (PR8) = realized_window_basis ∈ {tenor_matched, fixed_30d}.

Operationalises: P3 (canonical vol-carry contract), P5 (honest
disclosure — vol-point unit + premium sign convention in the
description), P11 (vol primitives live in fx_agent/vol/).
PR1 (concept-named), PR4 (parsimony), PR5 (new concept), PR7 (YAML
defaults), PR8 (single knob = realized_window_basis), PR10 (provenance
via output fields), PR12 (registered source tags), PR13 (cross-
config consistency), PR16 (3-test triplet).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from fx_agent.vol._shared import FXVolStandardTenor


FXRealizedWindowBasis = Literal["tenor_matched", "fixed_30d"]


class FXVolRiskPremiumInput(BaseModel):
    """Per-query parameters for the FX vol risk premium snapshot.

    Per PR8: ``realized_window_basis`` is the SINGLE central
    methodology knob — it defines HOW the realized horizon aligns
    with the implied tenor. pair / tenor are instrument selectors;
    lookback_days controls fetch window only; field_name is the
    standard PX_LAST/PX_BID/PX_ASK sentinel.
    """

    pair: str = Field(
        ...,
        description=(
            "FX pair, e.g. 'EURUSD', 'USDMXN'. Must have BOTH an "
            "fx_vol entry at the requested tenor AND an fx_spot history "
            "(needed for realized vol)."
        ),
    )
    tenor: FXVolStandardTenor = Field(
        default="1M",
        description=(
            "ATM vol tenor. One of '1W', '1M', '3M', '6M', '12M'. The "
            "realized-vol window is derived from tenor when "
            "realized_window_basis='tenor_matched' (e.g. 1M → 21 trading "
            "days, 3M → 63, ...)."
        ),
    )
    realized_window_basis: FXRealizedWindowBasis = Field(
        default="tenor_matched",
        description=(
            "Realized-vol window alignment. 'tenor_matched' (DEFAULT) "
            "uses N = TENOR_TO_TRADING_DAYS[tenor] — the methodologically "
            "clean convention (matches the implied horizon). 'fixed_30d' "
            "uses 30 trading days regardless of tenor, useful for cross-"
            "tenor comparability of the realized leg. The SINGLE central "
            "methodology knob (PR8)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched. Does NOT control the "
            "rolling 252-day window on the PREMIUM series (YAML-locked)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override for BOTH implied and spot legs. "
            "None falls through to PX_LAST."
        ),
    )


class FXVolRiskPremiumMetrics(BaseModel):
    as_of_date: str
    pair: str
    tenor: str
    realized_window_basis: str
    realized_window_days: int = Field(
        ...,
        description="Resolved trading-day window for the realized vol leg.",
    )
    vendor_ticker_implied: str = Field(
        ...,
        description="BBG ticker for the ATM implied vol observation.",
    )
    current_implied_vol_pct: float = Field(
        ...,
        description="Latest ATM implied vol in PERCENT (e.g. 8.0 = 8% / yr).",
    )
    current_realized_vol_pct: float = Field(
        ...,
        description=(
            "Latest realized vol at the resolved window in PERCENT. "
            "Computed from log-returns: std * sqrt(252) * 100."
        ),
    )
    current_vol_risk_premium_vol_pts: float = Field(
        ...,
        description=(
            "Latest VRP in ABSOLUTE vol points, defined exactly as "
            "VRP = current_implied_vol_pct - current_realized_vol_pct. "
            "Positive = implied vol > realized vol (implied is rich vs "
            "the recent realized path). Negative = implied < realized. "
            "POSITIONING SIGNAL ONLY — VRP is the implied-vs-realized "
            "differential, NOT a P&L estimate. Net P&L of any vol-"
            "seller / vol-buyer trade depends on execution price, bid-"
            "ask, hedging path / gamma exposure, and forward realized "
            "path — none of which this primitive estimates."
        ),
    )
    daily_change_vol_pts: Optional[float] = Field(
        None, description="1 trading-day absolute change in VRP vol points."
    )
    weekly_change_vol_pts: Optional[float] = Field(
        None, description="5 trading-day absolute change in VRP vol points."
    )
    monthly_change_vol_pts: Optional[float] = Field(
        None, description="21 trading-day absolute change in VRP vol points."
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-day z-score on the VRP series. None when the "
            "series has fewer than z_score_min_periods observations."
        ),
    )
    high_252d: Optional[float] = Field(None)
    low_252d: Optional[float] = Field(None)
    percentile_252d: Optional[float] = Field(None)
    observation_count: int = Field(
        ...,
        description=(
            "Count of dates where BOTH implied and realized had a valid "
            "value (post-realized-vol-warmup intersection)."
        ),
    )


class FXVolRiskPremiumOutput(BaseModel):
    current_metrics: FXVolRiskPremiumMetrics

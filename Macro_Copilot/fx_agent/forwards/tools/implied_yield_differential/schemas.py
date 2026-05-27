"""Pydantic schemas for the FX implied yield differential snapshot tool.

The implied yield differential is the rate spread between the LOCAL
currency and USD implied from forward points via Covered Interest
Parity (CIP). For a pair like USDMXN it answers "what MXN-USD rate
spread is the FX forward market pricing?"; for EURUSD it answers
"what EUR-USD spread is priced?".

Single-(pair, tenor) snapshot primitive. PM-friendly framing of the
same math fx_carry uses internally (carry_annualized_pct = quote -
base rate by CIP), but isolated to one (pair, tenor) with rolling
252-day z-score / percentile / range on the differential series.

PR8 note: NO central methodology knob. The convention is HARD-LOCKED
to local_minus_usd — sign-flipping is a presentation choice, not a
methodology choice (per PR4 parsimony, match vol_smile precedent
where no fake knob was introduced). For non-USD G10 crosses (EURJPY,
GBPCHF, etc.) the tool fails-loud at compute time since "local vs
USD" isn't defined; those pairs need a future quote-aware variant.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# Reuse the SUPPORTED_FORWARD_TENORS Literal idiom from fx_carry / forward_curve.
FXImpliedYieldDifferentialTenor = Literal["1W", "1M", "3M", "6M", "12M"]


class FXImpliedYieldDifferentialInput(BaseModel):
    """Per-query parameters for the FX implied yield differential.

    Per PR4 parsimony: NO central methodology knob. The
    local_minus_usd convention is hard-locked. pair / tenor are
    instrument selectors; lookback_days controls fetch window only;
    field_name is the standard PX_LAST/PX_BID/PX_ASK sentinel.

    Pair MUST contain a USD leg (EURUSD, USDMXN, USDJPY, ...) — the
    tool fails loud on non-USD crosses (EURJPY, GBPCHF, ...) for V1
    since "local minus USD" is undefined when no leg is USD.
    """

    pair: str = Field(
        ...,
        description=(
            "FX pair, must contain a USD leg (V1 limitation). "
            "Examples: 'EURUSD' (USD is quote, local=EUR), "
            "'USDMXN' (USD is base, local=MXN). G10 crosses like "
            "'EURJPY' fail-loud at compute time."
        ),
    )
    tenor: FXImpliedYieldDifferentialTenor = Field(
        default="1M",
        description="Forward tenor. One of '1W', '1M', '3M', '6M', '12M'.",
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched. Does NOT control the "
            "rolling 252-day window on the differential series (YAML-locked)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override for BOTH spot and forward legs. "
            "None falls through to PX_LAST."
        ),
    )


class FXImpliedYieldDifferentialMetrics(BaseModel):
    as_of_date: str
    pair: str
    tenor: str
    tenor_days: int = Field(
        ...,
        description="Resolved trading-day count for the tenor (1M=21, ...).",
    )
    usd_leg_position: str = Field(
        ...,
        description=(
            "Which side USD is on in the pair quote: 'base' for USDxxx "
            "(USD is base, local is quote); 'quote' for xxxUSD (USD is "
            "quote, local is base). Determines the sign convention "
            "applied to the raw CIP differential."
        ),
    )
    local_currency: str = Field(
        ...,
        description=(
            "The non-USD leg of the pair (3-letter ISO). EURUSD → EUR; "
            "USDMXN → MXN."
        ),
    )
    current_spot: float
    current_forward_points: float = Field(
        ...,
        description="Raw forward points (Bloomberg quote, pre-divisor).",
    )
    current_forward_points_spot_units: float = Field(
        ...,
        description="Forward points converted to spot units (JPY-aware divisor).",
    )
    current_outright_forward: float = Field(
        ...,
        description="spot + forward_points_spot_units.",
    )
    current_implied_yield_differential_pct: float = Field(
        ...,
        description=(
            "Implied LOCAL minus USD rate differential, annualized, in "
            "PERCENT. POSITIVE = local rate > USD rate (e.g. USDMXN "
            "positive = MXN rate exceeds USD rate, the canonical EM "
            "carry case). NEGATIVE = local rate < USD rate (e.g. USDJPY "
            "negative = JPY rate below USD rate). Computed per CIP from "
            "(F/S - 1) * (annualization_days / tenor_days) * 100 with "
            "the sign adjusted by usd_leg_position so the output is "
            "always local-minus-USD regardless of quote convention."
        ),
    )
    daily_change_pct: Optional[float] = Field(
        None, description="1 trading-day absolute change in the differential."
    )
    weekly_change_pct: Optional[float] = Field(
        None, description="5 trading-day absolute change in the differential."
    )
    monthly_change_pct: Optional[float] = Field(
        None, description="21 trading-day absolute change in the differential."
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-day z-score on the differential series. None "
            "when the series has fewer than z_score_min_periods obs."
        ),
    )
    high_252d: Optional[float] = None
    low_252d: Optional[float] = None
    percentile_252d: Optional[float] = None
    observation_count: int


class FXImpliedYieldDifferentialOutput(BaseModel):
    current_metrics: FXImpliedYieldDifferentialMetrics

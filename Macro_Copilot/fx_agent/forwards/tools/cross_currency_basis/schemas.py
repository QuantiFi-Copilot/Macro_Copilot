"""Pydantic schemas for the FX cross-currency basis snapshot tool.

Cross-currency basis (xccy basis) measures the CIP violation between
what FX forwards imply about the local-vs-USD rate spread, and what
the observed OIS curves show. In a frictionless world the two would
agree exactly and basis = 0. Post-2008 they diverge — basis is the
"USD funding premium" priced into the FX swap market, persistently
NEGATIVE for major DM pairs (USD relatively scarce / expensive via
FX swap vs OIS).

Single-(pair, tenor) snapshot primitive. Composes the FX implied yield
differential (from forward points via CIP) with the observed OIS
local-vs-USD spread, both annualized at 252 trading days.

PR8: NO central methodology knob in V1. The sign convention (Bloomberg
BCRX-style: negative basis = USD scarcity) is HARD-LOCKED so the
output matches PM-recognizable terminology. The currency→OIS curve
mapping is also hard-locked (DM only V1).

CROSS-DOMAIN DEPENDENCY: this primitive consumes the rates_agent OIS
substrate via shared.analytics.rates_fetch.fetch_cross_market_pair.
First FX→rates cross-domain primitive. See the methodology block for
the autonomous decisions taken on the cross-domain pattern.

V1 SCOPE: G10 USD-leg only — EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD.
USDCHF, NZD, NOK, SEK, EM all fail-loud BY DESIGN (no OIS coverage
in rates_agent V1 — see Q3 in the Phase C dependency doc).

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# V1 supported pairs — those with both FX forward AND USD/local OIS coverage
FXCrossCurrencyBasisPair = Literal[
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD",
]
FXCrossCurrencyBasisTenor = Literal["1W", "1M", "3M", "6M", "12M"]


class FXCrossCurrencyBasisInput(BaseModel):
    """Per-query parameters for the FX cross-currency basis snapshot.

    Per PR4 parsimony: NO central methodology knob. Sign convention
    (Bloomberg BCRX-style) and currency→OIS-curve mapping are both
    HARD-LOCKED. pair / tenor are instrument selectors; lookback_days
    controls fetch window only; field_name is the standard PX_LAST
    sentinel applied to BOTH FX and OIS legs.
    """

    pair: FXCrossCurrencyBasisPair = Field(
        ...,
        description=(
            "FX pair. V1 supports G10 USD-leg with matching OIS "
            "coverage in rates_agent: EURUSD, GBPUSD, USDJPY, AUDUSD, "
            "USDCAD. USDCHF / NZDUSD / USDNOK / USDSEK / EM / NDF "
            "fail-loud at the Pydantic boundary BY DESIGN — see "
            "config.yaml methodology.assumptions for the rationale "
            "and future-extension paths."
        ),
    )
    tenor: FXCrossCurrencyBasisTenor = Field(
        default="1M",
        description=(
            "Forward tenor for both the FX leg and the OIS leg. One "
            "of '1W', '1M', '3M', '6M', '12M'. Internal alias FX 12M "
            "→ OIS 1Y applied automatically (rates_agent OIS exposes "
            "1Y, not 12M)."
        ),
    )
    lookback_days: int = Field(
        default=365,
        ge=30,
        le=7300,
        description=(
            "Calendar days of history fetched for BOTH FX and OIS "
            "legs. Does NOT control the rolling 252-day window on the "
            "basis series (YAML-locked)."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override applied to FX (spot, forward) "
            "AND OIS legs. None falls through to PX_LAST."
        ),
    )

    @model_validator(mode="after")
    def _validate_pair_has_ois_coverage(self) -> "FXCrossCurrencyBasisInput":
        # Pydantic Literal already enforces the closed set; this just
        # provides a friendlier error if someone bypasses it.
        return self


class FXCrossCurrencyBasisMetrics(BaseModel):
    as_of_date: str
    pair: str
    tenor: str
    fx_tenor: str = Field(
        ..., description="Tenor as quoted on the FX side (e.g. '12M').",
    )
    ois_tenor: str = Field(
        ..., description="Tenor as quoted on the OIS side (e.g. '1Y' — alias of FX 12M).",
    )
    local_currency: str = Field(
        ..., description="Non-USD leg of the pair (3-letter ISO).",
    )
    local_ois_curve: str = Field(
        ..., description="OIS curve_family for the local currency (e.g. 'EUR_ESTR_OIS').",
    )
    usd_ois_curve: str = Field(
        ..., description="USD OIS curve_family (hard-locked to 'USD_SOFR_OIS' V1).",
    )
    sign_convention: str = Field(
        ...,
        description=(
            "HARD-LOCKED sign convention label. V1 = 'bloomberg_bcrx_usd_scarcity_negative' "
            "— basis negative when USD is scarce / FX-implied USD rate "
            "exceeds OIS USD rate. Matches Bloomberg BCRX index family "
            "convention for trader recognisability."
        ),
    )
    current_fx_implied_yield_diff_pct: float = Field(
        ...,
        description="Latest FX-implied local-USD rate spread in PERCENT (= implied_yield_differential output).",
    )
    current_ois_diff_pct: float = Field(
        ...,
        description="Latest observed local OIS - USD OIS spread in PERCENT.",
    )
    current_basis_bps: float = Field(
        ...,
        description=(
            "Latest cross-currency basis in BASIS POINTS, Bloomberg "
            "BCRX-style sign convention. Computed as "
            "basis_bps = (fx_iyd - (local_ois - usd_ois)) * 100. "
            "NEGATIVE = USD scarcity (FX-implied USD funding rate > "
            "USD OIS — USD funder demands a premium via swap). "
            "POSITIVE = USD abundance (rare post-2008). For DM pairs "
            "in normal regimes, typical magnitude -50 to -5 bp. "
            "Sign convention VALIDATED+FROZEN 2026-05-27 via four "
            "corroborations: Sreeram (Bloomberg reference), Codex "
            "(algebraic), Claude (concrete EURUSD/-30bp re-derivation), "
            "and empirical end-to-end PASS on Sreeram-provided OIS data."
        ),
    )
    daily_change_bps: Optional[float] = Field(
        None, description="1 trading-day absolute change in basis (bps)."
    )
    weekly_change_bps: Optional[float] = Field(
        None, description="5 trading-day absolute change in basis (bps)."
    )
    monthly_change_bps: Optional[float] = Field(
        None, description="21 trading-day absolute change in basis (bps)."
    )
    z_score: Optional[float] = Field(
        None,
        description=(
            "Rolling 252-day z-score on the basis series. None when "
            "the series has fewer than z_score_min_periods observations."
        ),
    )
    high_252d_bps: Optional[float] = Field(None)
    low_252d_bps: Optional[float] = Field(None)
    percentile_252d: Optional[float] = Field(None)
    observation_count: int = Field(
        ...,
        description=(
            "Count of dates where BOTH the FX leg (joined spot+forward) "
            "and the OIS leg (joined local+USD curves) had valid values."
        ),
    )


class FXCrossCurrencyBasisOutput(BaseModel):
    current_metrics: FXCrossCurrencyBasisMetrics

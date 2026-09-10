"""Pydantic schemas for the FX carry basket strategy-index primitive.

WHAT THIS IS (and is NOT)
-------------------------
This primitive returns a STRATEGY INDEX representing the daily mark-
to-market excess return of a paper FX carry portfolio. At every
rebalance date, long-top-N pairs (highest forward-implied carry) and
short-bottom-N pairs (lowest carry, in long_short mode) are selected
based on the carry ranking AT THE PRIOR CLOSE (signal_lag_days=1, no
look-ahead). Positions are held for one rebalance period (monthly =
21 trading days V1), then re-selected. Daily basket excess return is
the average daily spot log-return of the long leg minus the short
leg, with carry accrual added per held position. Cumulative index
chains daily basket returns geometrically.

This is **NOT** an executable backtest. V1 explicitly **does not**:
  - apply transaction costs, bid-ask, or slippage
  - model forward roll mechanics (assumes ideal daily mark)
  - model EM forward liquidity constraints
  - account for forward maturity / settlement (treats carry as a
    continuous daily accrual)
  - compound within rebalance period (positions are held flat from
    the rebalance date until the next rebalance)
  - rebalance any frequency other than monthly / 21d (V1 hard-lock)
  - support weighting schemes other than equal-weight (V1 hard-lock)

POSITIONING SIGNAL ONLY — use this index for relative-value /
historical-regime analysis, NOT as a forecast of executable returns.
The real-money equivalent will diverge from this index by typically
200-400 bp annualized after TC / bid-ask / forward liquidity costs.

PR8 central knob: `basket_construction` (long_short_top_n vs
long_only_top_n) — the genuine methodology choice (cross-sectional
zero-cost portfolio vs unidirectional bet).

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR8, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from shared.schemas import TimeSeries


FXCarryBasketMarketScope = Literal["G10", "EM", "ALL"]
FXCarryBasketTenor = Literal["1W", "1M", "3M", "6M", "12M"]
FXCarryBasketConstruction = Literal["long_short_top_n", "long_only_top_n"]


class FXCarryBasketInput(BaseModel):
    """Per-query parameters for the FX carry basket strategy index.

    Per PR8: ``basket_construction`` is the SINGLE central methodology
    knob — defines WHAT the portfolio IS (zero-cost cross-sectional
    long-short vs unidirectional long-only). market_scope / tenor /
    top_n are instrument selectors. Everything else is config-locked.
    """

    market_scope: FXCarryBasketMarketScope = Field(
        default="G10",
        description=(
            "Pair universe. 'G10' = G10 deliverable forwards (6 pairs); "
            "'EM' = EM deliverable forwards (6 pairs); 'ALL' = G10 + EM "
            "(12 pairs). NDFs are NOT included — separate substrate."
        ),
    )
    tenor: FXCarryBasketTenor = Field(
        default="1M",
        description=(
            "Forward tenor for the carry signal AND the holding period. "
            "Position selected by carry at this tenor; held for the "
            "matching rebalance period (e.g. 1M tenor → rebalance every "
            "21 trading days)."
        ),
    )
    top_n: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Number of long (and short, if long_short_top_n) legs. "
            "Equal-weighted within each leg. For G10 universe of 6 pairs, "
            "top_n=3 with long_short_top_n is the canonical 'top half vs "
            "bottom half' split."
        ),
    )
    basket_construction: FXCarryBasketConstruction = Field(
        default="long_short_top_n",
        description=(
            "Portfolio structure. 'long_short_top_n' (DEFAULT) = top_n "
            "highest-carry long, top_n lowest-carry short, equal-weighted, "
            "zero-cost cross-sectional. 'long_only_top_n' = top_n highest-"
            "carry long only, equal-weighted (unidirectional bet, exposed "
            "to USD-direction risk). SINGLE central methodology knob (PR8)."
        ),
    )
    lookback_days: int = Field(
        default=730,
        ge=60,
        le=7300,
        description=(
            "Calendar days of history fetched for the basket-return "
            "series. Default 730 (2y) gives ~1y of valid daily basket "
            "returns after the rebalance-period warmup. The rolling "
            "252-day stats on the basket return series are YAML-locked."
        ),
    )
    field_name: Optional[str] = Field(
        default=None,
        description=(
            "Bloomberg field override for BOTH spot and forward legs. "
            "None falls through to PX_LAST."
        ),
    )


class FXCarryBasketSnapshotMetrics(BaseModel):
    """Snapshot summary statistics on the basket return series."""

    as_of_date: str
    market_scope: str
    tenor: str
    top_n: int
    basket_construction: str
    rebalance_frequency_days: int = Field(
        ..., description="Trading days per rebalance period (V1 = 21)."
    )
    signal_lag_days: int = Field(
        ..., description="Signal-to-position lag (V1 = 1, no lookahead)."
    )
    weighting_scheme: str = Field(
        ..., description="V1 hard-lock = 'equal_weight'."
    )
    transaction_cost_basis: str = Field(
        ...,
        description=(
            "V1 hard-lock = 'none'. No TC / bid-ask / slippage / forward "
            "liquidity costs applied. THIS IS A STRATEGY INDEX, NOT AN "
            "EXECUTABLE BACKTEST."
        ),
    )
    current_cumulative_excess_return_pct: float = Field(
        ...,
        description=(
            "Latest cumulative excess return of the basket index, in "
            "PERCENT (5.0 = +5%). Compounded from daily basket returns "
            "since the first valid rebalance period in the lookback."
        ),
    )
    annualized_return_pct: Optional[float] = Field(
        None,
        description=(
            "Geometric annualized return over the basket return series. "
            "None if series is shorter than annualization_min_obs_days."
        ),
    )
    annualized_volatility_pct: Optional[float] = Field(
        None,
        description=(
            "Annualized standard deviation of daily basket returns "
            "(std * sqrt(252) * 100). None if too few obs."
        ),
    )
    sharpe_ratio: Optional[float] = Field(
        None,
        description=(
            "annualized_return / annualized_volatility. None if vol is "
            "zero or series too short. NO risk-free deduction (excess "
            "return is already vs USD funding implicitly via the carry "
            "signal). Treat as a SIGNAL Sharpe, not an executable Sharpe."
        ),
    )
    max_drawdown_pct: Optional[float] = Field(
        None,
        description="Largest peak-to-trough drawdown on the cumulative index, in PERCENT (-15.0 = -15%).",
    )
    observation_count: int = Field(
        ...,
        description="Count of valid daily basket-return rows.",
    )


class FXCarryBasketOutput(BaseModel):
    snapshot: FXCarryBasketSnapshotMetrics
    cumulative_excess_return_series: TimeSeries = Field(
        ...,
        description=(
            "Canonical TimeSeries of the cumulative excess return "
            "(RATIO unit; 0.05 = +5%). One row per trading day in the "
            "basket window."
        ),
    )
    constituent_pairs: List[str] = Field(
        ...,
        description=(
            "Latest constituent pairs at the most recent rebalance, in "
            "the form 'PAIR (long|short)' for long_short_top_n or "
            "'PAIR (long)' for long_only_top_n."
        ),
    )

"""
rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread —
config-driven same-tenor cross-market ZCIS spread.

Fourth tool in the inflation_swaps domain.  Owns the desk concept
of a *same-tenor cross-market zero-coupon inflation swap (ZCIS)
spread* between two ZCIS curve families at the same pillar (e.g.
USD_ZCIS 5Y minus EUR_ZCIS 5Y, USD_ZCIS 10Y minus GBP_ZCIS 10Y,
EUR_ZCIS 5Y minus GBP_ZCIS 5Y) — computed per-trade-date as the
difference of the two endpoint ZCIS rates:

    spread_pct = leg_a_pct - leg_b_pct
    spread_bps = spread_pct * 100

where each leg's ZCIS rate is computed by
``calculate_inflation_swap_rate_level``.  This primitive composes
two level calls (one per leg) and inherits the level primitive's
four-conjunct no-proxy guard
(``instrument_type='inflation_swap'`` AND
``pricing_type='zero_coupon_breakeven'`` AND ``curve_family=?``
AND ``tenor=?``) transitively.

INDEX-FAMILY CAVEAT — load-bearing:
USD_ZCIS / EUR_ZCIS / GBP_ZCIS reference DIFFERENT inflation
indices (US CPI-U / Eurozone HICP-xT / UK RPI), so this spread
captures BOTH inflation-expectation differentials AND structural
index-family differences; it is NOT a clean expected-inflation
divergence.  Per-leg metadata is surfaced on the wire on
``current_metrics`` so the desk reader can decompose the spread.

Cross-market only — same-curve, two-tenor spreads (e.g.
USD_ZCIS 5Y vs USD_ZCIS 10Y) belong to
``inflation_swap_curve_spread`` and are rejected at the schema
layer here.

External callers reach the public API via this package's path::

    from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread import (
        calculate_cross_market_inflation_swap_spread,
        CrossMarketInflationSwapSpreadInput,
        CrossMarketInflationSwapSpreadOutput,
        CONFIG_PATH,
    )

Or via the inflation_swaps schemas hub::

    from rates_agent.inflation_swaps.tools.schemas import (
        CrossMarketInflationSwapSpreadInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
This primitive's compute() does NOT directly own a fetch / date
seam — the inner level primitive's seams
(``...inflation_swap_rate_level.compute.fetch_zcis_single_pillar``,
``...compute.date``) are the patch points tests must use to
control inner-call behaviour.
"""

from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.compute import (
    CONFIG_PATH,
    calculate_cross_market_inflation_swap_spread,
)
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
    CrossMarketInflationSwapSpreadCurrentMetrics,
    CrossMarketInflationSwapSpreadInput,
    CrossMarketInflationSwapSpreadOutput,
    CrossMarketInflationSwapSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_cross_market_inflation_swap_spread",
    "CrossMarketInflationSwapSpreadInput",
    "CrossMarketInflationSwapSpreadCurrentMetrics",
    "CrossMarketInflationSwapSpreadOutput",
    "CrossMarketInflationSwapSpreadTimeSeriesRow",
]

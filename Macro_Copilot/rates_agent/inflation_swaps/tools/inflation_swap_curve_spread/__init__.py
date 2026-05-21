"""
rates_agent.inflation_swaps.tools.inflation_swap_curve_spread —
config-driven same-curve ZCIS tenor spread.

Second tool in the inflation_swaps domain.  Owns the desk concept
of a *same-curve zero-coupon inflation swap (ZCIS) tenor spread*
between two pillars of the same ZCIS curve family (e.g.
USD_ZCIS 5s10s, EUR_ZCIS 5s30s, GBP_ZCIS 2s10s) — computed per-
trade-date as the difference of the two endpoint ZCIS rates,
converted to bps:

    spread_bps = (long_zcis_pct - short_zcis_pct) * 100

where each endpoint ZCIS rate is computed by
``calculate_inflation_swap_rate_level``.  This primitive composes
two level calls (one per endpoint) and inherits the level
primitive's four-conjunct no-proxy guard
(``instrument_type='inflation_swap'`` AND
``pricing_type='zero_coupon_breakeven'`` AND ``curve_family=?``
AND ``tenor=?``) transitively.

Same-curve only — cross-curve combinations (e.g. USD_ZCIS 5Y vs
EUR_ZCIS 5Y) are a separate primitive
(``cross_market_inflation_swap_spread``), NOT a config knob on
this one.

External callers reach the public API via this package's path::

    from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread import (
        calculate_inflation_swap_curve_spread,
        InflationSwapCurveSpreadInput,
        InflationSwapCurveSpreadOutput,
        CONFIG_PATH,
    )

Or via the inflation_swaps schemas hub::

    from rates_agent.inflation_swaps.tools.schemas import (
        InflationSwapCurveSpreadInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
This primitive's compute() does NOT directly own a fetch / date
seam — the inner level primitive's seams
(``...inflation_swap_rate_level.compute.fetch_zcis_single_pillar``,
``...compute.date``) are the patch points tests must use to
control inner-call behaviour.
"""

from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.compute import (
    CONFIG_PATH,
    calculate_inflation_swap_curve_spread,
)
from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.schemas import (
    InflationSwapCurveSpreadCurrentMetrics,
    InflationSwapCurveSpreadInput,
    InflationSwapCurveSpreadOutput,
    InflationSwapCurveSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_inflation_swap_curve_spread",
    "InflationSwapCurveSpreadInput",
    "InflationSwapCurveSpreadCurrentMetrics",
    "InflationSwapCurveSpreadOutput",
    "InflationSwapCurveSpreadTimeSeriesRow",
]

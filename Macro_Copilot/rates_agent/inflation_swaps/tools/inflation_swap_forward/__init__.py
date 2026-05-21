"""
rates_agent.inflation_swaps.tools.inflation_swap_forward —
config-driven same-curve ZCIS forward inflation swap rate.

Third tool in the inflation_swaps domain.  Owns the desk concept of a
*forward zero-coupon inflation swap (ZCIS) rate* between two pillars
on the same ZCIS curve family — e.g. ``USD_ZCIS 5Y5Y``,
``EUR_ZCIS 5Y5Y``, ``GBP_ZCIS 2Y3Y`` — computed via the dual-
compounding geometric forward formula on the two endpoint ZCIS
rates:

    (1 + r_short)^T_short * (1 + f)^(T_long - T_short) =
        (1 + r_long)^T_long

    f = ((1 + r_long)^T_long / (1 + r_short)^T_short)
            ^ (1 / (T_long - T_short)) - 1

where each endpoint ZCIS rate (``r_short``, ``r_long``) is the
single-pillar ZCIS rate level computed by
``calculate_inflation_swap_rate_level`` at the corresponding tenor.
This primitive composes two level calls (one per endpoint) and
inherits the level primitive's four-conjunct no-proxy guard
(``instrument_type='inflation_swap'`` AND
``pricing_type='zero_coupon_breakeven'`` AND ``curve_family=?`` AND
``tenor=?``) transitively.

Same-curve only — cross-curve forward combinations (e.g.
``USD_ZCIS 5Y`` start vs ``EUR_ZCIS 10Y`` end) are not in scope; the
input layer accepts a single ``curve_family`` field so cross-curve
attempts cannot be expressed.

Concept honesty: the output is FORWARD INFLATION COMPENSATION, NOT a
clean forward expected-inflation read.  ZCIS still carries an
inflation risk premium and a (smaller, but non-zero) liquidity
premium, and the geometric forward inherits both.  The honesty
disclosure lives in ``methodology.what_it_does`` and threads onto
the wire via ``current_metrics.methodology_label`` — sourced from
this YAML at runtime, NOT hardcoded in Python.

External callers reach the public API via this package's path::

    from rates_agent.inflation_swaps.tools.inflation_swap_forward import (
        calculate_inflation_swap_forward,
        InflationSwapForwardInput,
        InflationSwapForwardOutput,
        CONFIG_PATH,
    )

Or via the inflation_swaps schemas hub::

    from rates_agent.inflation_swaps.tools.schemas import (
        InflationSwapForwardInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
This primitive's compute() does NOT directly own a fetch / date
seam — the inner level primitive's seams
(``...inflation_swap_rate_level.compute.fetch_zcis_single_pillar``,
``...compute.date``) are the patch points tests must use to
control inner-call behaviour.
"""

from rates_agent.inflation_swaps.tools.inflation_swap_forward.compute import (
    CONFIG_PATH,
    calculate_inflation_swap_forward,
)
from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
    InflationSwapForwardCurrentMetrics,
    InflationSwapForwardInput,
    InflationSwapForwardOutput,
    InflationSwapForwardTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_inflation_swap_forward",
    "InflationSwapForwardInput",
    "InflationSwapForwardCurrentMetrics",
    "InflationSwapForwardOutput",
    "InflationSwapForwardTimeSeriesRow",
]

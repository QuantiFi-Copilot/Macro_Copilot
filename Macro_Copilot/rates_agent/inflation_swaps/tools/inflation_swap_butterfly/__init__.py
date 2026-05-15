"""
rates_agent.inflation_swaps.tools.inflation_swap_butterfly —
config-driven same-curve zero-coupon inflation swap (ZCIS)
butterfly (3-point ZCIS curve curvature).

Owns the desk concept of a *same-curve ZCIS butterfly* — the 3-point
curvature on a SINGLE ZCIS curve family (e.g. USD_ZCIS 5s10s30s,
EUR_ZCIS 2s5s10s, GBP_ZCIS 2s10s30s), computed per-trade-date via
the FIXED simple-butterfly weighting:

    butterfly_bps = (belly_zcis_pct
                     - 0.5 * (short_zcis_pct + long_zcis_pct)) * 100

Equivalent to ``0.5 * (2 * belly - short - long) * 100``.  Sign
convention: POSITIVE = belly is CHEAP versus the half-weighted
wings (i.e. belly ZCIS rate is HIGH relative to the wings);
NEGATIVE = belly is RICH.  Matches the sovereign_bonds/butterfly,
inflation_indexed_bonds/real_yield_butterfly, and
inflation_indexed_bonds/breakeven_butterfly sign convention.

Each endpoint ZCIS rate (short / belly / long) is computed by
``calculate_inflation_swap_rate_level``.  This primitive composes
three level calls (one per endpoint) and inherits the level
primitive's four-conjunct SELECT no-proxy guard
(``instrument_type='inflation_swap'`` AND
``pricing_type='zero_coupon_breakeven'`` AND ``curve_family=?`` AND
``tenor=?``) transitively.

Same-curve only — cross-curve butterflies (e.g. mixing USD_ZCIS,
EUR_ZCIS, and GBP_ZCIS pillars in a 3-point curvature) are
forbidden by this primitive's input shape.

Raw inflation-swap-rate space — the butterfly arithmetic is
performed on raw ZCIS par rates only.  NO subtraction of a
model-derived basis, NO inflation-risk-premium adjustment, NO
routing through a fitted curve object.  The wire-locked promise is
surfaced via ``current_metrics.methodology_label``.

DISTINCT from:
  - a ZCIS curve spread (2-point difference) — see
    ``calculate_inflation_swap_curve_spread``.
  - a ZCIS forward (year-weighted forward of two endpoints) — see
    ``calculate_inflation_swap_forward``.
  - a linker bond-implied breakeven butterfly (3-point curvature
    of bond-implied breakevens; a different inflation-compensation
    object that carries inflation-risk-premium and relative
    liquidity premia) — see
    ``calculate_breakeven_butterfly``.
  - a real-yield butterfly (3-point curvature of linker real
    yields; in PERCENT) — see ``calculate_real_yield_butterfly``.
  - a nominal sovereign butterfly (3-point curvature of nominal
    yields; in BPS) — see sovereign_bonds/butterfly.

External callers reach the public API via this package's path::

    from rates_agent.inflation_swaps.tools.inflation_swap_butterfly import (
        calculate_inflation_swap_butterfly,
        InflationSwapButterflyInput,
        InflationSwapButterflyOutput,
        CONFIG_PATH,
    )

Or via the inflation_swaps schemas hub::

    from rates_agent.inflation_swaps.tools.schemas import (
        InflationSwapButterflyInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
This primitive's compute() does NOT directly own a fetch / date
seam — the inner level primitive's seams
(``...inflation_swap_rate_level.compute.fetch_zcis_single_pillar``,
``...compute.date``) are the patch points tests must use to
control inner-call behaviour.
"""

from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.compute import (
    CONFIG_PATH,
    calculate_inflation_swap_butterfly,
)
from rates_agent.inflation_swaps.tools.inflation_swap_butterfly.schemas import (
    InflationSwapButterflyCurrentMetrics,
    InflationSwapButterflyInput,
    InflationSwapButterflyOutput,
    InflationSwapButterflyTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_inflation_swap_butterfly",
    "InflationSwapButterflyInput",
    "InflationSwapButterflyCurrentMetrics",
    "InflationSwapButterflyOutput",
    "InflationSwapButterflyTimeSeriesRow",
]

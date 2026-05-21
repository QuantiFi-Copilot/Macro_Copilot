"""
rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple —
config-driven swap-breakeven basis primitive.

Fifth tool in the inflation_swaps domain.  Owns the desk concept
of a *swap-breakeven basis*: the same-tenor, same-currency
difference between a zero-coupon inflation swap (ZCIS) rate and
a generic linker bond-implied breakeven inflation rate at the
same pillar (e.g. USD_ZCIS 10Y minus UST/USD_TIPS 10Y breakeven,
EUR_ZCIS 5Y minus FR_OAT/EUR_FR_LINKER 5Y breakeven,
GBP_ZCIS 10Y minus UK_GILT/GBP_LINKER 10Y breakeven) — computed
per-trade-date as:

    basis_pct = zcis_pct - breakeven_pct
    basis_bps = basis_pct * 100

This primitive composes ``calculate_inflation_swap_rate_level``
once for the ZCIS leg and ``calculate_breakeven_inflation_simple``
once for the breakeven leg, and inherits the inner four-conjunct
SELECT guard (ZCIS leg) AND the linker / nominal instrument_type
discriminators (breakeven leg) AND the same-country invariant
(breakeven leg) transitively.

CONCEPT HONESTY — load-bearing:
The basis is NOT a clean liquidity-premium read.  It also
reflects:
  - index-lag differences between ZCIS conventions and the
    linker bond's realised CPI accrual,
  - linker bond on-the-run / liquidity premium effects in the
    nominal-vs-real decomposition,
  - structural ZCIS vs linker-breakeven basis present even in
    benign markets.

Per the catalog's methodology_guardrails, the canonical sign
convention is ``zcis_minus_breakeven`` and silent inversion is
forbidden.  The compute layer raises NotImplementedError on any
other ``swap_breakeven_basis_sign_convention`` value.

Same-currency only in V1 — cross-currency basis (e.g. USD_ZCIS
minus EUR linker breakeven) is documented in
``methodology.planned_extensions`` but not exposed as a knob.

External callers reach the public API via this package's path::

    from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple import (
        calculate_swap_breakeven_basis_simple,
        SwapBreakevenBasisSimpleInput,
        SwapBreakevenBasisSimpleOutput,
        CONFIG_PATH,
    )

Or via the inflation_swaps schemas hub::

    from rates_agent.inflation_swaps.tools.schemas import (
        SwapBreakevenBasisSimpleInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
This primitive's compute() does NOT directly own a fetch / date
seam — the inner ZCIS level primitive's seams
(``...inflation_swap_rate_level.compute.fetch_zcis_single_pillar``,
``...inflation_swap_rate_level.compute.date``) and the inner
breakeven primitive's seams
(``...breakeven_inflation_simple.compute.fetch_single_tenor``,
``...breakeven_inflation_simple.compute._fetch_curve_family_country_currency``,
``...breakeven_inflation_simple.compute.date``) are the patch
points tests must use to control inner-call behaviour.  Tests
that need to control whole-leg outputs can also patch the public
inner callables on this primitive's compute module
(``...swap_breakeven_basis_simple.compute.calculate_inflation_swap_rate_level``
/ ``...compute.calculate_breakeven_inflation_simple``).
"""

from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.compute import (
    CONFIG_PATH,
    calculate_swap_breakeven_basis_simple,
)
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
    SwapBreakevenBasisSimpleCurrentMetrics,
    SwapBreakevenBasisSimpleInput,
    SwapBreakevenBasisSimpleOutput,
    SwapBreakevenBasisSimpleTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_swap_breakeven_basis_simple",
    "SwapBreakevenBasisSimpleInput",
    "SwapBreakevenBasisSimpleCurrentMetrics",
    "SwapBreakevenBasisSimpleOutput",
    "SwapBreakevenBasisSimpleTimeSeriesRow",
]

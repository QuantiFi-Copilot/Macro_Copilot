"""
rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple
— config-driven same-tenor cross-country breakeven inflation
differential.

Fifth tool in the inflation_indexed_bonds domain.  Owns the desk
concept of a *same-tenor cross-country breakeven differential* —
the difference between two countries' generic bond-implied
breakevens at the same tenor (e.g. US 10Y breakeven minus EUR-FR
10Y breakeven, US 5Y breakeven minus UK 5Y breakeven), computed
per-trade-date as:

    spread_bps = breakeven_a_bps - breakeven_b_bps

where each country leg's breakeven is the spot bond-implied
breakeven computed by ``calculate_breakeven_inflation_simple``
constrained to that country's own (nominal, linker) pair.  This
primitive composes two spot calls (one per country) and inherits
the spot primitive's no-proxy guard (instrument_type filter on
each leg) AND its same-country / same-currency invariant (per
leg, before any market-data SELECT) transitively.

Cross-country guard layering: the cross-country requirement
(country_a vs country_b are different sovereign issuers) is
enforced in this primitive's input validators (no DB lookup
needed); the per-leg same-country invariant continues to live
inside ``breakeven_inflation_simple``.

NOT a pure cross-country expected-inflation differential — see
``compute.py`` and the methodology disclosure threaded onto the
wire via ``current_metrics.methodology_label`` (carries the
INDEX-FAMILY MISMATCH caveat AND the inherited inflation-
compensation caveat).

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple import (
        calculate_cross_country_breakeven_spread_simple,
        CrossCountryBreakevenSpreadSimpleInput,
        CrossCountryBreakevenSpreadSimpleOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        CrossCountryBreakevenSpreadSimpleInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
This primitive's compute() does NOT directly own a fetch / date
seam — the inner spot primitive's seams
(``...breakeven_inflation_simple.compute.fetch_single_tenor``,
``...compute.date``,
``...compute._fetch_curve_family_country_currency``) are the
patch points tests must use to control inner-call behaviour.
"""

from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.compute import (
    CONFIG_PATH,
    calculate_cross_country_breakeven_spread_simple,
)
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.schemas import (
    CrossCountryBreakevenSpreadSimpleCurrentMetrics,
    CrossCountryBreakevenSpreadSimpleInput,
    CrossCountryBreakevenSpreadSimpleOutput,
    CrossCountryBreakevenSpreadSimpleTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_cross_country_breakeven_spread_simple",
    "CrossCountryBreakevenSpreadSimpleInput",
    "CrossCountryBreakevenSpreadSimpleCurrentMetrics",
    "CrossCountryBreakevenSpreadSimpleOutput",
    "CrossCountryBreakevenSpreadSimpleTimeSeriesRow",
]

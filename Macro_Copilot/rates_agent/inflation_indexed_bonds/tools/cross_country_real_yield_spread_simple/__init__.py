"""
rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple —
config-driven same-tenor cross-country linker real-yield differential.

Seventh tool in the inflation_indexed_bonds domain.  Owns the desk
concept of a *same-tenor cross-country linker real-yield
differential* — the real-rate divergence object — between two
linker curves' real-yield levels at the same tenor (e.g. USD_TIPS
10Y real yield minus GBP_LINKER 10Y real yield), computed
per-trade-date as the difference of the two endpoint real-yield
levels:

    spread_pct = first_curve_real_yield_pct - second_curve_real_yield_pct

where each endpoint real-yield level is computed by
``get_real_yield_level``.  This primitive composes two single-
tenor level calls (one per curve_family) and inherits the level
primitive's no-proxy guard (``instrument_type='inflation_linker'``
filter on the DB read) transitively on BOTH legs.  Compute
additionally re-asserts on the resolved
``macro_data.instrument_master`` rows that EACH curve_family maps
to a single ``(country, currency)`` tuple under that
instrument_type — the post-fetch identity guard.

DISTINCT from a cross-country breakeven differential (which
differences two nominal-minus-linker pairs — inflation
compensation), a same-country real-yield curve spread (which
differences two tenors on a single curve — the real-yield curve-
shape object), and a sovereign nominal cross-market spread (which
differences two nominal-sovereign yields).  See ``compute.py`` and
the methodology disclosure threaded onto the wire via
``current_metrics.methodology_label``.

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple import (
        calculate_cross_country_real_yield_spread_simple,
        CrossCountryRealYieldSpreadSimpleInput,
        CrossCountryRealYieldSpreadSimpleOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        CrossCountryRealYieldSpreadSimpleInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``get_real_yield_level``,
``_enforce_linker_identity_both_legs``, and
``_fetch_curve_family_country_currency`` are patchable seams
inside ``compute.py``; tests must patch them at
``...cross_country_real_yield_spread_simple.compute.<name>``.  The
inner level primitive's own seams
(``rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor``,
``...compute.date``) are also valid patch points for end-to-end
synthetic-fixture testing.
"""

from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.compute import (
    CONFIG_PATH,
    calculate_cross_country_real_yield_spread_simple,
)
from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
    CrossCountryRealYieldSpreadSimpleCurrentMetrics,
    CrossCountryRealYieldSpreadSimpleInput,
    CrossCountryRealYieldSpreadSimpleOutput,
    CrossCountryRealYieldSpreadSimpleTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_cross_country_real_yield_spread_simple",
    "CrossCountryRealYieldSpreadSimpleInput",
    "CrossCountryRealYieldSpreadSimpleCurrentMetrics",
    "CrossCountryRealYieldSpreadSimpleOutput",
    "CrossCountryRealYieldSpreadSimpleTimeSeriesRow",
]

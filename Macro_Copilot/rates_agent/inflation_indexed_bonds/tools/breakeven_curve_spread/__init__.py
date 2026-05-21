"""
rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread —
config-driven same-country breakeven curve spread.

Fourth tool in the inflation_indexed_bonds domain.  Owns the desk
concept of a *same-country breakeven curve spread* — the inflation-
compensation term-structure object — between two breakeven tenors
of the same nominal/linker pair (e.g. UST/USD_TIPS 2s10s breakeven,
UK_GILT/GBP_LINKER 5s30s breakeven), computed per-trade-date as
the difference of the two endpoint spot breakevens:

    spread_bps = long_breakeven_bps - short_breakeven_bps

where each endpoint breakeven (``short_breakeven``,
``long_breakeven``) is the spot bond-implied breakeven computed by
``calculate_breakeven_inflation_simple``.  This primitive composes
two spot calls (one per endpoint) and inherits the spot
primitive's no-proxy guard (instrument_type filter on each leg)
AND same-country invariant (instrument_master country/currency
check before any market-data SELECT) transitively.

NOT the term structure of pure expected inflation — see
``compute.py`` and the methodology disclosure threaded onto the
wire via ``current_metrics.methodology_label``.

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread import (
        calculate_breakeven_curve_spread,
        BreakevenCurveSpreadInput,
        BreakevenCurveSpreadOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        BreakevenCurveSpreadInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
This primitive's compute() does NOT directly own a fetch / date
seam — the inner spot primitive's seams
(``...breakeven_inflation_simple.compute.fetch_single_tenor``,
``...compute.date``,
``...compute._fetch_curve_family_country_currency``) are the
patch points tests must use to control inner-call behaviour.
"""

from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread.compute import (
    CONFIG_PATH,
    calculate_breakeven_curve_spread,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread.schemas import (
    BreakevenCurveSpreadCurrentMetrics,
    BreakevenCurveSpreadInput,
    BreakevenCurveSpreadOutput,
    BreakevenCurveSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_breakeven_curve_spread",
    "BreakevenCurveSpreadInput",
    "BreakevenCurveSpreadCurrentMetrics",
    "BreakevenCurveSpreadOutput",
    "BreakevenCurveSpreadTimeSeriesRow",
]

"""
rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple —
config-driven forward bond-implied breakeven inflation.

Third tool in the inflation_indexed_bonds domain.  Owns the desk
concept of a *forward bond-implied breakeven inflation* between two
same-country curve points (e.g. 5Y5Y, 5Y10Y, 2Y3Y), computed via
the year-weighted linear formula on the two endpoint spot
breakevens:

    forward_breakeven_bps =
        (BE_long_bps * T_long - BE_short_bps * T_short)
        / (T_long - T_short)

where each endpoint breakeven (``BE_short``, ``BE_long``) is the
spot bond-implied breakeven computed by
``calculate_breakeven_inflation_simple``.  This primitive composes
two spot calls (one per endpoint) and inherits the spot primitive's
no-proxy guard (instrument_type filter on each leg) AND
same-country invariant (instrument_master country/currency check
before any market-data SELECT) transitively.

NOT a clean forward expected-inflation read — see ``compute.py`` and
the methodology disclosure threaded onto the wire via
``current_metrics.methodology_label``.

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple import (
        calculate_forward_breakeven_simple,
        ForwardBreakevenSimpleInput,
        ForwardBreakevenSimpleOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        ForwardBreakevenSimpleInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
This primitive's compute() does NOT directly own a fetch / date
seam — the inner spot primitive's seams
(``...breakeven_inflation_simple.compute.fetch_single_tenor``,
``...compute.date``,
``...compute._fetch_curve_family_country_currency``) are the
patch points tests must use to control inner-call behaviour.
"""

from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple.compute import (
    CONFIG_PATH,
    calculate_forward_breakeven_simple,
)
from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple.schemas import (
    ForwardBreakevenSimpleCurrentMetrics,
    ForwardBreakevenSimpleInput,
    ForwardBreakevenSimpleOutput,
    ForwardBreakevenSimpleTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_forward_breakeven_simple",
    "ForwardBreakevenSimpleInput",
    "ForwardBreakevenSimpleCurrentMetrics",
    "ForwardBreakevenSimpleOutput",
    "ForwardBreakevenSimpleTimeSeriesRow",
]

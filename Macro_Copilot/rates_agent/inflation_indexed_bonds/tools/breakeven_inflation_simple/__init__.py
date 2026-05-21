"""
rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple —
config-driven generic bond-implied breakeven inflation.

Second tool in the inflation_indexed_bonds domain.  Owns the desk
concept of bond-implied breakeven inflation between a nominal sovereign
yield and the corresponding sovereign linker real yield at the same
tenor:

    breakeven_pct = nominal_yield_pct - real_yield_pct
    breakeven_bps = breakeven_pct * 100

NOT a clean expected-inflation read — see ``compute.py`` and the
methodology disclosure threaded onto the wire via
``current_metrics.methodology_label``.

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
        calculate_breakeven_inflation_simple,
        BreakevenInflationSimpleInput,
        BreakevenInflationSimpleOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        BreakevenInflationSimpleInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_tenor`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...breakeven_inflation_simple.compute.<name>``, NOT on this package
init.
"""

from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute import (
    CONFIG_PATH,
    calculate_breakeven_inflation_simple,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.schemas import (
    BreakevenInflationSimpleCurrentMetrics,
    BreakevenInflationSimpleInput,
    BreakevenInflationSimpleOutput,
    BreakevenInflationSimpleTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_breakeven_inflation_simple",
    "BreakevenInflationSimpleInput",
    "BreakevenInflationSimpleCurrentMetrics",
    "BreakevenInflationSimpleOutput",
    "BreakevenInflationSimpleTimeSeriesRow",
]

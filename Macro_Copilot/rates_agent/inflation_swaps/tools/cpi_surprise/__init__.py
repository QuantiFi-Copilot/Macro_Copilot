"""
rates_agent.inflation_swaps.tools.cpi_surprise — config-driven CPI-surprise
primitive.

For one country's headline CPI YoY release series, computes the
per-release surprise = ``actual − consensus_median`` (in percentage
points of YoY CPI) plus a rolling z-score over a window of N
RELEASES (not calendar days).  Built on the event-calendar substrate
landed by ADR 0004 and populated by the economic-releases playbook
per ADR 0008.

External callers reach the public API via this package's path:

    from rates_agent.inflation_swaps.tools.cpi_surprise import (
        calculate_cpi_surprise,
        CpiSurpriseInput,
        CpiSurpriseOutput,
        CONFIG_PATH,
    )

Or via the inflation-swaps schemas hub:

    from rates_agent.inflation_swaps.tools.schemas import CpiSurpriseInput

Note for tests
--------------
``__init__.py`` re-exports only public symbols.  Test seams like
``fetch_economic_release_surprises`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...cpi_surprise.compute.fetch_economic_release_surprises`` /
``...cpi_surprise.compute.date``, NOT on the package init.
"""

from rates_agent.inflation_swaps.tools.cpi_surprise.compute import (
    CONFIG_PATH,
    calculate_cpi_surprise,
)
from rates_agent.inflation_swaps.tools.cpi_surprise.schemas import (
    CpiSurpriseCurrentMetrics,
    CpiSurpriseInput,
    CpiSurpriseOutput,
    CpiSurpriseTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_cpi_surprise",
    "CpiSurpriseCurrentMetrics",
    "CpiSurpriseInput",
    "CpiSurpriseOutput",
    "CpiSurpriseTimeSeriesRow",
]

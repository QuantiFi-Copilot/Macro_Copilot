"""
rates_agent.sovereign_bonds.tools.cross_market_spread — config-driven
cross-market sovereign yield differential tool.

Migrated from the legacy single-file
``rates_agent/sovereign_bonds/tools/cross_market_spread.py`` into the
per-tool-folder pattern (architecture/tool_architecture.md).

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.cross_market_spread import (
        calculate_cross_market_spread,
        CrossMarketSpreadInput,
        CrossMarketSpreadOutput,
        CONFIG_PATH,
    )

Or via the sovereign-bonds schemas hub:

    from rates_agent.sovereign_bonds.tools.schemas import CrossMarketSpreadInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_cross_market_pair`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...cross_market_spread.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.cross_market_spread.compute import (
    CONFIG_PATH,
    calculate_cross_market_spread,
)
from rates_agent.sovereign_bonds.tools.cross_market_spread.schemas import (
    CrossMarketSpreadInput,
    CrossMarketSpreadCurrentMetrics,
    CrossMarketSpreadTimeSeriesRow,
    CrossMarketSpreadOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_cross_market_spread",
    "CrossMarketSpreadInput",
    "CrossMarketSpreadCurrentMetrics",
    "CrossMarketSpreadTimeSeriesRow",
    "CrossMarketSpreadOutput",
]

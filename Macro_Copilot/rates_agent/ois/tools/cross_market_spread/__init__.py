"""
rates_agent.ois.tools.cross_market_spread — config-driven OIS
cross-market spread tool.

Migrated from the legacy single-file
``rates_agent/ois/tools/cross_market_spread.py`` into the per-tool-
folder pattern.  See ``compute.py`` for the methodology +
backward-compat detail and ``docs/architecture/tool_architecture.md``
for the canonical layout.

Third OIS tool brought onto the per-tool-folder pattern (after
``rate_level`` — PR #61 — and ``curve_spread`` — PR #62 + #63).  The
remaining two (``forward_rate`` and ``scanner``) follow in subsequent
PRs.

External callers reach the public API via this package's path:

    from rates_agent.ois.tools.cross_market_spread import (
        calculate_ois_cross_market_spread,
        OISCrossMarketSpreadInput,
        OISCrossMarketSpreadOutput,
        CONFIG_PATH,
    )

Or via the OIS schemas hub:

    from rates_agent.ois.tools.schemas import OISCrossMarketSpreadInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_cross_market_pair`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...cross_market_spread.compute.<name>``, NOT on the package init.
"""

from rates_agent.ois.tools.cross_market_spread.compute import (
    CONFIG_PATH,
    calculate_ois_cross_market_spread,
)
from rates_agent.ois.tools.cross_market_spread.schemas import (
    OISCrossMarketSpreadCurrentMetrics,
    OISCrossMarketSpreadInput,
    OISCrossMarketSpreadOutput,
    OISCrossMarketSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_ois_cross_market_spread",
    "OISCrossMarketSpreadCurrentMetrics",
    "OISCrossMarketSpreadInput",
    "OISCrossMarketSpreadOutput",
    "OISCrossMarketSpreadTimeSeriesRow",
]

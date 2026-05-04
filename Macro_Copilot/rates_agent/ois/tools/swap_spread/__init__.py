"""
rates_agent.ois.tools.swap_spread — cross-domain swap-spread primitive.

Asset-swap-spread style differential between a sovereign yield
curve and an OIS curve at the same tenor.  Per the per-tool-folder
pattern; see ``compute.py`` for the methodology + sign convention
detail and ``docs/architecture/tool_architecture.md`` for the
canonical layout.

This is the FIRST cross-domain primitive in the repo.  It lives
under ``rates_agent/ois/tools/`` because the OIS MCP server is its
natural host (the OIS routing pattern in ``orchestrator/`` already
exists), but the primitive itself spans both sovereign and OIS
domains.

External callers reach the public API via this package's path:

    from rates_agent.ois.tools.swap_spread import (
        calculate_swap_spread,
        SwapSpreadInput,
        SwapSpreadOutput,
        CONFIG_PATH,
    )

Or via the OIS schemas hub:

    from rates_agent.ois.tools.schemas import SwapSpreadInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_cross_domain_pair`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...swap_spread.compute.<name>``, NOT on the package init.
"""

from rates_agent.ois.tools.swap_spread.compute import (
    CONFIG_PATH,
    calculate_swap_spread,
)
from rates_agent.ois.tools.swap_spread.schemas import (
    SwapSpreadCurrentMetrics,
    SwapSpreadInput,
    SwapSpreadOutput,
    SwapSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_swap_spread",
    "SwapSpreadCurrentMetrics",
    "SwapSpreadInput",
    "SwapSpreadOutput",
    "SwapSpreadTimeSeriesRow",
]

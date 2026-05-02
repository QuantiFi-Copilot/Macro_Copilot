"""
rates_agent.sovereign_bonds.tools.butterfly — config-driven 3-point
butterfly (curvature) tool.

Migrated from the legacy single-file
``rates_agent/sovereign_bonds/tools/butterfly.py`` into the per-tool-
folder pattern (docs/architecture/tool_architecture.md).

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.butterfly import (
        calculate_butterfly,
        ButterflyInput,
        ButterflyOutput,
        CONFIG_PATH,
    )

Or via the sovereign-bonds schemas hub:

    from rates_agent.sovereign_bonds.tools.schemas import ButterflyInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_tenor_group`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...butterfly.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.butterfly.compute import (
    CONFIG_PATH,
    calculate_butterfly,
)
from rates_agent.sovereign_bonds.tools.butterfly.schemas import (
    ButterflyInput,
    ButterflyCurrentMetrics,
    ButterflyTimeSeriesRow,
    ButterflyOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_butterfly",
    "ButterflyInput",
    "ButterflyCurrentMetrics",
    "ButterflyTimeSeriesRow",
    "ButterflyOutput",
]

"""
rates_agent.sovereign_bonds.tools.yield_levels — config-driven
single-tenor yield-level snapshot.

Migrated from the legacy single-file
``rates_agent/sovereign_bonds/tools/yield_levels.py`` into the per-
tool-folder pattern established by the tool-config refactor pilot.
See ``compute.py`` for the methodology + backward-compat detail and
``docs/architecture/tool_architecture.md`` for the canonical layout.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.yield_levels import (
        get_yield_levels,
        YieldLevelInput,
        YieldLevelOutput,
        CONFIG_PATH,
    )

Or via the sovereign-bonds schemas hub:

    from rates_agent.sovereign_bonds.tools.schemas import YieldLevelInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_tenor`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...yield_levels.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.yield_levels.compute import (
    CONFIG_PATH,
    get_yield_levels,
)
from rates_agent.sovereign_bonds.tools.yield_levels.schemas import (
    YieldLevelInput,
    YieldLevelMetrics,
    YieldLevelOutput,
)


__all__ = [
    "CONFIG_PATH",
    "get_yield_levels",
    "YieldLevelInput",
    "YieldLevelMetrics",
    "YieldLevelOutput",
]

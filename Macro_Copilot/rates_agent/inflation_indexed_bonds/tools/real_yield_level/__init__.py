"""
rates_agent.inflation_indexed_bonds.tools.real_yield_level —
config-driven single-tenor sovereign-linker real-yield snapshot.

First tool in the inflation_indexed_bonds domain.  Mirrors the
per-tool-folder pattern established by the tool-config refactor pilot
(sovereign + OIS sides).  See ``compute.py`` for the methodology and
``docs/architecture/tool_architecture.md`` for the canonical layout.

External callers reach the public API via this package's path:

    from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
        get_real_yield_level,
        RealYieldLevelInput,
        RealYieldLevelOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub:

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        RealYieldLevelInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_tenor`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...real_yield_level.compute.<name>``, NOT on the package init.
"""

from rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute import (
    CONFIG_PATH,
    get_real_yield_level,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_level.schemas import (
    RealYieldLevelInput,
    RealYieldLevelMetrics,
    RealYieldLevelOutput,
)


__all__ = [
    "CONFIG_PATH",
    "get_real_yield_level",
    "RealYieldLevelInput",
    "RealYieldLevelMetrics",
    "RealYieldLevelOutput",
]

"""
rates_agent.ois.tools.rate_level — config-driven single-tenor OIS
par-swap-rate snapshot.

Migrated from the legacy single-file
``rates_agent/ois/tools/rate_level.py`` into the per-tool-folder
pattern established by the tool-config refactor pilot (sovereign
side).  See ``compute.py`` for the methodology + backward-compat
detail and ``docs/architecture/tool_architecture.md`` for the
canonical layout.

This is the FIRST OIS tool brought onto the per-tool-folder pattern
— follow-on OIS tools (curve_spread, forward_rate,
cross_market_spread, scanner) will follow.

External callers reach the public API via this package's path:

    from rates_agent.ois.tools.rate_level import (
        get_ois_rate_level,
        OISRateLevelInput,
        OISRateLevelOutput,
        CONFIG_PATH,
    )

Or via the OIS schemas hub:

    from rates_agent.ois.tools.schemas import OISRateLevelInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_tenor`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...rate_level.compute.<name>``, NOT on the package init.
"""

from rates_agent.ois.tools.rate_level.compute import (
    CONFIG_PATH,
    get_ois_rate_level,
)
from rates_agent.ois.tools.rate_level.schemas import (
    OISRateLevelInput,
    OISRateLevelMetrics,
    OISRateLevelOutput,
)


__all__ = [
    "CONFIG_PATH",
    "get_ois_rate_level",
    "OISRateLevelInput",
    "OISRateLevelMetrics",
    "OISRateLevelOutput",
]

"""
rates_agent.inflation_swaps.tools.inflation_swap_rate_level —
config-driven single-pillar zero-coupon inflation swap rate level.

First tool in the inflation_swaps domain.  Mirrors the per-tool-folder
pattern established by the tool-config refactor pilot.  See
``compute.py`` for the methodology and
``docs/architecture/tool_architecture.md`` for the canonical layout.

External callers reach the public API via this package's path:

    from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
        calculate_inflation_swap_rate_level,
        InflationSwapRateLevelInput,
        InflationSwapRateLevelOutput,
        CONFIG_PATH,
    )

Or via the inflation_swaps schemas hub:

    from rates_agent.inflation_swaps.tools.schemas import (
        InflationSwapRateLevelInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_zcis_single_pillar`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...inflation_swap_rate_level.compute.<name>``, NOT on the package
init.
"""

from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.compute import (
    CONFIG_PATH,
    calculate_inflation_swap_rate_level,
    fetch_zcis_single_pillar,
)
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
    InflationSwapRateLevelCurrentMetrics,
    InflationSwapRateLevelInput,
    InflationSwapRateLevelOutput,
    InflationSwapRateLevelTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_inflation_swap_rate_level",
    "fetch_zcis_single_pillar",
    "InflationSwapRateLevelInput",
    "InflationSwapRateLevelCurrentMetrics",
    "InflationSwapRateLevelTimeSeriesRow",
    "InflationSwapRateLevelOutput",
]

"""Re-exports for inflation_indexed_bonds tool schemas.

Mirrors the sovereign + OIS schema-hub pattern: per-tool-folder packages
own the canonical schema definitions, this hub re-exports them under
short names for callers that prefer the hub-style import.

External code can choose either path::

    # Direct (preferred for new code)
    from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
        RealYieldLevelInput,
    )

    # Via the hub (matches sovereign / OIS convention)
    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        RealYieldLevelInput,
    )
"""

# Real yield level (single-tenor) — re-exported from the
# per-tool-folder package
# (``rates_agent/inflation_indexed_bonds/tools/real_yield_level/``).
from rates_agent.inflation_indexed_bonds.tools.real_yield_level.schemas import (
    RealYieldLevelInput,
    RealYieldLevelMetrics,
    RealYieldLevelOutput,
)


__all__ = [
    # real_yield_level
    "RealYieldLevelInput",
    "RealYieldLevelMetrics",
    "RealYieldLevelOutput",
]

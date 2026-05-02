"""
rates_agent.sovereign_bonds.tools.zscore_custom — config-driven custom-
window rolling z-score on a single sovereign yield series.

The first new-pattern tool in the v6 sprint.  Distinct from
``yield_levels`` which fixes the z-score window at YAML's 252-day
default; this tool's central methodological choice is the user-
supplied ``z_score_window_days``, set per request.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.zscore_custom import (
        calculate_zscore_custom,
        ZscoreCustomInput,
        ZscoreCustomOutput,
        CONFIG_PATH,
    )

See docs/architecture/tool_architecture.md for the canonical layout.

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_tenor`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...zscore_custom.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.zscore_custom.compute import (
    CONFIG_PATH,
    calculate_zscore_custom,
)
from rates_agent.sovereign_bonds.tools.zscore_custom.schemas import (
    ZscoreCustomInput,
    ZscoreCustomMetrics,
    ZscoreCustomOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_zscore_custom",
    "ZscoreCustomInput",
    "ZscoreCustomMetrics",
    "ZscoreCustomOutput",
]

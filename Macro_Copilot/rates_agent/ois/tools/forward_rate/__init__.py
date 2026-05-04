"""
rates_agent.ois.tools.forward_rate — config-driven OIS forward-rate tool.

Migrated from the legacy single-file
``rates_agent/ois/tools/forward_rate.py`` into the per-tool-folder
pattern.  See ``compute.py`` for the methodology + backward-compat
detail and ``docs/architecture/tool_architecture.md`` for the
canonical layout.

Fourth OIS tool brought onto the per-tool-folder pattern (after
``rate_level``, ``curve_spread``, ``cross_market_spread``).  Only
``scanner`` remains.

External callers reach the public API via this package's path:

    from rates_agent.ois.tools.forward_rate import (
        calculate_ois_forward_rate,
        OISForwardRateInput,
        OISForwardRateOutput,
        CONFIG_PATH,
    )

Or via the OIS schemas hub:

    from rates_agent.ois.tools.schemas import OISForwardRateInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``_fetch_full_curve`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...forward_rate.compute.<name>``, NOT on the package init.
"""

from rates_agent.ois.tools.forward_rate.compute import (
    CONFIG_PATH,
    calculate_ois_forward_rate,
)
from rates_agent.ois.tools.forward_rate.schemas import (
    OISForwardRateCurrentMetrics,
    OISForwardRateInput,
    OISForwardRateOutput,
    OISForwardRateTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_ois_forward_rate",
    "OISForwardRateCurrentMetrics",
    "OISForwardRateInput",
    "OISForwardRateOutput",
    "OISForwardRateTimeSeriesRow",
]

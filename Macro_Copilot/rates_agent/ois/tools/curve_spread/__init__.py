"""
rates_agent.ois.tools.curve_spread — config-driven OIS curve-spread tool.

Migrated from the legacy single-file
``rates_agent/ois/tools/curve_spread.py`` into the per-tool-folder
pattern (sovereign-side pilot).  See ``compute.py`` for the
methodology + backward-compat detail and
``docs/architecture/tool_architecture.md`` for the canonical layout.

Second OIS tool brought onto the per-tool-folder pattern (after
``rate_level``).  The remaining three (forward_rate,
cross_market_spread, scanner) will follow.

External callers reach the public API via this package's path:

    from rates_agent.ois.tools.curve_spread import (
        calculate_ois_curve_spread,
        OISCurveSpreadInput,
        OISCurveSpreadOutput,
        CONFIG_PATH,
    )

Or via the OIS schemas hub:

    from rates_agent.ois.tools.schemas import OISCurveSpreadInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_tenor_pair`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...curve_spread.compute.<name>``, NOT on the package init.
"""

from rates_agent.ois.tools.curve_spread.compute import (
    CONFIG_PATH,
    calculate_ois_curve_spread,
)
from rates_agent.ois.tools.curve_spread.schemas import (
    OISCurveSpreadCurrentMetrics,
    OISCurveSpreadInput,
    OISCurveSpreadOutput,
    OISCurveSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_ois_curve_spread",
    "OISCurveSpreadCurrentMetrics",
    "OISCurveSpreadInput",
    "OISCurveSpreadOutput",
    "OISCurveSpreadTimeSeriesRow",
]

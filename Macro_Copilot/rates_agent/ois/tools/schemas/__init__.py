"""Re-exports for OIS tool schemas."""

# Curve spread (single OIS curve, two-tenor) — re-exported from the
# per-tool-folder package (``rates_agent/ois/tools/curve_spread/``).
# The schemas hub stays as the legacy import surface for callers /
# tests that haven't migrated to the package path; new code should
# prefer ``from rates_agent.ois.tools.curve_spread import
# OISCurveSpreadInput``.
from rates_agent.ois.tools.curve_spread.schemas import (
    OISCurveSpreadCurrentMetrics,
    OISCurveSpreadInput,
    OISCurveSpreadOutput,
    OISCurveSpreadTimeSeriesRow,
)

# Rate level (single-tenor) — re-exported from the per-tool-folder
# package (``rates_agent/ois/tools/rate_level/``).  The schemas hub
# stays as the legacy import surface for callers / tests that haven't
# migrated to the package path; new code should prefer
# ``from rates_agent.ois.tools.rate_level import OISRateLevelInput``.
from rates_agent.ois.tools.rate_level.schemas import (
    OISRateLevelInput,
    OISRateLevelMetrics,
    OISRateLevelOutput,
)

# Forward rate (tenor- or date-window forward)
from rates_agent.ois.tools.schemas.forward_rate import (
    OISForwardRateCurrentMetrics,
    OISForwardRateInput,
    OISForwardRateOutput,
    OISForwardRateTimeSeriesRow,
)

# Cross-market spread (same tenor, two OIS curves)
from rates_agent.ois.tools.schemas.cross_market import (
    OISCrossMarketSpreadCurrentMetrics,
    OISCrossMarketSpreadInput,
    OISCrossMarketSpreadOutput,
    OISCrossMarketSpreadTimeSeriesRow,
)

# Scanner (z-score extremes across the OIS universe)
from rates_agent.ois.tools.schemas.scanner import (
    OISScannerInput,
    OISScannerOutput,
    OISScannerResultRow,
)

# NOTE: meeting_pricing was removed.  The tool used linear interpolation
# on par OIS swap rates to approximate central-bank meeting moves, which
# produces a ramp where the market prices a step function — outputs drifted
# visibly from Bloomberg WIRP.  Will be rebuilt on top of ingested
# WIRP data rather than recomputed from scratch.  The CalendarProvider
# infrastructure in rates_agent/ois/reference/ is preserved for the rebuild.

__all__ = [
    # curve_spread
    "OISCurveSpreadCurrentMetrics",
    "OISCurveSpreadInput",
    "OISCurveSpreadOutput",
    "OISCurveSpreadTimeSeriesRow",
    # rate_level
    "OISRateLevelInput",
    "OISRateLevelMetrics",
    "OISRateLevelOutput",
    # forward_rate
    "OISForwardRateCurrentMetrics",
    "OISForwardRateInput",
    "OISForwardRateOutput",
    "OISForwardRateTimeSeriesRow",
    # cross_market
    "OISCrossMarketSpreadCurrentMetrics",
    "OISCrossMarketSpreadInput",
    "OISCrossMarketSpreadOutput",
    "OISCrossMarketSpreadTimeSeriesRow",
    # scanner
    "OISScannerInput",
    "OISScannerOutput",
    "OISScannerResultRow",
]

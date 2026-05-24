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

# Forward rate (tenor- or date-window forward) — re-exported from
# the per-tool-folder package
# (``rates_agent/ois/tools/forward_rate/``).  The schemas hub stays
# as the legacy import surface for callers / tests that haven't
# migrated to the package path; new code should prefer
# ``from rates_agent.ois.tools.forward_rate import OISForwardRateInput``.
from rates_agent.ois.tools.forward_rate.schemas import (
    OISForwardRateCurrentMetrics,
    OISForwardRateInput,
    OISForwardRateOutput,
    OISForwardRateTimeSeriesRow,
)

# Cross-market spread (same tenor, two OIS curves) — re-exported from
# the per-tool-folder package
# (``rates_agent/ois/tools/cross_market_spread/``).  The schemas hub
# stays as the legacy import surface for callers / tests that haven't
# migrated to the package path; new code should prefer
# ``from rates_agent.ois.tools.cross_market_spread import
# OISCrossMarketSpreadInput``.
from rates_agent.ois.tools.cross_market_spread.schemas import (
    OISCrossMarketSpreadCurrentMetrics,
    OISCrossMarketSpreadInput,
    OISCrossMarketSpreadOutput,
    OISCrossMarketSpreadTimeSeriesRow,
)

# Swap spread (cross-domain — one sovereign leg + one OIS leg) —
# re-exported from the per-tool-folder package
# (``rates_agent/ois/tools/swap_spread/``).  This is the FIRST cross-
# domain primitive in the repo; lives under OIS because the OIS MCP
# server is its natural host (existing routing pattern), but the
# primitive itself spans both domains.
from rates_agent.ois.tools.swap_spread.schemas import (
    SwapSpreadCurrentMetrics,
    SwapSpreadInput,
    SwapSpreadOutput,
    SwapSpreadTimeSeriesRow,
)

# Scanner (z-score extremes across the OIS universe)
from rates_agent.ois.tools.schemas.scanner import (
    OISScannerInput,
    OISScannerOutput,
    OISScannerResultRow,
)

# WIRP per-meeting pricing — INGEST primitive surfacing Bloomberg's
# WIRP-screen fields per ADR 0009 (P12 boundary).  List-shaped
# categorical output — registered in WORKFLOW_INCOMPATIBLE_TOOLS
# (not bridge-composable in V1).
from rates_agent.ois.tools.wirp_meeting_pricing.schemas import (
    WirpMeetingPricingCurrentMetrics,
    WirpMeetingPricingInput,
    WirpMeetingPricingOutput,
    WirpMeetingSnapshot,
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
    # swap_spread (cross-domain)
    "SwapSpreadCurrentMetrics",
    "SwapSpreadInput",
    "SwapSpreadOutput",
    "SwapSpreadTimeSeriesRow",
    # scanner
    "OISScannerInput",
    "OISScannerOutput",
    "OISScannerResultRow",
    # wirp_meeting_pricing
    "WirpMeetingPricingInput",
    "WirpMeetingPricingCurrentMetrics",
    "WirpMeetingSnapshot",
    "WirpMeetingPricingOutput",
]

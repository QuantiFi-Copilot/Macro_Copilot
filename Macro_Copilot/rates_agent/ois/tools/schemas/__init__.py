"""Re-exports for OIS tool schemas."""

# Curve spread (existing)
from rates_agent.ois.tools.schemas.spread import (
    OISCurveSpreadCurrentMetrics,
    OISCurveSpreadInput,
    OISCurveSpreadOutput,
    OISCurveSpreadTimeSeriesRow,
)

# Rate level (single-tenor)
from rates_agent.ois.tools.schemas.rate_level import (
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

# Meeting pricing (central-bank meeting calendar × OIS curve)
from rates_agent.ois.tools.schemas.meeting_pricing import (
    OISMeetingPricingCurrentMetrics,
    OISMeetingPricingInput,
    OISMeetingPricingMeetingRow,
    OISMeetingPricingOutput,
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
    # meeting_pricing
    "OISMeetingPricingCurrentMetrics",
    "OISMeetingPricingInput",
    "OISMeetingPricingMeetingRow",
    "OISMeetingPricingOutput",
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

"""
Sovereign Bonds tool schemas — re-export hub.

External code imports from here::

    from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput
    from rates_agent.sovereign_bonds.tools.schemas import ScannerOutput
"""

# Spread
from rates_agent.sovereign_bonds.tools.schemas.spread import (
    CurveSpreadInput,
    CurveSpreadCurrentMetrics,
    CurveSpreadTimeSeriesRow,
    CurveSpreadOutput,
)

# Yield Level
from rates_agent.sovereign_bonds.tools.schemas.yield_level import (
    YieldLevelInput,
    YieldLevelMetrics,
    YieldLevelOutput,
)

# Cross-Market Spread
from rates_agent.sovereign_bonds.tools.schemas.cross_market import (
    CrossMarketSpreadInput,
    CrossMarketSpreadCurrentMetrics,
    CrossMarketSpreadTimeSeriesRow,
    CrossMarketSpreadOutput,
)

# Butterfly
from rates_agent.sovereign_bonds.tools.schemas.butterfly import (
    ButterflyInput,
    ButterflyCurrentMetrics,
    ButterflyTimeSeriesRow,
    ButterflyOutput,
)

# Regime
from rates_agent.sovereign_bonds.tools.schemas.regime import (
    CurveRegimeInput,
    CurveRegimeCurrentMetrics,
    CurveRegimeOutput,
)

# Scanner
from rates_agent.sovereign_bonds.tools.schemas.scanner import (
    ScannerInput,
    ScannerResultRow,
    ScannerOutput,
)

__all__ = [
    "CurveSpreadInput", "CurveSpreadCurrentMetrics", "CurveSpreadTimeSeriesRow", "CurveSpreadOutput",
    "YieldLevelInput", "YieldLevelMetrics", "YieldLevelOutput",
    "CrossMarketSpreadInput", "CrossMarketSpreadCurrentMetrics", "CrossMarketSpreadTimeSeriesRow", "CrossMarketSpreadOutput",
    "ButterflyInput", "ButterflyCurrentMetrics", "ButterflyTimeSeriesRow", "ButterflyOutput",
    "CurveRegimeInput", "CurveRegimeCurrentMetrics", "CurveRegimeOutput",
    "ScannerInput", "ScannerResultRow", "ScannerOutput",
]

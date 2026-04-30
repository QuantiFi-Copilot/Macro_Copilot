"""
Sovereign Bonds tool schemas — re-export hub.

External code imports from here::

    from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput
    from rates_agent.sovereign_bonds.tools.schemas import ScannerOutput

Migration note (commit 5 of the tool-config pilot):
The CurveSpread* models are now sourced from
``rates_agent.sovereign_bonds.tools.curve_spread.schemas`` (the
canonical per-tool-folder location).  The legacy
``rates_agent.sovereign_bonds.tools.schemas.spread`` shim was deleted.
This hub continues to re-export the same names so all existing
``from ...tools.schemas import CurveSpreadInput`` imports keep
working unchanged.

Other schemas (yield_level, cross_market, butterfly, regime, scanner)
remain in this directory until their respective tools migrate to the
per-tool-folder layout.
"""

# Spread — canonical location is ...tools.curve_spread.schemas as of
# commit 3 of the tool-config pilot.  The legacy shim
# ...tools.schemas.spread was deleted in commit 5.
from rates_agent.sovereign_bonds.tools.curve_spread.schemas import (
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

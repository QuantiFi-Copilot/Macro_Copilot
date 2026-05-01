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

# Yield Level — canonical location moved to
# ...tools.yield_levels.schemas in the yield_levels migration.  The
# legacy ...tools.schemas.yield_level shim was deleted in the same
# commit.  Hub continues to re-export the same names so existing
# `from ...tools.schemas import YieldLevelInput` imports keep working.
from rates_agent.sovereign_bonds.tools.yield_levels.schemas import (
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

# Butterfly — canonical location moved to
# ...tools.butterfly.schemas in the butterfly migration commit.  The
# legacy ...tools.schemas.butterfly shim was deleted in the same
# commit.  Hub continues to re-export the same names so existing
# `from ...tools.schemas import ButterflyInput` imports keep working.
from rates_agent.sovereign_bonds.tools.butterfly.schemas import (
    ButterflyInput,
    ButterflyCurrentMetrics,
    ButterflyTimeSeriesRow,
    ButterflyOutput,
)

# Curve-move classifier — canonical location moved to
# ...tools.curve_move_classifier.schemas in the curve-move-classifier
# migration commit.  The previous schemas were named ``CurveRegime*``;
# they are renamed ``CurveMove*`` because the tool classifies a single
# observed move, NOT a persistence state ("regime" in the technical
# rates / macro sense).  The legacy ``...tools.schemas.regime`` shim
# was deleted in the same commit.
from rates_agent.sovereign_bonds.tools.curve_move_classifier.schemas import (
    CurveMoveInput,
    CurveMoveCurrentMetrics,
    CurveMoveOutput,
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
    "CurveMoveInput", "CurveMoveCurrentMetrics", "CurveMoveOutput",
    "ScannerInput", "ScannerResultRow", "ScannerOutput",
]

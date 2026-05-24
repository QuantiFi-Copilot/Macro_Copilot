"""
Sovereign Bonds tool schemas — re-export hub.

External code imports from here::

    from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput
    from rates_agent.sovereign_bonds.tools.schemas import ScannerOutput

Migration status (per-tool-folder + config-driven pilot):
- curve_spread, yield_levels, curve_move_classifier, butterfly,
  cross_market_spread: MIGRATED.  Schemas live in their per-tool
  packages (``...tools.<name>.schemas``); the legacy
  ``...tools.schemas.<name>`` shims have been deleted.  This hub
  continues to re-export the same names so existing imports
  (``from ...tools.schemas import CrossMarketSpreadInput``) keep
  working.
- scanner: still lives in this directory; it will move to the
  per-tool-folder layout when its tool migrates.
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

# Cross-Market Spread — canonical location moved to
# ...tools.cross_market_spread.schemas in the cross_market_spread
# migration commit.  The legacy ...tools.schemas.cross_market shim was
# deleted in the same commit.  Hub continues to re-export the same
# names so existing `from ...tools.schemas import CrossMarketSpreadInput`
# imports keep working.
from rates_agent.sovereign_bonds.tools.cross_market_spread.schemas import (
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

# OTR-history monitor — categorical / SCD2 output, not bridge-composable
# (see get_otr_history/schemas.py docstring).  Re-exported here so
# external code can import from the schemas hub uniformly with the
# other sovereign tools.
from rates_agent.sovereign_bonds.tools.get_otr_history.schemas import (
    OtrHistoryInput,
    OtrHistoryCurrentMetrics,
    OtrHistoryTransitionRow,
    OtrHistoryOutput,
)

# OTR/OFR spread — desk-recognised rich-cheap signal built on the
# cash-bond substrate.  Bridge-composable (Series shape, snapshot +
# canonical TimeSeries).  See otr_ofr_spread/schemas.py docstring.
from rates_agent.sovereign_bonds.tools.otr_ofr_spread.schemas import (
    OtrOfrSpreadInput,
    OtrOfrSpreadCurrentMetrics,
    OtrOfrSpreadTimeSeriesRow,
    OtrOfrSpreadOutput,
)

# US NFP surprise (per-release actual − consensus_median + rolling
# release-window z-score) — front-end Treasury desk concept.
# Bridge-composable (snapshot + canonical TimeSeries).  See
# nfp_surprise/schemas.py docstring.
from rates_agent.sovereign_bonds.tools.nfp_surprise.schemas import (
    NfpSurpriseInput,
    NfpSurpriseCurrentMetrics,
    NfpSurpriseTimeSeriesRow,
    NfpSurpriseOutput,
)

__all__ = [
    "CurveSpreadInput", "CurveSpreadCurrentMetrics", "CurveSpreadTimeSeriesRow", "CurveSpreadOutput",
    "YieldLevelInput", "YieldLevelMetrics", "YieldLevelOutput",
    "CrossMarketSpreadInput", "CrossMarketSpreadCurrentMetrics", "CrossMarketSpreadTimeSeriesRow", "CrossMarketSpreadOutput",
    "ButterflyInput", "ButterflyCurrentMetrics", "ButterflyTimeSeriesRow", "ButterflyOutput",
    "CurveMoveInput", "CurveMoveCurrentMetrics", "CurveMoveOutput",
    "ScannerInput", "ScannerResultRow", "ScannerOutput",
    "OtrHistoryInput", "OtrHistoryCurrentMetrics", "OtrHistoryTransitionRow", "OtrHistoryOutput",
    "OtrOfrSpreadInput", "OtrOfrSpreadCurrentMetrics", "OtrOfrSpreadTimeSeriesRow", "OtrOfrSpreadOutput",
    "NfpSurpriseInput", "NfpSurpriseCurrentMetrics", "NfpSurpriseTimeSeriesRow", "NfpSurpriseOutput",
]

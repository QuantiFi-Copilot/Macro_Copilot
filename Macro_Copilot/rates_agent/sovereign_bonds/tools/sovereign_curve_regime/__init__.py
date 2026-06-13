"""sovereign_curve_regime — the first Bucket-2 model-state primitive.

Stable re-exports so callers can ``from rates_agent.sovereign_bonds.
tools.sovereign_curve_regime import calculate_sovereign_curve_regime``
without reaching into submodules.
"""

from rates_agent.sovereign_bonds.tools.sovereign_curve_regime.compute import (
    CONFIG_PATH,
    calculate_sovereign_curve_regime,
)
from rates_agent.sovereign_bonds.tools.sovereign_curve_regime.schemas import (
    RegimeFeatureMeans,
    SovereignCurveRegimeCurrentMetrics,
    SovereignCurveRegimeInput,
    SovereignCurveRegimeOutput,
    SovereignCurveRegimeTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_sovereign_curve_regime",
    "SovereignCurveRegimeInput",
    "RegimeFeatureMeans",
    "SovereignCurveRegimeCurrentMetrics",
    "SovereignCurveRegimeTimeSeriesRow",
    "SovereignCurveRegimeOutput",
]

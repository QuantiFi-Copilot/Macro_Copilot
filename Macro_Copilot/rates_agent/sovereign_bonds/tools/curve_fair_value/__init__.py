"""curve_fair_value — Bucket-2 PCA curve fair-value primitive.

Stable re-exports.
"""

from rates_agent.sovereign_bonds.tools.curve_fair_value.compute import (
    CONFIG_PATH,
    calculate_curve_fair_value,
)
from rates_agent.sovereign_bonds.tools.curve_fair_value.schemas import (
    CurveFairValueCurrentMetrics,
    CurveFairValueInput,
    CurveFairValueOutput,
    CurveFairValueTimeSeriesRow,
    TenorRichness,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_curve_fair_value",
    "CurveFairValueInput",
    "TenorRichness",
    "CurveFairValueCurrentMetrics",
    "CurveFairValueTimeSeriesRow",
    "CurveFairValueOutput",
]

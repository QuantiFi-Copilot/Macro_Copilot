"""
Re-export shim for the curve_spread schemas.

The canonical location moved to
``rates_agent.sovereign_bonds.tools.curve_spread.schemas`` in commit 3
of the tool-config pilot.  This shim keeps the legacy import path
working so existing callers (and the schemas/__init__.py re-export hub)
don't need to change in this commit.

The shim — and the corresponding entry in this package's __init__.py —
will be removed in commit 5 once every caller has migrated to the
new path.
"""

from rates_agent.sovereign_bonds.tools.curve_spread.schemas import (
    CurveSpreadCurrentMetrics,
    CurveSpreadInput,
    CurveSpreadOutput,
    CurveSpreadTimeSeriesRow,
)

__all__ = [
    "CurveSpreadInput",
    "CurveSpreadCurrentMetrics",
    "CurveSpreadTimeSeriesRow",
    "CurveSpreadOutput",
]

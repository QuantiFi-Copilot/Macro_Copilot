"""
rates_agent.sovereign_bonds.tools.curve_spread — config-driven curve-spread tool.

This package replaces the legacy single-file
``rates_agent/sovereign_bonds/tools/curve_spread.py`` with a
folder-per-tool layout:

    curve_spread/
      __init__.py    (this file — public-API re-exports)
      config.yaml    (conventions + methodology metadata)
      schemas.py     (Pydantic input / output models)
      compute.py     (deterministic math, config-driven)

Backward-compat
---------------
External callers continue to import ``calculate_curve_spread`` and
the schema classes from this package's path:

    from rates_agent.sovereign_bonds.tools.curve_spread import calculate_curve_spread
    from rates_agent.sovereign_bonds.tools.schemas import CurveSpreadInput

The first works because this ``__init__.py`` re-exports it.  The second
works because ``tools/schemas/spread.py`` is now a re-export shim
pointing at this package's ``schemas`` module.

Note for tests
--------------
``__init__.py`` re-exports ONLY the public symbols (``calculate_curve_spread``
and the schema classes).  Test seams like ``fetch_tenor_pair`` and
``date`` live inside ``compute.py``'s namespace, so tests that need to
patch them must target ``...curve_spread.compute.fetch_tenor_pair``,
not ``...curve_spread.fetch_tenor_pair``.
"""

from rates_agent.sovereign_bonds.tools.curve_spread.compute import (
    CONFIG_PATH,
    calculate_curve_spread,
)
from rates_agent.sovereign_bonds.tools.curve_spread.schemas import (
    CurveSpreadCurrentMetrics,
    CurveSpreadInput,
    CurveSpreadOutput,
    CurveSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_curve_spread",
    "CurveSpreadCurrentMetrics",
    "CurveSpreadInput",
    "CurveSpreadOutput",
    "CurveSpreadTimeSeriesRow",
]

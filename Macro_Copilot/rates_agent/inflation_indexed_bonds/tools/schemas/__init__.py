"""Re-exports for inflation_indexed_bonds tool schemas.

Mirrors the sovereign + OIS schema-hub pattern: per-tool-folder packages
own the canonical schema definitions, this hub re-exports them under
short names for callers that prefer the hub-style import.

External code can choose either path::

    # Direct (preferred for new code)
    from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
        RealYieldLevelInput,
    )

    # Via the hub (matches sovereign / OIS convention)
    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        RealYieldLevelInput,
    )
"""

# Real yield level (single-tenor) — re-exported from the
# per-tool-folder package
# (``rates_agent/inflation_indexed_bonds/tools/real_yield_level/``).
from rates_agent.inflation_indexed_bonds.tools.real_yield_level.schemas import (
    RealYieldLevelInput,
    RealYieldLevelMetrics,
    RealYieldLevelOutput,
)

# Breakeven inflation (simple two-leg primitive) — re-exported from the
# per-tool-folder package
# (``rates_agent/inflation_indexed_bonds/tools/breakeven_inflation_simple/``).
from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.schemas import (
    BreakevenInflationSimpleCurrentMetrics,
    BreakevenInflationSimpleInput,
    BreakevenInflationSimpleOutput,
    BreakevenInflationSimpleTimeSeriesRow,
)

# Forward breakeven inflation (simple, year-weighted linear) — re-
# exported from the per-tool-folder package
# (``rates_agent/inflation_indexed_bonds/tools/forward_breakeven_simple/``).
# Composes ``breakeven_inflation_simple`` twice (once per endpoint
# tenor) into a year-weighted forward; inherits the spot primitive's
# no-proxy guard and same-country invariant transitively.
from rates_agent.inflation_indexed_bonds.tools.forward_breakeven_simple.schemas import (
    ForwardBreakevenSimpleCurrentMetrics,
    ForwardBreakevenSimpleInput,
    ForwardBreakevenSimpleOutput,
    ForwardBreakevenSimpleTimeSeriesRow,
)

# Breakeven curve spread (same-country, two-tenor difference) — re-
# exported from the per-tool-folder package
# (``rates_agent/inflation_indexed_bonds/tools/breakeven_curve_spread/``).
# Composes ``breakeven_inflation_simple`` twice (once per endpoint
# tenor) into a per-trade-date difference (the inflation-
# compensation term-structure object); inherits the spot primitive's
# no-proxy guard and same-country invariant transitively.
from rates_agent.inflation_indexed_bonds.tools.breakeven_curve_spread.schemas import (
    BreakevenCurveSpreadCurrentMetrics,
    BreakevenCurveSpreadInput,
    BreakevenCurveSpreadOutput,
    BreakevenCurveSpreadTimeSeriesRow,
)

# Cross-country breakeven spread (same-tenor, two-country difference)
# — re-exported from the per-tool-folder package
# (``rates_agent/inflation_indexed_bonds/tools/cross_country_breakeven_spread_simple/``).
# Composes ``breakeven_inflation_simple`` twice (once per country),
# each constrained to that country's own (nominal, linker) pair.
# Cross-country guard lives in this primitive's input validators
# (different countries required); the per-leg same-country invariant
# continues to live inside ``breakeven_inflation_simple``.
from rates_agent.inflation_indexed_bonds.tools.cross_country_breakeven_spread_simple.schemas import (
    CrossCountryBreakevenSpreadSimpleCurrentMetrics,
    CrossCountryBreakevenSpreadSimpleInput,
    CrossCountryBreakevenSpreadSimpleOutput,
    CrossCountryBreakevenSpreadSimpleTimeSeriesRow,
)


__all__ = [
    # real_yield_level
    "RealYieldLevelInput",
    "RealYieldLevelMetrics",
    "RealYieldLevelOutput",
    # breakeven_inflation_simple
    "BreakevenInflationSimpleInput",
    "BreakevenInflationSimpleCurrentMetrics",
    "BreakevenInflationSimpleOutput",
    "BreakevenInflationSimpleTimeSeriesRow",
    # forward_breakeven_simple
    "ForwardBreakevenSimpleInput",
    "ForwardBreakevenSimpleCurrentMetrics",
    "ForwardBreakevenSimpleOutput",
    "ForwardBreakevenSimpleTimeSeriesRow",
    # breakeven_curve_spread
    "BreakevenCurveSpreadInput",
    "BreakevenCurveSpreadCurrentMetrics",
    "BreakevenCurveSpreadOutput",
    "BreakevenCurveSpreadTimeSeriesRow",
    # cross_country_breakeven_spread_simple
    "CrossCountryBreakevenSpreadSimpleInput",
    "CrossCountryBreakevenSpreadSimpleCurrentMetrics",
    "CrossCountryBreakevenSpreadSimpleOutput",
    "CrossCountryBreakevenSpreadSimpleTimeSeriesRow",
]

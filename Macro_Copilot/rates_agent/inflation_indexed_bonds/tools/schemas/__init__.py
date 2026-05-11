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

# Real-yield curve spread (same-country, two-tenor difference on a
# single linker curve) — re-exported from the per-tool-folder
# package
# (``rates_agent/inflation_indexed_bonds/tools/real_yield_curve_spread/``).
# Composes ``real_yield_level`` twice (one per endpoint tenor) into a
# per-trade-date PERCENT-units difference — the real-yield curve-
# shape object.  Inherits the level primitive's no-proxy guard
# (instrument_type='inflation_linker') transitively; compute
# additionally re-asserts the curve_family identity on
# instrument_master before any market-data fetch fires.
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
    RealYieldCurveSpreadCurrentMetrics,
    RealYieldCurveSpreadInput,
    RealYieldCurveSpreadOutput,
    RealYieldCurveSpreadTimeSeriesRow,
)

# Cross-country real-yield spread (same-tenor, two-curve difference
# across two linker curves) — re-exported from the per-tool-folder
# package
# (``rates_agent/inflation_indexed_bonds/tools/cross_country_real_yield_spread_simple/``).
# Composes ``real_yield_level`` twice (one per curve_family), each
# at the same tenor.  Cross-country / cross-curve guard lives in
# this primitive's input validator (different curves required);
# the both-legs-must-be-linker invariant is enforced at compute
# time by a post-fetch identity guard on
# ``macro_data.instrument_master`` for BOTH curve_families.
from rates_agent.inflation_indexed_bonds.tools.cross_country_real_yield_spread_simple.schemas import (
    CrossCountryRealYieldSpreadSimpleCurrentMetrics,
    CrossCountryRealYieldSpreadSimpleInput,
    CrossCountryRealYieldSpreadSimpleOutput,
    CrossCountryRealYieldSpreadSimpleTimeSeriesRow,
)

# Real-yield butterfly (same-country, three-tenor curvature on a
# single linker curve) — re-exported from the per-tool-folder
# package
# (``rates_agent/inflation_indexed_bonds/tools/real_yield_butterfly/``).
# Composes ``real_yield_level`` three times (one per endpoint tenor)
# into a per-trade-date PERCENT-units fixed-simple-butterfly
# (``belly - 0.5*(short + long)``) — the real-yield curvature
# object.  Inherits the level primitive's no-proxy guard
# (instrument_type='inflation_linker') transitively; compute
# additionally re-asserts the curve_family identity on
# instrument_master before any market-data fetch fires.
from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.schemas import (
    RealYieldButterflyCurrentMetrics,
    RealYieldButterflyInput,
    RealYieldButterflyOutput,
    RealYieldButterflyTimeSeriesRow,
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
    # real_yield_curve_spread
    "RealYieldCurveSpreadInput",
    "RealYieldCurveSpreadCurrentMetrics",
    "RealYieldCurveSpreadOutput",
    "RealYieldCurveSpreadTimeSeriesRow",
    # cross_country_real_yield_spread_simple
    "CrossCountryRealYieldSpreadSimpleInput",
    "CrossCountryRealYieldSpreadSimpleCurrentMetrics",
    "CrossCountryRealYieldSpreadSimpleOutput",
    "CrossCountryRealYieldSpreadSimpleTimeSeriesRow",
    # real_yield_butterfly
    "RealYieldButterflyInput",
    "RealYieldButterflyCurrentMetrics",
    "RealYieldButterflyOutput",
    "RealYieldButterflyTimeSeriesRow",
]

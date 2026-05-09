"""Re-exports for inflation_swaps tool schemas.

Mirrors the sovereign_bonds / OIS / inflation_indexed_bonds schema-hub
pattern: per-tool-folder packages own the canonical schema definitions,
this hub re-exports them under short names for callers that prefer the
hub-style import.

External code can choose either path::

    # Direct (preferred for new code)
    from rates_agent.inflation_swaps.tools.inflation_swap_rate_level import (
        InflationSwapRateLevelInput,
    )

    # Via the hub (matches sovereign / OIS / linker convention)
    from rates_agent.inflation_swaps.tools.schemas import (
        InflationSwapRateLevelInput,
    )
"""

# Inflation-swap rate level (single curve pillar) — re-exported from the
# per-tool-folder package
# (``rates_agent/inflation_swaps/tools/inflation_swap_rate_level/``).
from rates_agent.inflation_swaps.tools.inflation_swap_rate_level.schemas import (
    InflationSwapRateLevelCurrentMetrics,
    InflationSwapRateLevelInput,
    InflationSwapRateLevelOutput,
    InflationSwapRateLevelTimeSeriesRow,
)

# Inflation-swap curve spread (same-curve, two-tenor) — re-exported
# from the per-tool-folder package
# (``rates_agent/inflation_swaps/tools/inflation_swap_curve_spread/``).
from rates_agent.inflation_swaps.tools.inflation_swap_curve_spread.schemas import (
    InflationSwapCurveSpreadCurrentMetrics,
    InflationSwapCurveSpreadInput,
    InflationSwapCurveSpreadOutput,
    InflationSwapCurveSpreadTimeSeriesRow,
)

# Inflation-swap forward (same-curve, two-tenor forward) — re-exported
# from the per-tool-folder package
# (``rates_agent/inflation_swaps/tools/inflation_swap_forward/``).
from rates_agent.inflation_swaps.tools.inflation_swap_forward.schemas import (
    InflationSwapForwardCurrentMetrics,
    InflationSwapForwardInput,
    InflationSwapForwardOutput,
    InflationSwapForwardTimeSeriesRow,
)

# Cross-market inflation-swap spread (same-tenor, two-curve) —
# re-exported from the per-tool-folder package
# (``rates_agent/inflation_swaps/tools/cross_market_inflation_swap_spread/``).
from rates_agent.inflation_swaps.tools.cross_market_inflation_swap_spread.schemas import (
    CrossMarketInflationSwapSpreadCurrentMetrics,
    CrossMarketInflationSwapSpreadInput,
    CrossMarketInflationSwapSpreadOutput,
    CrossMarketInflationSwapSpreadTimeSeriesRow,
)

# Swap-breakeven basis (ZCIS minus same-tenor, same-currency
# linker-implied breakeven) — re-exported from the per-tool-folder
# package
# (``rates_agent/inflation_swaps/tools/swap_breakeven_basis_simple/``).
from rates_agent.inflation_swaps.tools.swap_breakeven_basis_simple.schemas import (
    SwapBreakevenBasisSimpleCurrentMetrics,
    SwapBreakevenBasisSimpleInput,
    SwapBreakevenBasisSimpleOutput,
    SwapBreakevenBasisSimpleTimeSeriesRow,
)


__all__ = [
    # inflation_swap_rate_level
    "InflationSwapRateLevelInput",
    "InflationSwapRateLevelCurrentMetrics",
    "InflationSwapRateLevelTimeSeriesRow",
    "InflationSwapRateLevelOutput",
    # inflation_swap_curve_spread
    "InflationSwapCurveSpreadInput",
    "InflationSwapCurveSpreadCurrentMetrics",
    "InflationSwapCurveSpreadTimeSeriesRow",
    "InflationSwapCurveSpreadOutput",
    # inflation_swap_forward
    "InflationSwapForwardInput",
    "InflationSwapForwardCurrentMetrics",
    "InflationSwapForwardTimeSeriesRow",
    "InflationSwapForwardOutput",
    # cross_market_inflation_swap_spread
    "CrossMarketInflationSwapSpreadInput",
    "CrossMarketInflationSwapSpreadCurrentMetrics",
    "CrossMarketInflationSwapSpreadTimeSeriesRow",
    "CrossMarketInflationSwapSpreadOutput",
    # swap_breakeven_basis_simple
    "SwapBreakevenBasisSimpleInput",
    "SwapBreakevenBasisSimpleCurrentMetrics",
    "SwapBreakevenBasisSimpleTimeSeriesRow",
    "SwapBreakevenBasisSimpleOutput",
]

"""
rates_agent.sovereign_bonds.tools.rolling_regression — config-driven
rolling-OLS regression of one sovereign yield series on one or more
regressor yield series.

Second tool of the v6 sprint (after zscore_custom).  First tool with
nested Pydantic input shapes (`SeriesSpec`, `List[SeriesSpec]`),
proving the FastMCP nested-wrapper pattern that subsequent sprint
tools (`beta_adjusted_spread`, `half_life`,
`yield_change_attribution_pca`, `yield_change_decomposition_simple`)
can inherit.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.rolling_regression import (
        calculate_rolling_regression,
        RollingRegressionInput,
        RollingRegressionOutput,
        CONFIG_PATH,
    )

See docs/architecture/tool_architecture.md for the canonical layout.

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_tenor`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...rolling_regression.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.rolling_regression.compute import (
    CONFIG_PATH,
    calculate_rolling_regression,
)
from rates_agent.sovereign_bonds.tools.rolling_regression.schemas import (
    RollingRegressionInput,
    RollingRegressionMetrics,
    RollingRegressionOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_rolling_regression",
    "RollingRegressionInput",
    "RollingRegressionMetrics",
    "RollingRegressionOutput",
]

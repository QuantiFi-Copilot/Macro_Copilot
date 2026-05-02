"""
rates_agent.sovereign_bonds.tools.beta_adjusted_spread — config-driven
bivariate beta-adjusted RV (rolling OLS hedge ratio + bps residual +
residual z-score).

Third tool of the v6 sprint (after zscore_custom and
rolling_regression).  Built on top of
``shared.analytics.regression.rolling_ols`` so the bivariate
hedge-ratio math has a single authoritative implementation shared
with rolling_regression.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
        calculate_beta_adjusted_spread,
        BetaAdjustedSpreadInput,
        BetaAdjustedSpreadOutput,
        CONFIG_PATH,
    )

See docs/architecture/tool_architecture.md for the canonical layout.

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_tenor`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...beta_adjusted_spread.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute import (
    CONFIG_PATH,
    calculate_beta_adjusted_spread,
)
from rates_agent.sovereign_bonds.tools.beta_adjusted_spread.schemas import (
    BetaAdjustedSpreadInput,
    BetaAdjustedSpreadMetrics,
    BetaAdjustedSpreadOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_beta_adjusted_spread",
    "BetaAdjustedSpreadInput",
    "BetaAdjustedSpreadMetrics",
    "BetaAdjustedSpreadOutput",
]

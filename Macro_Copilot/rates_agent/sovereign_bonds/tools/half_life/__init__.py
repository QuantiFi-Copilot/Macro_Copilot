"""
rates_agent.sovereign_bonds.tools.half_life — config-driven OU / AR(1)
half-life of mean reversion.

Fourth tool of the v6 sprint (after zscore_custom, rolling_regression,
and beta_adjusted_spread).  Built on
``shared.analytics.stats.ou_half_life`` so the OU fit math has a
single authoritative implementation.

Three input variants (mutually exclusive — exactly one):
  * series_spec    — single (curve_family, tenor, field) sovereign yield
  * pair_spec      — cross-market spread (cf1 − cf2) × 100, in bps
  * pasted_series  — caller-supplied TimeSeries (units enum + rows)

Transport: MCP only this sprint (nested union input).  No FastAPI
route.  See docs/architecture/tool_architecture.md.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.half_life import (
        calculate_half_life,
        HalfLifeInput,
        HalfLifeOutput,
        CONFIG_PATH,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_single_tenor`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...half_life.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.half_life.compute import (
    CONFIG_PATH,
    calculate_half_life,
)
from rates_agent.sovereign_bonds.tools.half_life.schemas import (
    HalfLifeInput,
    HalfLifeMetrics,
    HalfLifeOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_half_life",
    "HalfLifeInput",
    "HalfLifeMetrics",
    "HalfLifeOutput",
]

"""
rates_agent.sovereign_bonds.tools.pca_yield_curve — config-driven PCA
on the yield-CHANGES panel of one sovereign curve.

Fifth tool of the v6 sprint.  Built on
``shared.analytics.stats.pca_yield_changes`` so the SVD math has a
single authoritative implementation that the next sprint tool
(yield_change_attribution_pca) consumes via PastedPcaLoadings or
fits inline.

Transport: flat scalars + ``tenors`` as a repeated query param →
**MCP and FastAPI GET** ship this sprint.  See
docs/architecture/tool_architecture.md.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
        calculate_pca_yield_curve,
        PcaYieldCurveInput,
        PcaYieldCurveOutput,
        CONFIG_PATH,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_tenor_group`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...pca_yield_curve.compute.<name>``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.pca_yield_curve.compute import (
    CONFIG_PATH,
    calculate_pca_yield_curve,
)
from rates_agent.sovereign_bonds.tools.pca_yield_curve.schemas import (
    LoadingRow,
    PcaComponentMetadata,
    PcaYieldCurveInput,
    PcaYieldCurveMetrics,
    PcaYieldCurveOutput,
    VarianceShareRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_pca_yield_curve",
    "PcaYieldCurveInput",
    "PcaYieldCurveMetrics",
    "PcaYieldCurveOutput",
    "LoadingRow",
    "VarianceShareRow",
    "PcaComponentMetadata",
]

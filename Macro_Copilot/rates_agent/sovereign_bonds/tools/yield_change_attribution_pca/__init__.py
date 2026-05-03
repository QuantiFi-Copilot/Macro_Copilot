"""
rates_agent.sovereign_bonds.tools.yield_change_attribution_pca —
config-driven PCA-based attribution of a sovereign yield change at
one tenor over a chosen calendar window.

Sixth tool of the v6 sprint (after zscore_custom, rolling_regression,
beta_adjusted_spread, half_life, pca_yield_curve).  Built on
``pca_yield_curve.calculate_pca_yield_curve`` (via the fit_inline
path) AND on ``shared.schemas.PastedPcaLoadings`` (via the pasted
path).  Both paths produce outputs fully reproducible from the
upstream config / provenance — see config.yaml's "Standardisation
under the v6 hierarchical-config principle" header.

Transport: MCP only this sprint (nested ``Optional[PastedPcaLoadings]``
input).  No FastAPI route until the UI-integration PR.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca import (
        calculate_yield_change_attribution_pca,
        YieldChangeAttributionPcaInput,
        YieldChangeAttributionPcaOutput,
        ComponentContribution,
        CONFIG_PATH,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``calculate_pca_yield_curve``, ``fetch_tenor_group``, and ``date``
all live inside ``compute.py``'s namespace; tests must patch them
at ``...yield_change_attribution_pca.compute.<name>``, NOT on the
package init.
"""

from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute import (
    CONFIG_PATH,
    calculate_yield_change_attribution_pca,
)
from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.schemas import (
    ComponentContribution,
    YieldChangeAttributionPcaInput,
    YieldChangeAttributionPcaMetrics,
    YieldChangeAttributionPcaOutput,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_yield_change_attribution_pca",
    "YieldChangeAttributionPcaInput",
    "YieldChangeAttributionPcaMetrics",
    "ComponentContribution",
    "YieldChangeAttributionPcaOutput",
]

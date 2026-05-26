"""
rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes —
universe-wide ZCIS quoted-rate sweep ranked by absolute 252-day
z-score of ZCIS rate LEVEL.

First universe-scan primitive in the ``inflation_swaps`` domain
(catalog id ``inflation_swaps__scan_inflation_swaps_extremes``,
build_order 25).  Composes the upstream per-pillar
``inflation_swap_rate_level`` primitive's concept across every ZCIS
(curve_family, tenor) pillar in the inflation_swaps universe.  See
``compute.py`` for the methodology + scope caveats and
``config.yaml`` for the convention layer (including the z-score
lookback window required by the catalog's methodology guardrail).

External callers reach the public API via this package's path::

    from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes import (
        calculate_scan_inflation_swaps_extremes,
        ScanInflationSwapsExtremesInput,
        ScanInflationSwapsExtremesOutput,
        CONFIG_PATH,
    )

Or via the inflation_swaps schemas hub::

    from rates_agent.inflation_swaps.tools.schemas import (
        ScanInflationSwapsExtremesInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_scan_universe``, ``fetch_scan_universe_reference``, and
``date`` live inside ``compute.py``'s namespace; tests must patch
them at
``...scan_inflation_swaps_extremes.compute.<name>``, NOT on the
package init.
"""

from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes.compute import (
    CONFIG_PATH,
    calculate_scan_inflation_swaps_extremes,
)
from rates_agent.inflation_swaps.tools.scan_inflation_swaps_extremes.schemas import (
    ScanInflationSwapsExtremesInput,
    ScanInflationSwapsExtremesOutput,
    ScanInflationSwapsExtremesResultRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_scan_inflation_swaps_extremes",
    "ScanInflationSwapsExtremesInput",
    "ScanInflationSwapsExtremesOutput",
    "ScanInflationSwapsExtremesResultRow",
]

"""
rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes —
universe-wide linker real-yield sweep ranked by absolute 252-day
z-score of real-yield LEVEL.

First universe-scan primitive in the ``inflation_indexed_bonds``
domain (catalog id
``inflation_linkers__scan_inflation_linkers_extremes``,
build_order 24).  Composes the upstream per-bond ``real_yield_level``
primitive's concept across every linker (curve_family, tenor) in
the inflation_indexed_bonds universe.  See ``compute.py`` for the
methodology + scope caveats and ``config.yaml`` for the convention
layer (including the z-score lookback window required by the
catalog's methodology guardrail).

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes import (
        calculate_scan_inflation_linkers_extremes,
        ScanInflationLinkersExtremesInput,
        ScanInflationLinkersExtremesOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        ScanInflationLinkersExtremesInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_scan_universe``, ``fetch_scan_universe_reference``, and
``date`` live inside ``compute.py``'s namespace; tests must patch
them at
``...scan_inflation_linkers_extremes.compute.<name>``, NOT on the
package init.
"""

from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes.compute import (
    CONFIG_PATH,
    calculate_scan_inflation_linkers_extremes,
)
from rates_agent.inflation_indexed_bonds.tools.scan_inflation_linkers_extremes.schemas import (
    ScanInflationLinkersExtremesInput,
    ScanInflationLinkersExtremesOutput,
    ScanInflationLinkersExtremesResultRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_scan_inflation_linkers_extremes",
    "ScanInflationLinkersExtremesInput",
    "ScanInflationLinkersExtremesOutput",
    "ScanInflationLinkersExtremesResultRow",
]

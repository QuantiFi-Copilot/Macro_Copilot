"""
rates_agent.bond_futures.tools.scan_bond_futures_extremes — universe-
wide front-month bond-futures sweep ranked by absolute 252-day z-score
across four metrics (price LEVEL, 1-day price CHANGE, volume LEVEL,
end-of-day open-interest LEVEL).

Third primitive under the ``bond_futures`` domain (ADR 0011 — V1
monitors-only). Replaces the Phase-4 inter-commodity DV01-weighted
spread stack per ADR 0011's V1 scope; the catalog's methodology
guardrail binds every output to disclose this scope explicitly. See
``compute.py`` for the methodology + scope caveat and ``config.yaml``
for the convention layer (including the z-score lookback window
required by the catalog's methodology guardrail).

External callers reach the public API via this package's path::

    from rates_agent.bond_futures.tools.scan_bond_futures_extremes import (
        calculate_scan_bond_futures_extremes,
        ScanBondFuturesExtremesInput,
        ScanBondFuturesExtremesOutput,
        CONFIG_PATH,
    )

Or via the bond_futures schemas hub::

    from rates_agent.bond_futures.tools.schemas import (
        ScanBondFuturesExtremesInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_rolling_generic_universe_series`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...scan_bond_futures_extremes.compute.<name>``, NOT on the package
init.
"""

from rates_agent.bond_futures.tools.scan_bond_futures_extremes.compute import (
    CONFIG_PATH,
    calculate_scan_bond_futures_extremes,
)
from rates_agent.bond_futures.tools.scan_bond_futures_extremes.schemas import (
    ScanBondFuturesExtremesInput,
    ScanBondFuturesExtremesOutput,
    ScanBondFuturesExtremesResultRow,
    ScanMetric,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_scan_bond_futures_extremes",
    "ScanBondFuturesExtremesInput",
    "ScanBondFuturesExtremesOutput",
    "ScanBondFuturesExtremesResultRow",
    "ScanMetric",
]

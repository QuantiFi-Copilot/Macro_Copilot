"""
rates_agent.bond_futures.tools.futures_volume_oi — config-driven
front-month bond-futures volume + open-interest monitor.

Second primitive under the ``bond_futures`` domain (ADR 0013 — V1
monitors-only). See ``compute.py`` for the methodology + scope
caveat and ``config.yaml`` for the convention layer (including the
OI z-score lookback window required by the catalog's methodology
guardrail).

External callers reach the public API via this package's path:

    from rates_agent.bond_futures.tools.futures_volume_oi import (
        calculate_futures_volume_oi,
        FuturesVolumeOIInput,
        FuturesVolumeOIOutput,
        CONFIG_PATH,
    )

Or via the bond_futures schemas hub:

    from rates_agent.bond_futures.tools.schemas import FuturesVolumeOIInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_rolling_generic_series`` / ``fetch_rolling_generic_reference``
and ``date`` live inside ``compute.py``'s namespace; tests must patch
them at ``...futures_volume_oi.compute.<name>``, NOT on the package
init.
"""

from rates_agent.bond_futures.tools.futures_volume_oi.compute import (
    CONFIG_PATH,
    calculate_futures_volume_oi,
)
from rates_agent.bond_futures.tools.futures_volume_oi.schemas import (
    FuturesVolumeOICurrentMetrics,
    FuturesVolumeOIInput,
    FuturesVolumeOIOutput,
    FuturesVolumeOITimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_futures_volume_oi",
    "FuturesVolumeOIInput",
    "FuturesVolumeOICurrentMetrics",
    "FuturesVolumeOITimeSeriesRow",
    "FuturesVolumeOIOutput",
]

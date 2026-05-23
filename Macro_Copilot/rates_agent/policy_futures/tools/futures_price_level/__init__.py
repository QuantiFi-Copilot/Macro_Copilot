"""
rates_agent.policy_futures.tools.futures_price_level — config-driven
strip-position price-level + implied-rate monitor.

First primitive under the new ``policy_futures`` domain (ADR 0011 —
strip-position-keyed monitors). See ``compute.py`` for the methodology
+ scope caveat and ``config.yaml`` for the convention layer.

External callers reach the public API via this package's path:

    from rates_agent.policy_futures.tools.futures_price_level import (
        calculate_futures_price_level,
        FuturesPriceLevelInput,
        FuturesPriceLevelOutput,
        CONFIG_PATH,
    )

Or via the policy_futures schemas hub:

    from rates_agent.policy_futures.tools.schemas import (
        FuturesPriceLevelInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_strip_position`` / ``fetch_strip_position_reference`` /
``fetch_strip_position_max_date`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...futures_price_level.compute.<name>``, NOT on the package init.
"""

from rates_agent.policy_futures.tools.futures_price_level.compute import (
    CONFIG_PATH,
    calculate_futures_price_level,
)
from rates_agent.policy_futures.tools.futures_price_level.schemas import (
    FuturesPriceLevelCurrentMetrics,
    FuturesPriceLevelInput,
    FuturesPriceLevelOutput,
    FuturesPriceLevelTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_futures_price_level",
    "FuturesPriceLevelInput",
    "FuturesPriceLevelCurrentMetrics",
    "FuturesPriceLevelTimeSeriesRow",
    "FuturesPriceLevelOutput",
]

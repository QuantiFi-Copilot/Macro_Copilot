"""
rates_agent.policy_futures.tools.futures_cross_market_spread —
config-driven matched-strip cross-market implied-rate differential
monitor (V1).

Fifth primitive under the ``policy_futures`` domain (ADR 0013 —
strip-position-keyed monitors). The single-leg price/implied-rate
read lives on the sibling ``futures_price_level`` tool; the volume /
open-interest snapshot lives on
``volume_open_interest_snapshot``; the two-leg same-curve calendar
spread lives on ``futures_calendar_spread``; the three-leg same-
curve simple butterfly lives on ``futures_butterfly_simple``; this
primitive owns the matched-strip CROSS-MARKET implied-rate
differential between TWO different ``curve_family`` values at ONE
strip position.

See ``compute.py`` for the methodology + ADR 0013 scope caveat,
``schemas.py`` for the sign convention + wire-frozen field-name
discipline, and ``config.yaml`` for the convention layer (z window,
trailing range window, ffill limit, rounding precisions, regime
map, A−B orientation disclosure).

External callers reach the public API via this package's path:

    from rates_agent.policy_futures.tools.futures_cross_market_spread import (
        calculate_futures_cross_market_spread,
        FuturesCrossMarketSpreadInput,
        FuturesCrossMarketSpreadOutput,
        CONFIG_PATH,
    )

Or via the policy_futures schemas hub:

    from rates_agent.policy_futures.tools.schemas import (
        FuturesCrossMarketSpreadInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_cross_market_strip`` / ``fetch_strip_position_reference`` /
``fetch_strip_position_max_date`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...futures_cross_market_spread.compute.<name>``, NOT on the
package init.
"""

from rates_agent.policy_futures.tools.futures_cross_market_spread.compute import (
    CONFIG_PATH,
    calculate_futures_cross_market_spread,
)
from rates_agent.policy_futures.tools.futures_cross_market_spread.schemas import (
    FuturesCrossMarketSpreadCurrentMetrics,
    FuturesCrossMarketSpreadInput,
    FuturesCrossMarketSpreadOutput,
    FuturesCrossMarketSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_futures_cross_market_spread",
    "FuturesCrossMarketSpreadInput",
    "FuturesCrossMarketSpreadCurrentMetrics",
    "FuturesCrossMarketSpreadTimeSeriesRow",
    "FuturesCrossMarketSpreadOutput",
]

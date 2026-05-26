"""
rates_agent.policy_futures.tools.futures_calendar_spread — config-driven
same-curve calendar-spread monitor (V1).

Third primitive under the ``policy_futures`` domain (ADR 0013 —
strip-position-keyed monitors). The single-leg price/implied-rate
read lives on the sibling ``futures_price_level`` tool and the
volume / open-interest snapshot lives on
``volume_open_interest_snapshot``; this primitive owns the
calendar-spread (strip-slope) concept on the implied-rate axis for
one ``(curve_family, strip_position_short, strip_position_long)``
triple.

See ``compute.py`` for the methodology + ADR 0013 scope caveat,
``schemas.py`` for the sign convention + wire-frozen field-name
discipline, and ``config.yaml`` for the convention layer (z window,
trailing range window, ffill limit, rounding precisions, regime
map).

External callers reach the public API via this package's path:

    from rates_agent.policy_futures.tools.futures_calendar_spread import (
        calculate_futures_calendar_spread,
        FuturesCalendarSpreadInput,
        FuturesCalendarSpreadOutput,
        CONFIG_PATH,
    )

Or via the policy_futures schemas hub:

    from rates_agent.policy_futures.tools.schemas import (
        FuturesCalendarSpreadInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_strip_group`` / ``fetch_strip_position_reference`` /
``fetch_strip_position_max_date`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...futures_calendar_spread.compute.<name>``, NOT on the package
init.
"""

from rates_agent.policy_futures.tools.futures_calendar_spread.compute import (
    CONFIG_PATH,
    calculate_futures_calendar_spread,
)
from rates_agent.policy_futures.tools.futures_calendar_spread.schemas import (
    FuturesCalendarSpreadCurrentMetrics,
    FuturesCalendarSpreadInput,
    FuturesCalendarSpreadOutput,
    FuturesCalendarSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_futures_calendar_spread",
    "FuturesCalendarSpreadInput",
    "FuturesCalendarSpreadCurrentMetrics",
    "FuturesCalendarSpreadTimeSeriesRow",
    "FuturesCalendarSpreadOutput",
]

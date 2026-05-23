"""
rates_agent.policy_futures.tools.futures_butterfly_simple — config-
driven same-curve simple-butterfly monitor (V1).

Fourth primitive under the ``policy_futures`` domain (ADR 0011 —
strip-position-keyed monitors). The single-leg price/implied-rate
read lives on the sibling ``futures_price_level`` tool; the volume
/ open-interest snapshot lives on
``volume_open_interest_snapshot``; the two-leg calendar spread lives
on ``futures_calendar_spread``; and this primitive owns the
three-leg curvature (simple-butterfly) concept on the implied-rate
axis for one ``(curve_family, strip_position_wing_short,
strip_position_body, strip_position_wing_long)`` triple. Uses the
canonical FIXED 50-50 simple-butterfly weighting:
``butterfly_value_pct = body − 0.5 * (wing_short + wing_long)``.

See ``compute.py`` for the methodology + ADR 0011 scope caveat,
``schemas.py`` for the sign convention + wire-frozen field-name
discipline, and ``config.yaml`` for the convention layer (z window,
trailing range window, ffill limit, rounding precisions, regime
map, butterfly-weighting disclosure block).

External callers reach the public API via this package's path:

    from rates_agent.policy_futures.tools.futures_butterfly_simple import (
        calculate_futures_butterfly_simple,
        FuturesButterflySimpleInput,
        FuturesButterflySimpleOutput,
        CONFIG_PATH,
    )

Or via the policy_futures schemas hub:

    from rates_agent.policy_futures.tools.schemas import (
        FuturesButterflySimpleInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_strip_group`` / ``fetch_strip_position_reference`` /
``fetch_strip_position_max_date`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...futures_butterfly_simple.compute.<name>``, NOT on the package
init.
"""

from rates_agent.policy_futures.tools.futures_butterfly_simple.compute import (
    CONFIG_PATH,
    calculate_futures_butterfly_simple,
)
from rates_agent.policy_futures.tools.futures_butterfly_simple.schemas import (
    FuturesButterflySimpleCurrentMetrics,
    FuturesButterflySimpleInput,
    FuturesButterflySimpleOutput,
    FuturesButterflySimpleTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_futures_butterfly_simple",
    "FuturesButterflySimpleInput",
    "FuturesButterflySimpleCurrentMetrics",
    "FuturesButterflySimpleTimeSeriesRow",
    "FuturesButterflySimpleOutput",
]

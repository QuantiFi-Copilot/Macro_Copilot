"""
rates_agent.policy_futures.tools.futures_pack_average_simple —
config-driven same-curve pack-average implied rate monitor (V1).

Fifth (catalog #28) primitive under the ``policy_futures`` domain
(ADR 0013 — strip-position-keyed monitors). Owns the desk-recognised
"pack average" concept on the implied-rate axis: the arithmetic mean
of four same-curve strip-position legs (whites = strip positions
1-4; reds = strip positions 5-8) on ONE ``curve_family``.

See ``compute.py`` for the methodology + the ADR 0013 V1
EUR_SHORT_RATE_FUT refusal gate (the playbook does not yet carry
``delivery_month_type`` metadata to disambiguate the serial-vs-
quarterly mix in the Euribor strip), ``schemas.py`` for the pack
input enum + wire-frozen field-name discipline, and ``config.yaml``
for the convention layer (whites/reds strip-position ranges, z
window, trailing range window, ffill limit, rounding precisions,
regime map, simple-mean weighting disclosure block).

External callers reach the public API via this package's path:

    from rates_agent.policy_futures.tools.futures_pack_average_simple import (
        calculate_futures_pack_average_simple,
        FuturesPackAverageSimpleInput,
        FuturesPackAverageSimpleOutput,
        CONFIG_PATH,
    )

Or via the policy_futures schemas hub:

    from rates_agent.policy_futures.tools.schemas import (
        FuturesPackAverageSimpleInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_strip_group`` / ``fetch_strip_position_reference`` /
``fetch_strip_position_max_date`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...futures_pack_average_simple.compute.<name>``, NOT on the
package init.
"""

from rates_agent.policy_futures.tools.futures_pack_average_simple.compute import (
    CONFIG_PATH,
    calculate_futures_pack_average_simple,
)
from rates_agent.policy_futures.tools.futures_pack_average_simple.schemas import (
    FuturesPackAverageSimpleCurrentMetrics,
    FuturesPackAverageSimpleInput,
    FuturesPackAverageSimpleOutput,
    FuturesPackAverageSimpleTimeSeriesRow,
    PolicyFuturesPack,
    PolicyFuturesPackAverageCurveFamily,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_futures_pack_average_simple",
    "FuturesPackAverageSimpleInput",
    "FuturesPackAverageSimpleCurrentMetrics",
    "FuturesPackAverageSimpleTimeSeriesRow",
    "FuturesPackAverageSimpleOutput",
    "PolicyFuturesPack",
    "PolicyFuturesPackAverageCurveFamily",
]

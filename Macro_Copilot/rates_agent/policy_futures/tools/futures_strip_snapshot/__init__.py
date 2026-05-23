"""
rates_agent.policy_futures.tools.futures_strip_snapshot — whole-strip
snapshot for one policy-futures curve_family.

V1 primitive under the ``policy_futures`` domain (ADR 0011 — strip-
position-keyed monitors). Returns one row per configured strip
position (V1 default: positions 1..8) with the per-leg raw_price,
implied_rate_pct, daily_change_implied_rate_pct, rolling 252-day
z-score, open_interest, and a per-row methodology card.

External callers reach the public API via this package's path:

    from rates_agent.policy_futures.tools.futures_strip_snapshot import (
        calculate_futures_strip_snapshot,
        FuturesStripSnapshotInput,
        FuturesStripSnapshotOutput,
        CONFIG_PATH,
    )

Or via the policy_futures schemas hub:

    from rates_agent.policy_futures.tools.schemas import (
        FuturesStripSnapshotInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_strip_group`` / ``fetch_strip_position_reference`` /
``fetch_strip_position_max_date`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...futures_strip_snapshot.compute.<name>``, NOT on the package init.
"""

from rates_agent.policy_futures.tools.futures_strip_snapshot.compute import (
    CONFIG_PATH,
    calculate_futures_strip_snapshot,
)
from rates_agent.policy_futures.tools.futures_strip_snapshot.schemas import (
    FuturesStripSnapshotInput,
    FuturesStripSnapshotOutput,
    FuturesStripSnapshotRow,
    PolicyFuturesCurveFamily,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_futures_strip_snapshot",
    "FuturesStripSnapshotInput",
    "FuturesStripSnapshotRow",
    "FuturesStripSnapshotOutput",
    "PolicyFuturesCurveFamily",
]

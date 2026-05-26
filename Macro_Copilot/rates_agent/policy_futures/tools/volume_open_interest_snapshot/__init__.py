"""
rates_agent.policy_futures.tools.volume_open_interest_snapshot —
config-driven strip-position volume + open-interest snapshot.

Second primitive under the ``policy_futures`` domain (ADR 0013 —
strip-position-keyed monitors). The price/implied-rate read lives on
the sibling ``futures_price_level`` tool; this primitive owns the
distinct positioning / flow concept (volume + open interest + ΔOI +
OI z-score + percentile-in-range + short-window volume context) for
one ``(curve_family, strip_position)`` pair.

See ``compute.py`` for the methodology + ADR 0013 scope caveat and
``config.yaml`` for the convention layer (OI z-score window, rolling
volume window, OI trailing range window, Bloomberg field mnemonics).

External callers reach the public API via this package's path:

    from rates_agent.policy_futures.tools.volume_open_interest_snapshot import (
        calculate_volume_open_interest_snapshot,
        VolumeOpenInterestSnapshotInput,
        VolumeOpenInterestSnapshotOutput,
        CONFIG_PATH,
    )

Or via the policy_futures schemas hub:

    from rates_agent.policy_futures.tools.schemas import (
        VolumeOpenInterestSnapshotInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_strip_position`` / ``fetch_strip_position_reference`` /
``fetch_strip_position_max_date`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...volume_open_interest_snapshot.compute.<name>``, NOT on the
package init.
"""

from rates_agent.policy_futures.tools.volume_open_interest_snapshot.compute import (
    CONFIG_PATH,
    calculate_volume_open_interest_snapshot,
)
from rates_agent.policy_futures.tools.volume_open_interest_snapshot.schemas import (
    VolumeOpenInterestSnapshotCurrentMetrics,
    VolumeOpenInterestSnapshotInput,
    VolumeOpenInterestSnapshotOutput,
    VolumeOpenInterestSnapshotTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_volume_open_interest_snapshot",
    "VolumeOpenInterestSnapshotInput",
    "VolumeOpenInterestSnapshotCurrentMetrics",
    "VolumeOpenInterestSnapshotTimeSeriesRow",
    "VolumeOpenInterestSnapshotOutput",
]

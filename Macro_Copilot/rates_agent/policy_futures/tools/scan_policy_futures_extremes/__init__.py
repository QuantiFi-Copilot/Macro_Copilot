"""
rates_agent.policy_futures.tools.scan_policy_futures_extremes —
universe-wide policy-futures strip sweep ranked by absolute 252-day
z-score across four metrics (implied_rate_level, implied_rate_change
in bps, volume_level, open_interest_level).

Eighth and final primitive under the ``policy_futures`` domain
(ADR 0013 — V1 strip-position-keyed monitors). The catalog's
methodology guardrail binds every output row to disclose the
universe-wide-strip-scan label, the explicit z-score lookback
window, the per-row RFR-vs-IBOR regime caveat per curve_family per
ADR 0013, the inverse-pricing rule, and the rolling-generic-strip
caveat. See ``compute.py`` for the methodology + scope caveat and
``config.yaml`` for the convention layer (including the z-score
lookback window required by the catalog's methodology guardrail).

External callers reach the public API via this package's path::

    from rates_agent.policy_futures.tools.scan_policy_futures_extremes import (
        calculate_scan_policy_futures_extremes,
        ScanPolicyFuturesExtremesInput,
        ScanPolicyFuturesExtremesOutput,
        CONFIG_PATH,
    )

Or via the policy_futures schemas hub::

    from rates_agent.policy_futures.tools.schemas import (
        ScanPolicyFuturesExtremesInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_scan_universe_strip_position`` /
``fetch_scan_universe_policy_future_reference`` /
``fetch_scan_universe_strip_position_max_date`` and ``date`` live
inside ``compute.py``'s namespace; tests must patch them at
``...scan_policy_futures_extremes.compute.<name>``, NOT on the
package init.
"""

from rates_agent.policy_futures.tools.scan_policy_futures_extremes.compute import (
    CONFIG_PATH,
    calculate_scan_policy_futures_extremes,
)
from rates_agent.policy_futures.tools.scan_policy_futures_extremes.schemas import (
    PolicyFuturesScanCurveFamily,
    ScanMetric,
    ScanPolicyFuturesExtremesInput,
    ScanPolicyFuturesExtremesOutput,
    ScanPolicyFuturesExtremesResultRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_scan_policy_futures_extremes",
    "ScanMetric",
    "PolicyFuturesScanCurveFamily",
    "ScanPolicyFuturesExtremesInput",
    "ScanPolicyFuturesExtremesOutput",
    "ScanPolicyFuturesExtremesResultRow",
]

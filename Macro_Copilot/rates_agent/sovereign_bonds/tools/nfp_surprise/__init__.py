"""
rates_agent.sovereign_bonds.tools.nfp_surprise — config-driven US NFP-surprise
primitive.

For the US nonfarm-payrolls release series, computes the per-release
surprise = ``actual − consensus_median`` (in thousands of jobs) plus
a rolling z-score over a window of N RELEASES (not calendar days).
Built on the event-calendar substrate landed by ADR 0004 and
populated by the economic-releases playbook per ADR 0008.

Per the brief, this primitive is single-country single-event — both
``country`` (US) and ``event_type`` (nfp) are YAML-locked, NOT
LLM-facing inputs.  The only LLM-facing input is
``lookback_releases`` (display window).

Lives under ``rates_agent/sovereign_bonds/`` (not inflation_swaps)
because NFP is the macro print most directly read into the
2s/5s/10s front-end Treasury curve per PR3.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.nfp_surprise import (
        calculate_nfp_surprise,
        NfpSurpriseInput,
        NfpSurpriseOutput,
        CONFIG_PATH,
    )

Or via the sovereign-bonds schemas hub:

    from rates_agent.sovereign_bonds.tools.schemas import NfpSurpriseInput

Note for tests
--------------
``__init__.py`` re-exports only public symbols.  Test seams like
``fetch_economic_release_surprises`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...nfp_surprise.compute.fetch_economic_release_surprises`` /
``...nfp_surprise.compute.date``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.nfp_surprise.compute import (
    CONFIG_PATH,
    calculate_nfp_surprise,
)
from rates_agent.sovereign_bonds.tools.nfp_surprise.schemas import (
    NfpSurpriseCurrentMetrics,
    NfpSurpriseInput,
    NfpSurpriseOutput,
    NfpSurpriseTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_nfp_surprise",
    "NfpSurpriseCurrentMetrics",
    "NfpSurpriseInput",
    "NfpSurpriseOutput",
    "NfpSurpriseTimeSeriesRow",
]

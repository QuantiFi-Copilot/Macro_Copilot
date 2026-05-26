"""
rates_agent.sovereign_bonds.tools.otr_ofr_spread — config-driven OTR/OFR
yield-spread tool.

For one (country, tenor) sovereign cash-bond slot, computes the basis-
point yield spread between the on-the-run (OTR) bond and the bond
that was OTR immediately prior to it ("first off-the-run" / OFR),
plus a rolling 252-trading-day z-score.  Built on the cash-bond
substrate landed by ADRs 0003 + 0005 + 0007.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.otr_ofr_spread import (
        calculate_otr_ofr_spread,
        OtrOfrSpreadInput,
        OtrOfrSpreadOutput,
        CONFIG_PATH,
    )

Or via the sovereign-bonds schemas hub:

    from rates_agent.sovereign_bonds.tools.schemas import OtrOfrSpreadInput

Note for tests
--------------
``__init__.py`` re-exports only public symbols.  Test seams like
``fetch_otr_ofr_yield_pair`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...otr_ofr_spread.compute.fetch_otr_ofr_yield_pair`` /
``...otr_ofr_spread.compute.date``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.otr_ofr_spread.compute import (
    CONFIG_PATH,
    calculate_otr_ofr_spread,
)
from rates_agent.sovereign_bonds.tools.otr_ofr_spread.schemas import (
    OtrOfrSpreadCurrentMetrics,
    OtrOfrSpreadInput,
    OtrOfrSpreadOutput,
    OtrOfrSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_otr_ofr_spread",
    "OtrOfrSpreadCurrentMetrics",
    "OtrOfrSpreadInput",
    "OtrOfrSpreadOutput",
    "OtrOfrSpreadTimeSeriesRow",
]

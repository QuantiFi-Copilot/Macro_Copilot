"""
rates_agent.sovereign_bonds.tools.get_otr_history — config-driven monitor
of the macro_data.otr_history SCD2 table for one (country, tenor)
sovereign on-the-run slot.

Built on the cash-bond substrate landed by ADR 0003 and populated by
the resolver per ADR 0007.  Pure INGEST primitive (P12) — reads
identifier rows, recomputes nothing the source of record provides.

External callers reach the public API via this package's path:

    from rates_agent.sovereign_bonds.tools.get_otr_history import (
        get_otr_history,
        OtrHistoryInput,
        OtrHistoryOutput,
        CONFIG_PATH,
    )

Or via the sovereign-bonds schemas hub:

    from rates_agent.sovereign_bonds.tools.schemas import OtrHistoryInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``date`` lives inside ``compute.py``'s namespace; tests must patch it
at ``...get_otr_history.compute.date``, NOT on the package init.
"""

from rates_agent.sovereign_bonds.tools.get_otr_history.compute import (
    CONFIG_PATH,
    get_otr_history,
)
from rates_agent.sovereign_bonds.tools.get_otr_history.schemas import (
    OtrHistoryCurrentMetrics,
    OtrHistoryInput,
    OtrHistoryOutput,
    OtrHistoryTransitionRow,
)


__all__ = [
    "CONFIG_PATH",
    "get_otr_history",
    "OtrHistoryInput",
    "OtrHistoryCurrentMetrics",
    "OtrHistoryOutput",
    "OtrHistoryTransitionRow",
]

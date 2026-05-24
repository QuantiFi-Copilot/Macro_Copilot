"""
rates_agent.ois.tools.wirp_meeting_pricing — config-driven WIRP per-meeting
pricing primitive.

INGEST primitive (P12 boundary per the brief + ADR 0009 §1) — pulls
Bloomberg WIRP-screen fields verbatim from
``macro_data.market_data_daily`` and surfaces them.  NOT a
recomputation from STIR futures or OIS pricing.

Lives under ``rates_agent/ois/`` (PR3) — WIRP is computed against
OIS pricing, so the OIS desk owns the primitive.

External callers reach the public API via this package's path:

    from rates_agent.ois.tools.wirp_meeting_pricing import (
        calculate_wirp_meeting_pricing,
        WirpMeetingPricingInput,
        WirpMeetingPricingOutput,
        CONFIG_PATH,
    )

Or via the OIS schemas hub:

    from rates_agent.ois.tools.schemas import WirpMeetingPricingInput

Note for tests
--------------
``__init__.py`` re-exports only public symbols.  Test seams like
``fetch_wirp_meeting_snapshots`` and ``date`` live inside
``compute.py``'s namespace; tests must patch them at
``...wirp_meeting_pricing.compute.X``, NOT on the package init.
"""

from rates_agent.ois.tools.wirp_meeting_pricing.compute import (
    CONFIG_PATH,
    calculate_wirp_meeting_pricing,
)
from rates_agent.ois.tools.wirp_meeting_pricing.schemas import (
    WirpMeetingPricingCurrentMetrics,
    WirpMeetingPricingInput,
    WirpMeetingPricingOutput,
    WirpMeetingSnapshot,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_wirp_meeting_pricing",
    "WirpMeetingPricingCurrentMetrics",
    "WirpMeetingPricingInput",
    "WirpMeetingPricingOutput",
    "WirpMeetingSnapshot",
]

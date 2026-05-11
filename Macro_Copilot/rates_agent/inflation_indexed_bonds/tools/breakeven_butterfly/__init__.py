"""
rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly —
config-driven same-country bond-implied breakeven butterfly (3-point
curvature).

Owns the desk concept of a *same-country bond-implied breakeven
butterfly* — the 3-point curvature on a single nominal/linker pair
(e.g. UST/USD_TIPS 5s10s30s breakeven butterfly,
UK_GILT/GBP_LINKER 2s10s30s breakeven butterfly,
FR_OAT/EUR_FR_LINKER 2s5s10s breakeven butterfly,
CANADA_GOVT/CAD_RRB 5s10s30s breakeven butterfly), computed per-
trade-date via the FIXED simple-butterfly weighting:

    butterfly_bps = belly_breakeven_bps
                  - 0.5 * (short_breakeven_bps + long_breakeven_bps)

Equivalent to ``0.5 * (2 * belly - short - long)``.  Sign convention:
POSITIVE = belly is CHEAP versus the half-weighted wings (i.e. belly
breakeven is HIGH relative to the wings); NEGATIVE = belly is RICH.
Matches the sovereign_bonds/butterfly and
inflation_indexed_bonds/real_yield_butterfly sign convention.

Each endpoint breakeven (short / belly / long) is computed by
``calculate_breakeven_inflation_simple``.  This primitive composes
three spot-breakeven calls (one per endpoint) and inherits the spot
primitive's no-proxy guard (instrument_type filter on each leg) AND
same-country invariant transitively.

DISTINCT from a breakeven curve spread (2-point difference) and from
a nominal sovereign butterfly (3-point curvature of nominal yields,
in BPS).  Generic breakeven / inflation compensation, NOT a curvature
of pure expected inflation — see ``compute.py`` and the methodology
disclosure threaded onto the wire via
``current_metrics.methodology_label``.

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly import (
        calculate_breakeven_butterfly,
        BreakevenButterflyInput,
        BreakevenButterflyOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        BreakevenButterflyInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``calculate_breakeven_inflation_simple`` is a patchable seam inside
``compute.py``; tests must patch it (or the spot primitive's own
seams ``...breakeven_inflation_simple.compute.fetch_single_tenor``
and ``...breakeven_inflation_simple.compute._fetch_curve_family_country_currency``
and ``...breakeven_inflation_simple.compute.date``) at the
``...breakeven_butterfly.compute.<name>`` or
``...breakeven_inflation_simple.compute.<name>`` paths.
"""

from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.compute import (
    CONFIG_PATH,
    calculate_breakeven_butterfly,
)
from rates_agent.inflation_indexed_bonds.tools.breakeven_butterfly.schemas import (
    BreakevenButterflyCurrentMetrics,
    BreakevenButterflyInput,
    BreakevenButterflyOutput,
    BreakevenButterflyTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_breakeven_butterfly",
    "BreakevenButterflyInput",
    "BreakevenButterflyCurrentMetrics",
    "BreakevenButterflyOutput",
    "BreakevenButterflyTimeSeriesRow",
]

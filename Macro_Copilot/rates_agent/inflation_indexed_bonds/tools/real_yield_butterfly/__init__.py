"""
rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly —
config-driven same-country linker real-yield butterfly (curvature).

Eighth tool in the inflation_indexed_bonds domain.  Owns the desk
concept of a *same-country linker real-yield butterfly* — the 3-point
curvature on a single sovereign linker curve (e.g. USD_TIPS 5s10s30s
real-yield butterfly, GBP_LINKER 2s10s30s real-yield butterfly,
EUR_FR_LINKER 2s10s15s real-yield butterfly, CAD_RRB 5s10s30s
real-yield butterfly), computed per-trade-date via the FIXED simple-
butterfly weighting:

    butterfly_pct = belly_real_yield_pct
                  - 0.5 * (short_real_yield_pct + long_real_yield_pct)

Equivalent to ``0.5 * (2 * belly - short - long)``.  Sign convention:
POSITIVE = belly is CHEAP versus the half-weighted wings; NEGATIVE =
belly is RICH.  Matches the sovereign_bonds/butterfly tool's sign
convention.

Each endpoint real-yield level (short / belly / long) is computed by
``get_real_yield_level``.  This primitive composes three single-tenor
level calls (one per endpoint) and inherits the level primitive's
no-proxy guard (``instrument_type='inflation_linker'`` filter on the
DB read) transitively.  Compute additionally re-asserts on the
resolved ``macro_data.instrument_master`` rows that the curve_family
maps to a single ``(country, currency)`` tuple under that
instrument_type — the post-fetch identity guard, mirroring the
sibling real_yield_curve_spread tool.

DISTINCT from a real-yield curve spread (2-point difference) and from
a nominal sovereign butterfly (3-point curvature of nominal yields,
reported in BPS).  See ``compute.py`` and the methodology disclosure
threaded onto the wire via ``current_metrics.methodology_label``.

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly import (
        calculate_real_yield_butterfly,
        RealYieldButterflyInput,
        RealYieldButterflyOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        RealYieldButterflyInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``get_real_yield_level``, ``_enforce_same_curve_family_identity``,
and ``_fetch_curve_family_country_currency`` are patchable seams
inside ``compute.py``; tests must patch them at
``...real_yield_butterfly.compute.<name>``.  The inner level
primitive's own seams
(``rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor``,
``...compute.date``) are also valid patch points for end-to-end
synthetic-fixture testing.
"""

from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.compute import (
    CONFIG_PATH,
    calculate_real_yield_butterfly,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_butterfly.schemas import (
    RealYieldButterflyCurrentMetrics,
    RealYieldButterflyInput,
    RealYieldButterflyOutput,
    RealYieldButterflyTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_real_yield_butterfly",
    "RealYieldButterflyInput",
    "RealYieldButterflyCurrentMetrics",
    "RealYieldButterflyOutput",
    "RealYieldButterflyTimeSeriesRow",
]

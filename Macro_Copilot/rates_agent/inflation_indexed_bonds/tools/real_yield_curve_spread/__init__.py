"""
rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread —
config-driven same-country linker real-yield curve spread.

Sixth tool in the inflation_indexed_bonds domain.  Owns the desk
concept of a *same-country linker real-yield curve spread* —
the real-yield curve-shape object — between two real-yield
tenors of the same sovereign linker curve (e.g. USD_TIPS 5s10s
real-yield, GBP_LINKER 2s10s real-yield), computed per-trade-date
as the difference of the two endpoint real-yield levels:

    spread_pct = long_real_yield_pct - short_real_yield_pct

where each endpoint real-yield level (``short_real_yield_pct``,
``long_real_yield_pct``) is computed by ``get_real_yield_level``.
This primitive composes two single-tenor level calls (one per
endpoint) and inherits the level primitive's no-proxy guard
(``instrument_type='inflation_linker'`` filter on the DB read)
transitively.  Compute additionally re-asserts on the resolved
``macro_data.instrument_master`` rows that the curve_family maps
to a single ``(country, currency)`` tuple under that
instrument_type — the post-fetch identity guard.

DISTINCT from a breakeven curve spread (which differences two
nominal-minus-linker pairs) and from a nominal sovereign curve
spread (which differences two nominal-sovereign yields).  See
``compute.py`` and the methodology disclosure threaded onto the
wire via ``current_metrics.methodology_label``.

External callers reach the public API via this package's path::

    from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread import (
        calculate_real_yield_curve_spread,
        RealYieldCurveSpreadInput,
        RealYieldCurveSpreadOutput,
        CONFIG_PATH,
    )

Or via the inflation_indexed_bonds schemas hub::

    from rates_agent.inflation_indexed_bonds.tools.schemas import (
        RealYieldCurveSpreadInput,
    )

Note for tests: ``__init__.py`` re-exports only public symbols.
``get_real_yield_level``, ``_enforce_same_curve_family_identity``,
and ``_fetch_curve_family_country_currency`` are patchable seams
inside ``compute.py``; tests must patch them at
``...real_yield_curve_spread.compute.<name>``.  The inner level
primitive's own seams
(``rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute.fetch_single_tenor``,
``...compute.date``) are also valid patch points for end-to-end
synthetic-fixture testing.
"""

from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.compute import (
    CONFIG_PATH,
    calculate_real_yield_curve_spread,
)
from rates_agent.inflation_indexed_bonds.tools.real_yield_curve_spread.schemas import (
    RealYieldCurveSpreadCurrentMetrics,
    RealYieldCurveSpreadInput,
    RealYieldCurveSpreadOutput,
    RealYieldCurveSpreadTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_real_yield_curve_spread",
    "RealYieldCurveSpreadInput",
    "RealYieldCurveSpreadCurrentMetrics",
    "RealYieldCurveSpreadOutput",
    "RealYieldCurveSpreadTimeSeriesRow",
]

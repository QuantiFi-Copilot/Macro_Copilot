"""
rates_agent.ois.tools.calculate_ois_butterfly — config-driven 3-point
OIS butterfly (curvature) on a single OIS par-swap curve.

Owns the desk concept of a *same-curve OIS butterfly* — the 3-point
curvature on a SINGLE OIS curve family (e.g. USD_SOFR_OIS 2s5s10s,
EUR_ESTR_OIS 2s5s10s, GBP_SONIA_OIS 2s5s10s), computed via the FIXED
simple-butterfly weighting (a.k.a. "50-50 wings" — equal weight on
the short and long wings, NOT DV01-neutral):

    butterfly_bps = (2 * belly_rate_pct
                     - short_rate_pct
                     - long_rate_pct) * 100

Equivalent weight tuple ``(-1, +2, -1)`` applied to
``(short, belly, long)`` in raw OIS par-rate space.  Sign convention:
POSITIVE = belly is CHEAP (belly OIS rate HIGH relative to the linear
interpolation of the wings); NEGATIVE = belly is RICH.  Matches the
sovereign_bonds/butterfly,
inflation_indexed_bonds/real_yield_butterfly,
inflation_indexed_bonds/breakeven_butterfly, and
inflation_swaps/inflation_swap_butterfly sign convention.

Same-curve only — cross-curve butterflies (e.g. mixing USD_SOFR_OIS
and EUR_ESTR_OIS pillars) are forbidden by this primitive's input
shape (single ``curve_family`` field on the closed OIS playbook
enum).

Raw OIS par-swap-rate space — the butterfly arithmetic is performed
on raw OIS par rates only.  NO conversion to a bond-equivalent yield,
NO subtraction of a model-derived basis, NO routing through a fitted
curve object.  See config.yaml's methodology disclosure for the
wire-locked promise.

DISTINCT from:
  - an OIS curve spread (2-point difference) — see
    ``calculate_ois_curve_spread``.
  - an OIS forward rate (year-weighted forward of two endpoints) —
    see ``calculate_ois_forward_rate``.
  - a cross-currency OIS spread (e.g. SOFR-ESTR same-tenor
    differential) — see ``calculate_ois_cross_market_spread``.
  - a sovereign butterfly (3-point curvature of sovereign yields; in
    BPS but with different sign-on-the-wire semantics for the
    OIS-vs-sovereign asset-swap relationship) — see
    ``rates_agent.sovereign_bonds.tools.butterfly``.
  - a swap spread (sovereign-vs-OIS differential at one tenor) — see
    ``rates_agent.ois.tools.swap_spread``.

External callers reach the public API via this package's path::

    from rates_agent.ois.tools.calculate_ois_butterfly import (
        calculate_ois_butterfly,
        OISButterflyInput,
        OISButterflyOutput,
        CONFIG_PATH,
    )

Or via the OIS schemas hub::

    from rates_agent.ois.tools.schemas import OISButterflyInput

Note for tests: ``__init__.py`` re-exports only public symbols.
``fetch_tenor_group`` and ``date`` live inside ``compute.py``'s
namespace; tests must patch them at
``...calculate_ois_butterfly.compute.<name>``, NOT on the package
init.
"""

from rates_agent.ois.tools.calculate_ois_butterfly.compute import (
    CONFIG_PATH,
    calculate_ois_butterfly,
)
from rates_agent.ois.tools.calculate_ois_butterfly.schemas import (
    OIS_CURVE_FAMILY,
    OISButterflyCurrentMetrics,
    OISButterflyInput,
    OISButterflyOutput,
    OISButterflyTimeSeriesRow,
)


__all__ = [
    "CONFIG_PATH",
    "calculate_ois_butterfly",
    "OIS_CURVE_FAMILY",
    "OISButterflyInput",
    "OISButterflyCurrentMetrics",
    "OISButterflyOutput",
    "OISButterflyTimeSeriesRow",
]

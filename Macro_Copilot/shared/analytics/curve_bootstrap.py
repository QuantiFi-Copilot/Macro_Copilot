"""
curve_bootstrap.py — Shared rates-curve primitives for forward-rate analytics
==============================================================================

Pure-math helpers for converting par OIS quotes into discount factors and
forward rates.  Used by ois_forward_rate today; reusable by any future
rates tool that needs forward-curve math.

Compounding convention (dual — matches market practice)
-------------------------------------------------------
OIS par swap rates approximate zero rates because the float leg is a
daily-compounded overnight rate with no funding spread.  We therefore
use the par rate directly as a zero rate rather than running a full
iterative bootstrap.  The discount factor formula depends on the tenor:

    DF(T) = 1 / (1 + R·T)            for T ≤ 1 year   (money-market / simple)
    DF(T) = 1 / (1 + R)**T           for T > 1 year   (annual compounding)

This dual convention matches how desks quote OIS curves: short-end
tenors are money-market instruments (simple compounding), multi-year
tenors are annually-compounded par swaps.  Using simple compounding
everywhere — as an earlier version did — produces discount factors
that drift tens of bps wrong on the long end.  At 4% for 10Y,
simple compounding gives DF=0.714 vs annual 0.676, which translates
to ~20bps of error on a 5Y5Y forward.

Year fractions
--------------
Tenor strings ("1W", "3M", "1Y", "10Y") are converted to calendar year
fractions via a fixed mapping (7/365 for weeks, n/12 for months, n for
years).  This ignores calendar-aware day counting but matches the
macro-desk convention for "quick" forward quotes.  Per-curve day-count
conventions are surfaced via ``day_count_basis_for_curve`` for callers
that need precise day-fraction normalisation in date-window forwards.

All helpers are pure Python (no pandas, no DB) so they're trivially
testable in isolation.
"""

from __future__ import annotations

from bisect import bisect_left
from typing import Sequence


# ============================================================================
# TENOR PARSING
# ============================================================================

def tenor_to_years(tenor: str) -> float:
    """Convert a tenor label to a calendar year fraction.

    Accepted forms: ``'1W'``, ``'1M'`` / ``'2M'`` / ``'9M'``, ``'1Y'``
    / ``'10Y'`` / ``'30Y'``.  Weeks use ``n * 7 / 365``; months use
    ``n / 12``; years pass through directly.

    Raises ``ValueError`` on unrecognised input so the caller can
    surface a clean domain error to the user.
    """
    t = tenor.strip().upper()
    if not t:
        raise ValueError("Tenor string is empty.")

    try:
        suffix = t[-1]
        n = int(t[:-1])
    except (ValueError, IndexError):
        raise ValueError(f"Unrecognised tenor format: {tenor!r}")

    if suffix == "W":
        return n * 7.0 / 365.0
    if suffix == "M":
        return n / 12.0
    if suffix == "Y":
        return float(n)
    raise ValueError(
        f"Unrecognised tenor suffix {suffix!r} in {tenor!r}. "
        "Expected W, M, or Y."
    )


# ============================================================================
# DAY-COUNT CONVENTIONS
# ============================================================================

# OIS day-count by curve_family.  USD SOFR and EUR ESTR use ACT/360;
# GBP SONIA, JPY OIS (TONA), AUD OIS (AONIA), CAD OIS (CORRA) use
# ACT/365.FIXED.  This is a static lookup — for production pricing you
# would pull the convention from instrument_master.attributes per-row.
_DAY_COUNT_360 = frozenset({"USD_SOFR_OIS", "EUR_ESTR_OIS"})


def day_count_basis_for_curve(curve_family: str) -> int:
    """Return the day-count divisor (360 or 365) for an OIS curve.

    Not strictly required by the default forward-rate math (which uses
    calendar year fractions), but exposed for callers that need to
    compute precise day-weighted accruals or convert days to year
    fractions.
    """
    return 360 if curve_family in _DAY_COUNT_360 else 365


# ============================================================================
# DISCOUNT FACTOR
# ============================================================================

def discount_factor_from_par(par_rate: float, years: float) -> float:
    """Approximate discount factor from a par OIS rate.

    Uses the standard dual convention:

        DF(T) = 1 / (1 + R·T)      for T ≤ 1 year   (money-market)
        DF(T) = 1 / (1 + R)**T     for T > 1 year   (annual compounding)

    The two branches agree at T = 1 (both give 1/(1+R)), so the function
    is continuous at the boundary.

    Inputs:
        par_rate : rate in DECIMAL (e.g. 0.045 for 4.5%), not percent.
        years    : calendar year fraction (≥ 0).

    The caller is responsible for converting pct → decimal before
    passing.  Keeping decimal here avoids a ×100 factor quietly hiding
    inside this primitive.
    """
    if years < 0:
        raise ValueError(f"years must be non-negative, got {years}")
    if years <= 1.0:
        # Money-market / simple compounding for short end.
        return 1.0 / (1.0 + par_rate * years)
    # Annual compounding for multi-year tenors — the market standard for
    # the par-swap leg of an OIS.
    return 1.0 / ((1.0 + par_rate) ** years)


# ============================================================================
# ZERO-RATE INTERPOLATION
# ============================================================================

def interpolate_rate(
    curve_years: Sequence[float],
    curve_rates: Sequence[float],
    target_years: float,
) -> float:
    """Linear interpolation of the rate at ``target_years`` from a
    discretely-observed curve.

    Extrapolates flat past the ends of the curve (uses the nearest
    endpoint's rate rather than extrapolating slope) — this is the
    macro-desk convention for handling queries outside the quoted grid
    and avoids blowing up on very short or very long target tenors.

    ``curve_years`` must be sorted ascending.  Units are caller-defined
    (caller can pass rates in percent or decimal, just be consistent);
    this helper is unit-blind.
    """
    if not curve_years:
        raise ValueError("Curve is empty — cannot interpolate.")
    if len(curve_years) != len(curve_rates):
        raise ValueError(
            f"Curve length mismatch: {len(curve_years)} tenors vs "
            f"{len(curve_rates)} rates."
        )

    # Flat extrapolation beyond the grid endpoints.
    if target_years <= curve_years[0]:
        return curve_rates[0]
    if target_years >= curve_years[-1]:
        return curve_rates[-1]

    # Find the bracketing tenor pair and linearly interpolate.
    idx = bisect_left(list(curve_years), target_years)
    t_low, t_high = curve_years[idx - 1], curve_years[idx]
    r_low, r_high = curve_rates[idx - 1], curve_rates[idx]
    if t_high == t_low:
        return r_low
    frac = (target_years - t_low) / (t_high - t_low)
    return r_low + frac * (r_high - r_low)


# ============================================================================
# FORWARD RATE
# ============================================================================

def forward_rate_between(
    curve_years: Sequence[float],
    curve_rates_decimal: Sequence[float],
    start_years: float,
    end_years: float,
) -> float:
    """Implied forward rate covering the period ``(start_years, end_years)``
    given a set of observed par OIS rates.

    Formula (simple compounding):

        DF(t) = 1 / (1 + r(t) · t)             [zero-rate approximation]
        f(t1, t2) = (DF(t1) / DF(t2) - 1) / (t2 - t1)

    Returns the forward in DECIMAL (e.g. 0.04 for 4.00%); callers
    typically multiply by 100 for display in percent.

    Raises ``ValueError`` on invalid windows so the tool layer can
    surface a clean error rather than silently producing garbage.
    """
    if end_years <= start_years:
        raise ValueError(
            f"end_years ({end_years}) must be strictly greater than "
            f"start_years ({start_years})."
        )
    if start_years < 0 or end_years < 0:
        raise ValueError(
            f"Tenors must be non-negative (start={start_years}, end={end_years})."
        )

    r_start = interpolate_rate(curve_years, curve_rates_decimal, start_years)
    r_end = interpolate_rate(curve_years, curve_rates_decimal, end_years)

    df_start = discount_factor_from_par(r_start, start_years) if start_years > 0 else 1.0
    df_end = discount_factor_from_par(r_end, end_years)

    dt = end_years - start_years
    return (df_start / df_end - 1.0) / dt


# ============================================================================
# CANONICAL TENOR ORDERING
# ============================================================================

def sort_tenors_by_years(tenors: Sequence[str]) -> list[str]:
    """Return tenors sorted ascending by their year fraction.

    Convenience for callers that receive tenors in database-order
    (lexicographic) and need them in chronological order for
    interpolation.  Skips unparseable tenors silently — the tool layer
    is expected to validate input before calling this.
    """
    parsed = []
    for t in tenors:
        try:
            parsed.append((tenor_to_years(t), t))
        except ValueError:
            continue
    parsed.sort(key=lambda x: x[0])
    return [t for _, t in parsed]

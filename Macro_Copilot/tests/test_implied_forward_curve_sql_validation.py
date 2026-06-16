#!/usr/bin/env python3
"""test_implied_forward_curve_sql_validation.py — live-DB validator.

The forward strip is a deterministic curve analytic, so this does a REAL
numeric parity: independently fetch the live OIS curve (anchored on the
data's own MAX(trade_date), NOT today−60d) and recompute each anchor's
forward FROM FIRST PRINCIPLES — linear interpolation of the par grid +
the dual-convention discount factor, forward = (DF_a/DF_{a+h} − 1)/h —
using ONLY the shared ``discount_factor_from_par`` primitive.  It does
NOT re-call ``forward_rate_between`` (the production engine the tool
itself uses), so the baseline is genuinely independent: an engine bug in
``forward_rate_between`` would be CAUGHT here rather than funnelling
through the same function on both sides.

Read-only.  Standalone, NOT pytest-collected:

    python3 -m tests.test_implied_forward_curve_sql_validation
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import List, Sequence

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from rates_agent.ois.tools.implied_forward_curve import (
    ImpliedForwardCurveInput,
    ImpliedForwardCurveOutput,
    calculate_implied_forward_curve,
)
from shared.analytics.curve_bootstrap import (
    discount_factor_from_par,
    sort_tenors_by_years,
    tenor_to_years,
)

_TOL_PCT = 0.0002  # primitive rounds to 4dp percent
_ANCHORS = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]
_HORIZON = "1Y"
_DEFAULT_CASES = ("USD_SOFR_OIS", "EUR_ESTR_OIS")


def _latest_curve(engine, cf) -> pd.DataFrame:
    """Fetch the latest-available OIS curve cross-section, anchored on the
    data's own MAX(trade_date) (NOT today−60d).

    Mirrors the forward_rate reference validator's ``WITH latest_date``
    CTE: the OIS vintage can lag wall-clock by months, so a today−N
    window goes dormant when the data is older than N days.  Anchoring on
    MAX(trade_date) keeps the parity gate live on whatever the most
    recent stored curve is.
    """
    sql = text("""
        WITH latest_date AS (
            SELECT MAX(trade_date) AS as_of
            FROM macro_data.v_market_data_daily_enriched
            WHERE curve_family = :cf AND field_name = 'PX_LAST'
              AND instrument_type = 'ois_swap' AND tenor IS NOT NULL
        )
        SELECT t.trade_date, t.tenor,
               t.field_value::double precision AS v
        FROM macro_data.v_market_data_daily_enriched t
        CROSS JOIN latest_date l
        WHERE t.curve_family = :cf AND t.field_name = 'PX_LAST'
          AND t.instrument_type = 'ois_swap' AND t.tenor IS NOT NULL
          AND t.trade_date = l.as_of
        ORDER BY t.tenor
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"cf": cf}).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def _interp_independent(
    grid_years: Sequence[float],
    grid_rates: Sequence[float],
    target: float,
) -> float:
    """Linear interpolation reimplemented from first principles (flat
    extrapolation past the grid ends) — independent of the production
    ``interpolate_rate`` so the baseline does not funnel through any tool
    code path.  Units caller-defined (decimal here)."""
    if target <= grid_years[0]:
        return grid_rates[0]
    if target >= grid_years[-1]:
        return grid_rates[-1]
    for i in range(1, len(grid_years)):
        if grid_years[i] >= target:
            x0, x1 = grid_years[i - 1], grid_years[i]
            y0, y1 = grid_rates[i - 1], grid_rates[i]
            return y0 + (y1 - y0) * (target - x0) / (x1 - x0)
    return grid_rates[-1]


def _forward_from_first_principles(
    grid_years: Sequence[float],
    grid_rates_dec: Sequence[float],
    a: float,
    h: float,
) -> float:
    """Independent forward over (a, a+h) in DECIMAL.

    Built ONLY from the shared ``discount_factor_from_par`` primitive plus
    a from-scratch linear interpolation — it does NOT call the production
    ``forward_rate_between``.  The dual-DF forward is the textbook
    no-arbitrage relation:

        DF(t) = par-as-zero discount factor (dual convention)
        f(a, a+h) = (DF(a) / DF(a+h) − 1) / h

    Because this path shares NO arithmetic with the tool's engine, a bug
    in ``forward_rate_between`` would produce a divergence the parity
    check below would flag.
    """
    r_a = _interp_independent(grid_years, grid_rates_dec, a)
    r_end = _interp_independent(grid_years, grid_rates_dec, a + h)
    df_a = discount_factor_from_par(r_a, a) if a > 0 else 1.0
    df_end = discount_factor_from_par(r_end, a + h)
    return (df_a / df_end - 1.0) / h


def validate_case(engine, cf) -> List[str]:
    failures: List[str] = []
    day = _latest_curve(engine, cf)
    if day.empty:
        return [f"{cf}: no curve data"]
    as_of = day["trade_date"].max().date()
    stale_days = (date.today() - as_of).days
    note = f" (data stale by {stale_days}d)" if stale_days > 7 else ""
    print(f"    · {cf}: latest curve as-of {as_of.isoformat()}{note}")
    ordered = sort_tenors_by_years(day["tenor"].tolist())
    rate_by_tenor = dict(zip(day["tenor"], day["v"]))
    years = [tenor_to_years(t) for t in ordered]
    dec = [float(rate_by_tenor[t]) / 100.0 for t in ordered]
    h = tenor_to_years(_HORIZON)

    r = calculate_implied_forward_curve(
        engine=engine,
        params=ImpliedForwardCurveInput(
            curve_family=cf, forward_horizon=_HORIZON, anchor_tenors=_ANCHORS),
    )
    if "error" in r:
        return [f"{cf}: primitive error — {r['error']}"]
    by_anchor = {p["anchor_tenor"]: p["forward_rate_pct"]
                 for p in r["current_metrics"]["forward_curve"]}
    for anchor in _ANCHORS:
        a = tenor_to_years(anchor)
        if a > years[-1] or a + h < years[0]:
            continue
        exp = _forward_from_first_principles(years, dec, a, h) * 100
        got = by_anchor.get(anchor)
        if got is None or abs(got - exp) > _TOL_PCT:
            failures.append(f"{cf} {anchor}1Y: {got} vs {exp:.4f}")

    # Panel round-trips + determinism.
    try:
        ImpliedForwardCurveOutput.model_validate(r)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{cf}: Panel round-trip failed — {exc}")
    r2 = calculate_implied_forward_curve(
        engine=engine,
        params=ImpliedForwardCurveInput(
            curve_family=cf, forward_horizon=_HORIZON, anchor_tenors=_ANCHORS),
    )
    if r["current_metrics"] != r2["current_metrics"]:
        failures.append(f"{cf}: not reproducible")
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curve", default=None)
    args = parser.parse_args(argv)
    engine = get_db_engine()
    cases = (args.curve,) if args.curve else _DEFAULT_CASES

    all_failures: List[str] = []
    for cf in cases:
        fs = validate_case(engine, cf)
        print(f"[{'PASS' if not fs else 'FAIL'}] {cf}")
        for f in fs:
            print(f"    - {f}")
        all_failures += fs

    if all_failures:
        print(f"\n{len(all_failures)} failure(s).")
        return 1
    print("\nAll cases passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

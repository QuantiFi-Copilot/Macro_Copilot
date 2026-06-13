#!/usr/bin/env python3
"""test_implied_forward_curve_sql_validation.py — live-DB validator.

The forward strip is a deterministic curve analytic, so this does a REAL
numeric parity: independently fetch the live OIS curve and recompute each
anchor's forward via the same forward_rate_between engine, asserting the
primitive's latest forward-curve cross-section matches within tolerance.

Read-only.  Standalone, NOT pytest-collected:

    python3 -m tests.test_implied_forward_curve_sql_validation
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import List

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from rates_agent.ois.tools.implied_forward_curve import (
    ImpliedForwardCurveInput,
    ImpliedForwardCurveOutput,
    calculate_implied_forward_curve,
)
from shared.analytics.curve_bootstrap import (
    forward_rate_between,
    sort_tenors_by_years,
    tenor_to_years,
)

_TOL_PCT = 0.0002  # primitive rounds to 4dp percent
_ANCHORS = ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]
_HORIZON = "1Y"
_DEFAULT_CASES = ("USD_SOFR_OIS", "EUR_ESTR_OIS")


def _latest_curve(engine, cf) -> pd.DataFrame:
    sql = text("""
        SELECT trade_date, tenor, field_value::double precision AS v
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family = :cf AND field_name = 'PX_LAST'
          AND instrument_type = 'ois_swap' AND tenor IS NOT NULL
          AND trade_date >= :start
        ORDER BY trade_date, tenor
    """)
    start = (date.today() - timedelta(days=60)).isoformat()
    with engine.connect() as conn:
        rows = conn.execute(sql, {"cf": cf, "start": start}).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df[df["trade_date"] == df["trade_date"].max()]


def validate_case(engine, cf) -> List[str]:
    failures: List[str] = []
    day = _latest_curve(engine, cf)
    if day.empty:
        return [f"{cf}: no curve data"]
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
        exp = forward_rate_between(years, dec, a, a + h) * 100
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

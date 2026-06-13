#!/usr/bin/env python3
"""test_swap_carry_and_roll_sql_validation.py — live-DB validator.

swap_carry_and_roll is a fully deterministic curve analytic, so this does a
REAL numeric parity: independently fetch the live OIS curve, recompute the
carry/roll decomposition (roll = s(T)−s(T−h), carry = s(T)−s(h)) via the
same interpolation, and assert the primitive's current_metrics match within
tolerance.

Read-only.  Standalone, NOT pytest-collected:

    python3 -m tests.test_swap_carry_and_roll_sql_validation
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import List

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from rates_agent.ois.tools.swap_carry_and_roll import (
    SwapCarryAndRollInput,
    calculate_swap_carry_and_roll,
)
from shared.analytics.curve_bootstrap import (
    interpolate_rate,
    sort_tenors_by_years,
    tenor_to_years,
)

_TOL_BPS = 0.02  # primitive rounds to 2dp
_DEFAULT_CASES = (("USD_SOFR_OIS", "10Y", "3M"), ("EUR_ESTR_OIS", "10Y", "3M"))


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
    last = df["trade_date"].max()
    return df[df["trade_date"] == last]


def validate_case(engine, cf, tenor, horizon) -> List[str]:
    failures: List[str] = []
    day = _latest_curve(engine, cf)
    if day.empty:
        return [f"{cf}: no curve data"]
    ordered = sort_tenors_by_years(day["tenor"].tolist())
    rate_by_tenor = dict(zip(day["tenor"], day["v"]))
    years = [tenor_to_years(t) for t in ordered]
    dec = [float(rate_by_tenor[t]) / 100.0 for t in ordered]
    T, h = tenor_to_years(tenor), tenor_to_years(horizon)
    if T > years[-1]:
        return [f"{cf}: tenor {tenor} past grid"]
    s_t = interpolate_rate(years, dec, T)
    s_tmh = interpolate_rate(years, dec, T - h)
    s_h = interpolate_rate(years, dec, h)
    exp_roll = (s_t - s_tmh) * 10000
    exp_carry = (s_t - s_h) * 10000

    r = calculate_swap_carry_and_roll(
        engine=engine,
        params=SwapCarryAndRollInput(curve_family=cf, tenor=tenor, horizon=horizon),
    )
    if "error" in r:
        return [f"{cf}: primitive error — {r['error']}"]
    cm = r["current_metrics"]
    if abs(cm["roll_bps"] - exp_roll) > _TOL_BPS:
        failures.append(f"{cf} roll: {cm['roll_bps']} vs {exp_roll:.4f}")
    if abs(cm["carry_bps"] - exp_carry) > _TOL_BPS:
        failures.append(f"{cf} carry: {cm['carry_bps']} vs {exp_carry:.4f}")
    if abs(cm["total_carry_roll_bps"] - (cm["roll_bps"] + cm["carry_bps"])) > _TOL_BPS:
        failures.append(f"{cf}: total != roll + carry")
    # Determinism.
    r2 = calculate_swap_carry_and_roll(
        engine=engine,
        params=SwapCarryAndRollInput(curve_family=cf, tenor=tenor, horizon=horizon),
    )
    if cm != r2["current_metrics"]:
        failures.append(f"{cf}: not reproducible")
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curve", default=None)
    args = parser.parse_args(argv)
    engine = get_db_engine()
    cases = (
        tuple(c for c in _DEFAULT_CASES if c[0] == args.curve)
        or ((args.curve, "10Y", "3M"),)
    ) if args.curve else _DEFAULT_CASES

    all_failures: List[str] = []
    for cf, tenor, horizon in cases:
        fs = validate_case(engine, cf, tenor, horizon)
        print(f"[{'PASS' if not fs else 'FAIL'}] {cf} {tenor} {horizon}")
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

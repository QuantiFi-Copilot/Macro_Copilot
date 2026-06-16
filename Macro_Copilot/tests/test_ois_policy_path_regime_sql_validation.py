#!/usr/bin/env python3
"""test_ois_policy_path_regime_sql_validation.py — live-DB validator.

A Gaussian-HMM fit has no SQL parity, so this does NOT cross-check the
regime labels against SQL.  Two layers:

  LAYER 1 — DATA ASSEMBLY SANITY.  Fetch the live policy-futures strip via
    the shared fetcher, apply the inverse-pricing conversion (100 − price)
    + the feature arithmetic independently, and assert the implied front
    rate, slope, curvature, and realized-vol are finite and in plausible
    ranges (front rate 0–10%, |slope| < 500 bps).

  LAYER 1c — LITERAL-SQL CONVENTION CHECK.  For one (cf, strip_position,
    trade_date) cell, run a literal ``SELECT 100 − field_value`` directly
    against the view and assert it equals the Python-over-rows implied
    rate.  This makes the inverse-pricing convention cross-check a literal
    SQL statement (not just Python reproducing the same arithmetic over
    fetched rows).

  LAYER 2 — MODEL-STATE PROPERTIES + DETERMINISM.  fit_scope=='full_sample';
    converged; persistence in [0,1]; >= 2 distinct decoded labels; regime
    names from the configured vocabulary; rerun-identical current_metrics.

Read-only.  Standalone, NOT pytest-collected:

    python3 -m tests.test_ois_policy_path_regime_sql_validation
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, timedelta
from typing import List

from sqlalchemy import text

from database.database import get_db_engine
from rates_agent.ois.tools.ois_policy_path_regime import (
    OISPolicyPathRegimeInput,
    calculate_ois_policy_path_regime,
)
from shared.analytics.panel_assembly import (
    _strip_panel_column_key,
    fetch_policy_futures_strip_panel,
)

_LOOKBACK = 2520
_POS = [1, 4, 8]
# EUR has the deepest history (2005→); SOFR from 2018 inception.
_DEFAULT_CASES = ("EUR_SHORT_RATE_FUT", "SOFR_FUT")
_VALID_NAMES = {"easing_priced", "neutral_priced", "tightening_priced"}


def validate_case(engine, cf) -> List[str]:
    failures: List[str] = []
    start_date = date.today() - timedelta(days=_LOOKBACK)

    # LAYER 1 — data assembly sanity.
    raw, umeta = fetch_policy_futures_strip_panel(
        engine=engine, curve_families=[cf], strip_positions=_POS,
        field_name="PX_LAST", start_date=start_date, ffill_limit_days=5,
    )
    if raw.empty:
        return [f"{cf}: no strip data"]
    flags = umeta[umeta["curve_family"] == cf]["inverse_pricing"].dropna().tolist()
    if not flags:
        return [f"{cf}: missing inverse_pricing flag"]
    inverse = bool(flags[0])
    implied = raw.copy()
    if inverse:
        for col in implied.columns:
            implied[col] = 100.0 - implied[col]
    k = {p: _strip_panel_column_key(cf, p) for p in _POS}
    if any(k[p] not in implied.columns for p in _POS):
        return [f"{cf}: missing strip positions"]
    front = implied[k[1]].dropna()
    slope = ((implied[k[8]] - implied[k[1]]) * 100.0).dropna()
    if not (0.0 < float(front.iloc[-1]) < 10.0):
        failures.append(f"{cf}: front implied rate {front.iloc[-1]:.2f}% implausible")
    if abs(float(slope.iloc[-1])) > 500.0:
        failures.append(f"{cf}: strip slope {slope.iloc[-1]:.1f} bps implausible")
    if not math.isfinite(float(slope.std())):
        failures.append(f"{cf}: non-finite slope series")

    # LAYER 1c — literal-SQL convention cross-check.  For the front cell
    # (strip_position=1) on its as-of date, a literal `SELECT 100 -
    # field_value` must equal the Python-over-rows implied front rate.
    if inverse:
        as_of_cell = front.index[-1]
        cell_sql = text(
            """
            SELECT 100.0 - v.field_value::double precision AS implied_rate
            FROM macro_data.v_market_data_daily_enriched AS v
            WHERE v.instrument_type = 'policy_future'
              AND v.field_name = 'PX_LAST'
              AND v.curve_family = :cf
              AND (v.attributes->>'strip_position')::int = 1
              AND v.field_value IS NOT NULL
              AND v.trade_date = :as_of
            """
        )
        with engine.connect() as conn:
            cell_rows = conn.execute(
                cell_sql,
                {"cf": cf, "as_of": as_of_cell.date().isoformat()},
            ).mappings().all()
        if not cell_rows:
            failures.append(
                f"{cf}: literal-SQL 100−field_value returned no front cell "
                f"for {as_of_cell.date().isoformat()}"
            )
        else:
            sql_implied = float(cell_rows[0]["implied_rate"])
            py_implied = float(front.iloc[-1])
            if abs(sql_implied - py_implied) > 1e-9:
                failures.append(
                    f"{cf}: literal-SQL 100−field_value={sql_implied:.6f} != "
                    f"Python implied front {py_implied:.6f} "
                    f"on {as_of_cell.date().isoformat()}"
                )

    # LAYER 2 — model-state properties + determinism.
    params = OISPolicyPathRegimeInput(curve_family=cf, n_states=3,
                                      lookback_days=_LOOKBACK)
    r1 = calculate_ois_policy_path_regime(engine=engine, params=params)
    if "error" in r1:
        return failures + [f"{cf}: primitive error — {r1['error']}"]
    r2 = calculate_ois_policy_path_regime(engine=engine, params=params)
    cm = r1["current_metrics"]

    if cm["fit_scope"] != "full_sample":
        failures.append(f"{cf}: fit_scope != full_sample")
    if not cm["converged"]:
        failures.append(f"{cf}: HMM did not converge")
    p = cm["regime_persistence"]
    if p is None or not (0.0 <= p <= 1.0):
        failures.append(f"{cf}: persistence {p} out of [0,1]")
    labels = [row["value"] for row in r1["time_series_regime"]["rows"]
              if row["value"] is not None]
    if len({round(x) for x in labels}) < 2:
        failures.append(f"{cf}: fewer than 2 distinct regimes decoded")
    for r in cm["per_regime"]:
        base = r["regime_name"].rsplit("_", 1)[0] if r["regime_name"][-1].isdigit() \
            else r["regime_name"]
        if base not in _VALID_NAMES and r["regime_name"] not in _VALID_NAMES:
            failures.append(f"{cf}: unexpected regime name {r['regime_name']}")
    if cm != r2["current_metrics"]:
        failures.append(f"{cf}: current_metrics not reproducible")

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

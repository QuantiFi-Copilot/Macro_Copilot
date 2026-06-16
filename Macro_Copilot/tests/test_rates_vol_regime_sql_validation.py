#!/usr/bin/env python3
"""test_rates_vol_regime_sql_validation.py — live-DB validator.

A GARCH(1,1) fit (constrained MLE) has NO pure-SQL parity, so — like the
HMM / PCA-fit validators — this does NOT cross-check the conditional-vol
path against SQL.  Two layers:

  LAYER 1 — INPUT SANITY (SQL).  Independently fetch the tenor yields,
    first-difference them, and confirm the finite-return count and the
    realized std (a coarse anchor for the GARCH conditional vol) are in
    plausible ranges — a structural sanity check on the input the fit
    consumes.

  LAYER 1b — MAGNITUDE TIE (SQL ↔ model).  The realized daily std from
    SQL is a model-free anchor for the GARCH conditional vol.  Assert the
    median of the fitted conditional-vol series sits within a coarse
    factor band of that realized std:
        0.3·realized_std < median(conditional_vol_bps) < 3·realized_std
    A wildly-wrong-but-plausible σ_t (passing the bare range check) now
    fails this numeric proximity tie — the LAYER-1 anchor is no longer a
    pure range check disconnected from the fit.

  LAYER 2 — MODEL-STATE PROPERTIES + DETERMINISM.  persistence = α+β in
    (0,1) and == α+β; ω>0, α>=0, β>=0; fit_scope=='full_sample';
    regime_label in {calm,normal,elevated}; vol_percentile in [0,100];
    current daily vol > 0; rerun-identical current_metrics.

Read-only.  Standalone, NOT pytest-collected:

    python3 -m tests.test_rates_vol_regime_sql_validation
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import List

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from rates_agent.sovereign_bonds.tools.rates_vol_regime import (
    RatesVolRegimeInput,
    calculate_rates_vol_regime,
)

_LOOKBACK = 2520
_DEFAULT_CASES = (("UST", "10Y"), ("DE_BUND", "10Y"))


def _sql_returns(engine, curve_family, tenor, start_date) -> pd.Series:
    sql = text(
        """
        SELECT trade_date, field_value::double precision AS v
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'sovereign_benchmark'
          AND field_name = 'YLD_YTM_MID'
          AND curve_family = :cf AND tenor = :tn
          AND trade_date >= :start
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql, {"cf": curve_family, "tn": tenor,
                  "start": start_date.isoformat()},
        ).mappings().all()
    if not rows:
        return pd.Series(dtype=float)
    s = pd.Series(
        [r["v"] for r in rows],
        index=pd.DatetimeIndex([r["trade_date"] for r in rows]),
    ).astype(float).ffill(limit=5)
    return s.diff()


def validate_case(engine, curve_family, tenor) -> List[str]:
    failures: List[str] = []
    start_date = date.today() - timedelta(days=_LOOKBACK)

    # LAYER 1 — input sanity.
    rets = _sql_returns(engine, curve_family, tenor, start_date)
    finite = rets.dropna()
    if len(finite) < 100:
        return [f"{curve_family} {tenor}: only {len(finite)} return rows"]
    realized_std_bps = float(finite.std(ddof=1)) * 100.0
    if not (0.5 < realized_std_bps < 30.0):
        failures.append(
            f"{curve_family} {tenor}: realized daily std {realized_std_bps:.2f} "
            "bps outside the plausible 0.5–30 range"
        )

    # LAYER 2 — model-state properties + determinism.
    params = RatesVolRegimeInput(
        curve_family=curve_family, tenor=tenor, lookback_days=_LOOKBACK,
    )
    r1 = calculate_rates_vol_regime(engine=engine, params=params)
    if "error" in r1:
        return failures + [f"{curve_family} {tenor}: primitive error — {r1['error']}"]
    r2 = calculate_rates_vol_regime(engine=engine, params=params)
    cm = r1["current_metrics"]

    # LAYER 1b — coarse magnitude tie between the SQL realized std and the
    # fitted conditional-vol series.  Only meaningful when the realized
    # std itself was plausible (else the anchor is unreliable).
    if 0.5 < realized_std_bps < 30.0:
        cv = [row["conditional_vol_bps"] for row in r1["time_series"]
              if row.get("conditional_vol_bps") is not None]
        if cv:
            median_cv = float(pd.Series(cv).median())
            lo, hi = 0.3 * realized_std_bps, 3.0 * realized_std_bps
            if not (lo < median_cv < hi):
                failures.append(
                    f"{curve_family} {tenor}: median conditional vol "
                    f"{median_cv:.2f} bps outside the [{lo:.2f}, {hi:.2f}] "
                    f"band tied to realized std {realized_std_bps:.2f} bps"
                )
        else:
            failures.append(
                f"{curve_family} {tenor}: no conditional-vol series to "
                "magnitude-tie against the realized std"
            )

    if not (0.0 < cm["persistence"] < 1.0):
        failures.append(f"{curve_family} {tenor}: persistence {cm['persistence']} not in (0,1)")
    if abs(cm["persistence"] - (cm["alpha"] + cm["beta"])) > 1e-3:
        failures.append(f"{curve_family} {tenor}: persistence != alpha+beta")
    if cm["omega"] <= 0.0 or cm["alpha"] < 0.0 or cm["beta"] < 0.0:
        failures.append(f"{curve_family} {tenor}: invalid GARCH params (ω/α/β)")
    if cm["fit_scope"] != "full_sample":
        failures.append(f"{curve_family} {tenor}: fit_scope != full_sample")
    if cm["regime_label"] not in {"calm", "normal", "elevated"}:
        failures.append(f"{curve_family} {tenor}: bad regime_label {cm['regime_label']}")
    if not (0.0 <= cm["vol_percentile"] <= 100.0):
        failures.append(f"{curve_family} {tenor}: vol_percentile out of [0,100]")
    if cm["current_conditional_vol_daily_bps"] <= 0.0:
        failures.append(f"{curve_family} {tenor}: non-positive conditional vol")
    if cm != r2["current_metrics"]:
        failures.append(f"{curve_family} {tenor}: current_metrics not reproducible")

    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curve", default=None)
    args = parser.parse_args(argv)
    engine = get_db_engine()
    cases = (
        tuple(c for c in _DEFAULT_CASES if c[0] == args.curve)
        or ((args.curve, "10Y"),)
    ) if args.curve else _DEFAULT_CASES

    all_failures: List[str] = []
    for curve_family, tenor in cases:
        fs = validate_case(engine, curve_family, tenor)
        print(f"[{'PASS' if not fs else 'FAIL'}] {curve_family} {tenor}")
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

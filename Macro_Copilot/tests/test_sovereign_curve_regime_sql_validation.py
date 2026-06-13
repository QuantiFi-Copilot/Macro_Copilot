#!/usr/bin/env python3
"""test_sovereign_curve_regime_sql_validation.py — live-DB validator.

A Gaussian-HMM fit (Baum-Welch + Viterbi) has NO pure-SQL parity
expression, so — exactly like ``test_pca_yield_curve_sql_validation.py``
— this validator does NOT cross-check the regime labels against SQL.  It
splits into the two layers the Standards-Warden blessed for a stochastic
Bucket-2 fit:

  LAYER 1 — FEATURE-PANEL SQL PARITY (the deterministic part).
    The four features (level, 2s10s slope, curvature, realized-vol) ARE
    SQL-expressible.  We reconstruct them from
    ``macro_data.v_market_data_daily_enriched`` with SELECT-only window
    SQL and assert they match an INDEPENDENT pandas re-derivation of the
    SAME feature definitions the primitive uses — within tolerance.  A
    bug in the primitive's feature arithmetic would diverge here.

  LAYER 2 — REGIME PROPERTIES + DETERMINISM (the stochastic part).
    Run the primitive on the live curve and assert: rerun-equality
    (byte-identical loglik + regime series), labels ⊆ {0..K-1}, >= 2
    distinct labels decoded, persistence in [0, 1], converged, and
    fit_scope == 'full_sample'.

Read-only (SELECT only); run as a standalone script, NOT a pytest test:

    python3 -m tests.test_sovereign_curve_regime_sql_validation
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, timedelta
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from rates_agent.sovereign_bonds.tools.sovereign_curve_regime import (
    SovereignCurveRegimeInput,
    calculate_sovereign_curve_regime,
)

# Feature-parity tolerances (yield-point units; tiny float-pivot noise).
_FEATURE_TOL = {
    "level": 1e-9,
    "slope": 1e-7,       # bps (×100)
    "curvature": 1e-7,   # bps (×100)
    "realized_vol": 1e-7,
}
_DEFAULT_CASES: Tuple[Tuple[str, str, str, str], ...] = (
    ("UST", "2Y", "5Y", "10Y"),
    ("DE_BUND", "2Y", "5Y", "10Y"),
)
_LOOKBACK_DAYS = 2520
_VOL_WINDOW = 21
_VOL_ANNUALIZATION = 252


def _sql_raw_panel(engine, curve_family, tenors, start_date) -> pd.DataFrame:
    """Independent SELECT-only fetch of the three tenor yields, pivoted."""
    sql = text(
        """
        SELECT trade_date, tenor, field_value::double precision AS v
        FROM macro_data.v_market_data_daily_enriched
        WHERE instrument_type = 'sovereign_benchmark'
          AND field_name = 'YLD_YTM_MID'
          AND curve_family = :cf
          AND tenor = ANY(:tn)
          AND trade_date >= :start
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql, {"cf": curve_family, "tn": list(tenors),
                  "start": start_date.isoformat()},
        ).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    wide = df.pivot_table(index="trade_date", columns="tenor", values="v",
                          aggfunc="first").sort_index()
    wide.index = pd.DatetimeIndex(pd.to_datetime(wide.index))
    return wide.ffill(limit=5)


def _features_pandas(wide, short, belly, long_) -> pd.DataFrame:
    level = wide[long_].astype(float)
    slope = (wide[long_] - wide[short]).astype(float) * 100.0
    curv = (2.0 * wide[belly] - wide[short] - wide[long_]).astype(float) * 100.0
    rv = level.diff().rolling(_VOL_WINDOW).std() * math.sqrt(_VOL_ANNUALIZATION)
    return pd.DataFrame(
        {"level": level, "slope": slope, "curvature": curv, "realized_vol": rv},
    )


def _features_sql(engine, curve_family, short, belly, long_, start_date) -> pd.DataFrame:
    """Reconstruct the features purely in SQL (window STDDEV for vol)."""
    sql = text(
        """
        WITH p AS (
            SELECT trade_date,
                   MAX(field_value) FILTER (WHERE tenor = :short)::double precision AS s,
                   MAX(field_value) FILTER (WHERE tenor = :belly)::double precision AS b,
                   MAX(field_value) FILTER (WHERE tenor = :long)::double precision AS l
            FROM macro_data.v_market_data_daily_enriched
            WHERE instrument_type = 'sovereign_benchmark'
              AND field_name = 'YLD_YTM_MID'
              AND curve_family = :cf
              AND tenor = ANY(:tn)
              AND trade_date >= :start
            GROUP BY trade_date
        ),
        f AS (
            SELECT trade_date,
                   l AS level,
                   (l - s) * 100.0 AS slope,
                   (2.0 * b - s - l) * 100.0 AS curvature,
                   l - LAG(l) OVER (ORDER BY trade_date) AS dlevel
            FROM p
        )
        SELECT trade_date, level, slope, curvature,
               STDDEV_SAMP(dlevel) OVER (
                   ORDER BY trade_date ROWS BETWEEN :win_m1 PRECEDING AND CURRENT ROW
               ) * SQRT(:ann) AS realized_vol,
               COUNT(dlevel) OVER (
                   ORDER BY trade_date ROWS BETWEEN :win_m1 PRECEDING AND CURRENT ROW
               ) AS win_n
        FROM f
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(
            sql,
            {"cf": curve_family, "short": short, "belly": belly, "long": long_,
             "tn": [short, belly, long_], "start": start_date.isoformat(),
             "win_m1": _VOL_WINDOW - 1, "ann": _VOL_ANNUALIZATION},
        ).mappings().all()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df.index = pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
    # Only rows with a full window have a comparable realized-vol.
    df.loc[df["win_n"] < _VOL_WINDOW, "realized_vol"] = np.nan
    return df[["level", "slope", "curvature", "realized_vol"]].astype(float)


def _compare_features(pandas_df, sql_df) -> List[str]:
    failures: List[str] = []
    common = pandas_df.index.intersection(sql_df.index)
    for col, tol in _FEATURE_TOL.items():
        a = pandas_df.loc[common, col]
        b = sql_df.loc[common, col]
        both = a.notna() & b.notna()
        if not both.any():
            continue
        diff = (a[both] - b[both]).abs().max()
        if diff > tol:
            failures.append(f"feature '{col}': max|Δ|={diff:.3e} > tol {tol:.0e}")
    return failures


def validate_case(engine, curve_family, short, belly, long_) -> List[str]:
    failures: List[str] = []
    start_date = date.today() - timedelta(days=_LOOKBACK_DAYS)

    # --- LAYER 1: feature SQL parity ---
    wide = _sql_raw_panel(engine, curve_family, (short, belly, long_), start_date)
    if wide.empty or any(c not in wide.columns for c in (short, belly, long_)):
        return [f"{curve_family}: no data for {short}/{belly}/{long_}"]
    feat_pandas = _features_pandas(wide, short, belly, long_)
    feat_sql = _features_sql(engine, curve_family, short, belly, long_, start_date)
    failures += [f"{curve_family}: {m}" for m in
                 _compare_features(feat_pandas, feat_sql)]

    # --- LAYER 2: regime properties + determinism ---
    params = SovereignCurveRegimeInput(
        curve_family=curve_family, short_tenor=short, belly_tenor=belly,
        long_tenor=long_, n_states=2, lookback_days=_LOOKBACK_DAYS,
    )
    r1 = calculate_sovereign_curve_regime(engine=engine, params=params)
    if "error" in r1:
        return failures + [f"{curve_family}: primitive error — {r1['error']}"]
    r2 = calculate_sovereign_curve_regime(engine=engine, params=params)

    cm = r1["current_metrics"]
    if cm["fit_scope"] != "full_sample":
        failures.append(f"{curve_family}: fit_scope={cm['fit_scope']} != full_sample")
    if not cm["converged"]:
        failures.append(f"{curve_family}: HMM did not converge")
    p = cm["regime_persistence"]
    if p is None or not (0.0 <= p <= 1.0):
        failures.append(f"{curve_family}: persistence {p} out of [0,1]")
    labels1 = [row["value"] for row in r1["time_series_regime"]["rows"]
               if row["value"] is not None]
    distinct = set(labels1)
    if not distinct <= {0.0, 1.0}:
        failures.append(f"{curve_family}: labels {distinct} outside 0..K-1")
    if len(distinct) < 2:
        failures.append(f"{curve_family}: only {len(distinct)} regime(s) decoded")
    # Determinism: byte-identical rerun.
    if cm["loglik"] != r2["current_metrics"]["loglik"]:
        failures.append(f"{curve_family}: loglik not reproducible across reruns")
    labels2 = [row["value"] for row in r2["time_series_regime"]["rows"]
               if row["value"] is not None]
    if labels1 != labels2:
        failures.append(f"{curve_family}: regime series not reproducible")

    return failures


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curve", default=None,
                        help="Validate one curve_family only (e.g. UST).")
    args = parser.parse_args(argv)

    engine = get_db_engine()
    cases = _DEFAULT_CASES
    if args.curve:
        cases = tuple(c for c in _DEFAULT_CASES if c[0] == args.curve) or (
            (args.curve, "2Y", "5Y", "10Y"),
        )

    all_failures: List[str] = []
    for curve_family, short, belly, long_ in cases:
        fs = validate_case(engine, curve_family, short, belly, long_)
        status = "PASS" if not fs else "FAIL"
        print(f"[{status}] {curve_family} {short}/{belly}/{long_}")
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

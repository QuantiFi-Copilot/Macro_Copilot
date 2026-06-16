#!/usr/bin/env python3
"""test_curve_fair_value_sql_validation.py — live-DB validator.

Unlike the HMM regime fit, the PCA fair-value RESIDUAL is fully
deterministic and independently reproducible, so this validator does a
real NUMERIC parity (not the property substitution the stochastic HMM
needed):

  LAYER 1 — RESIDUAL NUMERIC PARITY.  Independently fetch the tenor levels
    via SELECT-only SQL, run an independent NumPy correlation-PCA
    reconstruction (z-score → SVD → top-k projection → un-standardise →
    observed − reconstruction; complete-case rows), and assert each
    tenor's latest residual matches the primitive's per-tenor
    residual_bps within tolerance.  The residual is invariant to the
    std ddof choice (correlation eigenvectors are ddof-independent and the
    un-standardisation cancels the scale), so this is an exact cross-check.

  LAYER 2 — MODEL-STATE PROPERTIES + DETERMINISM.  explained_variance_ratio
    in (0,1], sums to <= 1, non-increasing; fit_scope == 'full_sample';
    rerun-identical per-tenor residuals.  (Raw loadings get property
    checks, not value parity — eigenvector sign/rotation under
    near-degeneracy makes raw-value parity brittle.)

Read-only.  Run standalone, NOT pytest-collected:

    python3 -m tests.test_curve_fair_value_sql_validation
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import List, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from shared.analytics.rates_fetch import latest_trade_date
from rates_agent.sovereign_bonds.tools.curve_fair_value import (
    CurveFairValueInput,
    calculate_curve_fair_value,
)

_TENORS = ["2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]
_N_COMPONENTS = 3
_LOOKBACK = 2520
_RESIDUAL_TOL_BPS = 0.05  # primitive rounds to 2 dp; float noise tiny
_DEFAULT_CURVES = ("UST", "DE_BUND")


def _sql_levels(engine, curve_family, tenors, start_date) -> pd.DataFrame:
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


def _independent_residual_bps(wide_sorted: pd.DataFrame, n_components: int):
    """Independent correlation-PCA residual (bps) per column at the last
    complete-case date.  Returns {column: latest_residual_bps}."""
    complete = wide_sorted.dropna(how="any")
    X = complete.to_numpy(dtype=float)
    mu = X.mean(axis=0)
    sd = X.std(axis=0, ddof=0)
    Xz = (X - mu) / sd
    _, _, Vt = np.linalg.svd(Xz, full_matrices=False)
    Vk = Vt[:n_components].T            # (n_features, k)
    Pk = Vk @ Vk.T                       # projection onto top-k subspace
    Xhat_z = Xz @ Pk
    out = {}
    for j, col in enumerate(complete.columns):
        recon = Xhat_z[:, j] * sd[j] + mu[j]
        residual = X[:, j] - recon
        out[col] = float(residual[-1]) * 100.0  # bps, latest complete date
    return out


def validate_curve(engine, curve_family) -> List[str]:
    failures: List[str] = []
    # Anchor the independent window to the latest available trade_date — the
    # same way the primitive now does — so the parity check compares identical
    # windows even when the DB lags "today".
    anchor = latest_trade_date(engine, curve_family=curve_family) or date.today()
    start_date = anchor - timedelta(days=_LOOKBACK)
    cols = [f"{curve_family}_{t}" for t in _TENORS]

    wide = _sql_levels(engine, curve_family, _TENORS, start_date)
    if wide.empty or any(t not in wide.columns for t in _TENORS):
        present = [] if wide.empty else list(wide.columns)
        return [f"{curve_family}: missing tenors (present={present})"]
    # Rename to the primitive's column convention + sort like the operator.
    wide = wide.rename(columns={t: f"{curve_family}_{t}" for t in _TENORS})
    wide_sorted = wide[sorted(cols)]
    indep = _independent_residual_bps(wide_sorted, _N_COMPONENTS)

    params = CurveFairValueInput(
        curve_family=curve_family, tenors=_TENORS, focus_tenor="10Y",
        lookback_days=_LOOKBACK,
    )
    r1 = calculate_curve_fair_value(engine=engine, params=params)
    if "error" in r1:
        return [f"{curve_family}: primitive error — {r1['error']}"]
    r2 = calculate_curve_fair_value(engine=engine, params=params)
    cm = r1["current_metrics"]

    # LAYER 1 — residual numeric parity.
    prim = {r["tenor"]: r["residual_bps"] for r in cm["per_tenor"]}
    for tenor in _TENORS:
        col = f"{curve_family}_{tenor}"
        a = prim.get(tenor)
        b = indep.get(col)
        if a is None or b is None:
            failures.append(f"{curve_family} {tenor}: missing residual")
            continue
        if abs(a - b) > _RESIDUAL_TOL_BPS:
            failures.append(
                f"{curve_family} {tenor}: residual {a} bps vs independent "
                f"{b:.4f} bps (Δ={abs(a-b):.4f} > {_RESIDUAL_TOL_BPS})"
            )

    # LAYER 2 — model-state properties + determinism.
    evr = cm["explained_variance_ratio"]
    if not all(0.0 <= v <= 1.0 + 1e-9 for v in evr):
        failures.append(f"{curve_family}: EVR out of (0,1] — {evr}")
    if sum(evr) > 1.0 + 1e-6:
        failures.append(f"{curve_family}: EVR sums to {sum(evr)} > 1")
    if any(evr[i] < evr[i + 1] - 1e-9 for i in range(len(evr) - 1)):
        failures.append(f"{curve_family}: EVR not non-increasing — {evr}")
    if cm["fit_scope"] != "full_sample":
        failures.append(f"{curve_family}: fit_scope != full_sample")
    if cm["per_tenor"] != r2["current_metrics"]["per_tenor"]:
        failures.append(f"{curve_family}: per-tenor residuals not reproducible")

    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curve", default=None)
    args = parser.parse_args(argv)
    engine = get_db_engine()
    curves = (args.curve,) if args.curve else _DEFAULT_CURVES

    all_failures: List[str] = []
    for curve in curves:
        fs = validate_curve(engine, curve)
        print(f"[{'PASS' if not fs else 'FAIL'}] {curve}")
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

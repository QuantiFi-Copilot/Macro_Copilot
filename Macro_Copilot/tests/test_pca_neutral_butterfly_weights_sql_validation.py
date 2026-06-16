#!/usr/bin/env python3
"""test_pca_neutral_butterfly_weights_sql_validation.py — live-DB validator.

GENUINE independent reproduction (NOT a re-call of the production tool).
The PCA fit (SVD) has no closed-form SQL oracle, so — per the PR16 /
TESTING_AND_DB_VALIDATION_POLICY "compensate with an independently-implemented
reference" exception — this validator:

  1. Pulls the REAL yield-change panel from macro-tsdb (read-only SELECT via
     fetch_instrument_panel).
  2. Re-fits correlation PCA in a SEPARATE numpy implementation (fresh
     z-score + economy SVD + canonical sign — it does NOT call
     shared.quant.pca.fit_pca or the production tool's solve).
  3. Solves the 2×2 for the wing weights BY HAND on σ-weighted loadings
     (the correct neutrality system: Σ_j w_j σ_j loading[k,j] = 0).
  4. Asserts the tool's solved weights match the hand solve to tolerance.
  5. Asserts the GENUINE neutrality property: the realized correlation of the
     raw fly (changes @ weights) with the independently-computed factor
     SCORES is ~0 for the neutralized PCs (PC1/PC2) and clearly non-zero for
     PC3 (curvature retained).  This is the load-bearing check that catches
     the σ-weighting (B8) defect — reverting it pushes corr(fly,PC1) to
     ~+0.2..+0.3 on live UST/Bund and this validator FAILS.

Read-only.  Standalone, NOT pytest-collected:

    python3 -m tests.test_pca_neutral_butterfly_weights_sql_validation
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import List, Tuple

import numpy as np

from database.database import get_db_engine
from shared.analytics.curve_bootstrap import sort_tenors_by_years
from shared.analytics.panel_assembly import fetch_instrument_panel
from rates_agent.sovereign_bonds.tools.pca_neutral_butterfly_weights import (
    PcaNeutralButterflyWeightsInput,
    calculate_pca_neutral_butterfly_weights,
)

_DEFAULT_CASES = ("UST", "DE_BUND")
_FIT_TENORS = ["2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]
_FLY = ("2Y", "5Y", "10Y")
_BELLY_W = 2.0
_FIELD = "YLD_YTM_MID"
_LOOKBACK = 2520
_FFILL = 5
# Match the contract: neutralized PCs must read ~0; the realized correlation
# is bounded by SVD/BLAS noise, well below 1e-3 in practice on real curves.
_CORR_TOL = 1e-3
_WEIGHT_TOL = 5e-3   # tool rounds to 4dp; hand solve is full precision


def _independent_pca(X: np.ndarray):
    """Fresh correlation PCA — NOT shared.quant.pca.  Returns
    (loadings (K,d), factor_scores (n,K), column_stds (d,))."""
    mu = X.mean(axis=0)
    sd = X.std(axis=0)  # ddof=0, matching np.std default / fit_pca
    Z = (X - mu) / sd
    U, S, Vt = np.linalg.svd(Z, full_matrices=False)
    loadings = Vt.copy()
    scores = U * S
    for k in range(loadings.shape[0]):
        j = int(np.argmax(np.abs(loadings[k])))
        if loadings[k, j] < 0.0:
            loadings[k] = -loadings[k]
            scores[:, k] = -scores[:, k]
    return loadings, scores, sd


def _hand_solve_sigma(loadings, sd, idx) -> Tuple[float, float]:
    """Solve the 2×2 by hand on σ-weighted loadings (PC1+PC2 neutral)."""
    i_s, i_b, i_l = idx
    sl = loadings * sd[np.newaxis, :]
    A = np.array([[sl[0, i_s], sl[0, i_l]], [sl[1, i_s], sl[1, i_l]]])
    b = -_BELLY_W * np.array([sl[0, i_b], sl[1, i_b]])
    w_s, w_l = np.linalg.solve(A, b)
    return float(w_s), float(w_l)


def _fetch_changes(engine, cf):
    fit_tenors = sort_tenors_by_years(list(set(_FIT_TENORS) | set(_FLY)))
    cols = [f"{cf}_{t}" for t in fit_tenors]
    start = date.today() - timedelta(days=_LOOKBACK)
    raw = fetch_instrument_panel(
        engine=engine,
        leg_specs=[(cf, t, _FIELD) for t in fit_tenors],
        start_date=start, end_date=None, ffill_limit_days=_FFILL,
    )
    if raw.empty or any(c not in raw.columns or raw[c].isna().all() for c in cols):
        return None, None, None
    levels = raw[cols].astype(float)
    changes = levels.diff().dropna(how="any")
    idx = (fit_tenors.index("2Y"), fit_tenors.index("5Y"),
           fit_tenors.index("10Y"))
    return changes, idx, fit_tenors


def validate_case(engine, cf) -> List[str]:
    failures: List[str] = []
    params = PcaNeutralButterflyWeightsInput(
        curve_family=cf, short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
        n_pcs_to_neutralize=2,
    )
    r1 = calculate_pca_neutral_butterfly_weights(engine=engine, params=params)
    if "error" in r1:
        return [f"{cf}: primitive error — {r1['error']}"]
    cm = r1["current_metrics"]

    # ---- Independent reproduction from the raw DB panel ----------------
    changes, idx, _ = _fetch_changes(engine, cf)
    if changes is None:
        return [f"{cf}: could not fetch the change panel for independent re-fit"]
    X = changes.to_numpy(dtype=float)
    loadings, scores, sd = _independent_pca(X)

    # (a) Tool weights match the independent σ-weighted hand solve.
    hw_s, hw_l = _hand_solve_sigma(loadings, sd, idx)
    if abs(cm["short_weight"] - hw_s) > _WEIGHT_TOL:
        failures.append(
            f"{cf}: short_weight {cm['short_weight']} != hand σ-solve "
            f"{hw_s:.6f} (Δ={abs(cm['short_weight']-hw_s):.2e})")
    if abs(cm["long_weight"] - hw_l) > _WEIGHT_TOL:
        failures.append(
            f"{cf}: long_weight {cm['long_weight']} != hand σ-solve "
            f"{hw_l:.6f} (Δ={abs(cm['long_weight']-hw_l):.2e})")

    # (b) GENUINE neutrality: realized corr(raw_fly, factor_score_k) ~0 for
    #     the neutralized PCs, non-zero for PC3.  Uses the TOOL's weights and
    #     the INDEPENDENT scores — the check the old validator could not do.
    i_s, i_b, i_l = idx
    fly = (cm["short_weight"] * X[:, i_s] + _BELLY_W * X[:, i_b]
           + cm["long_weight"] * X[:, i_l])
    realized = {k + 1: float(np.corrcoef(fly, scores[:, k])[0, 1])
                for k in range(scores.shape[1])}
    for pc in (1, 2):
        if abs(realized[pc]) > _CORR_TOL:
            failures.append(
                f"{cf}: realized corr(fly, PC{pc} score) = {realized[pc]:.4f} "
                f"> {_CORR_TOL} — fly is NOT neutral to PC{pc} (B8 regressed)")
    if abs(realized.get(3, 0.0)) < 1e-3:
        failures.append(
            f"{cf}: realized corr(fly, PC3 score) = {realized.get(3)} — the "
            "fly has no curvature exposure (degenerate)")

    # ---- Property smoke-checks (kept) ---------------------------------
    for w in (cm["short_weight"], cm["long_weight"], cm["current_fly_bps"]):
        if w is None:
            failures.append(f"{cf}: a weight/fly value is None")
    if cm["near_degenerate"]:
        failures.append(f"{cf}: PCA flagged near-degenerate")
    r2 = calculate_pca_neutral_butterfly_weights(engine=engine, params=params)
    if cm != r2["current_metrics"]:
        failures.append(f"{cf}: not reproducible")

    print(f"    weights: tool=({cm['short_weight']},{cm['long_weight']}) "
          f"hand=({hw_s:.4f},{hw_l:.4f}); "
          f"realized corr(fly,PC1/2/3)="
          f"({realized[1]:.4f},{realized[2]:.4f},{realized.get(3, float('nan')):.4f})")
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

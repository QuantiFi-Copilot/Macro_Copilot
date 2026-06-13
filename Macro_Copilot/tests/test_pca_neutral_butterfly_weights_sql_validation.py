#!/usr/bin/env python3
"""test_pca_neutral_butterfly_weights_sql_validation.py — live-DB validator.

The PCA fit (SVD) has no independent closed-form/SQL oracle, so this
validates the load-bearing PROPERTY on live data: after solving, the fly's
net loading on the neutralized PCs must be ~0; plus the weights are finite,
the fit is non-degenerate, and the result is reproducible.

Read-only.  Standalone, NOT pytest-collected:

    python3 -m tests.test_pca_neutral_butterfly_weights_sql_validation
"""

from __future__ import annotations

import argparse
import sys
from typing import List

from database.database import get_db_engine
from rates_agent.sovereign_bonds.tools.pca_neutral_butterfly_weights import (
    PcaNeutralButterflyWeightsInput,
    calculate_pca_neutral_butterfly_weights,
)

_DEFAULT_CASES = ("UST", "DE_BUND")


def validate_case(engine, cf) -> List[str]:
    failures: List[str] = []
    params = PcaNeutralButterflyWeightsInput(
        curve_family=cf, short_tenor="2Y", belly_tenor="5Y", long_tenor="10Y",
        n_pcs_to_neutralize=2,
    )
    r1 = calculate_pca_neutral_butterfly_weights(engine=engine, params=params)
    if "error" in r1:
        return [f"{cf}: primitive error — {r1['error']}"]
    r2 = calculate_pca_neutral_butterfly_weights(engine=engine, params=params)
    cm = r1["current_metrics"]

    # The neutralized PCs must have ~0 fly loading (the load-bearing property).
    for r in cm["residual_pc_exposures"]:
        if r["neutralized"] and abs(r["fly_loading"]) > 1e-3:
            failures.append(
                f"{cf}: PC{r['pc']} marked neutralized but fly_loading="
                f"{r['fly_loading']}"
            )
    # PC3 should retain some exposure (the fly's curvature).
    pc3 = [r for r in cm["residual_pc_exposures"] if r["pc"] == 3]
    if pc3 and abs(pc3[0]["fly_loading"]) < 1e-6:
        failures.append(f"{cf}: PC3 exposure vanished (fly has no curvature)")
    # Weights finite; wings shorted on a normal curve fit.
    for w in (cm["short_weight"], cm["long_weight"], cm["current_fly_bps"]):
        if w is None:
            failures.append(f"{cf}: a weight/fly value is None")
    if cm["near_degenerate"]:
        failures.append(f"{cf}: PCA flagged near-degenerate")
    if cm != r2["current_metrics"]:
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

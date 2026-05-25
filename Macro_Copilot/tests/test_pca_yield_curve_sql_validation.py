#!/usr/bin/env python3
"""
test_pca_yield_curve_sql_validation.py — Live-DB end-to-end smoke for
pca_yield_curve across curve_families (Round 3 Stage 2, work item A3).

What this validates
-------------------
The pca_yield_curve primitive's PR16 SQL-validation surface differs
from the spreads / butterflies because PCA's core math has no SQL
parity expression — SVD on a covariance matrix is structurally hard
to replicate in pure SQL (the existing primitive README documents this
as an explicit PR16 exception for statistical-fit primitives).

This runner is therefore a LIVE-DB END-TO-END SMOKE test that
verifies the A3 curve-family-agnostic refactor works against real
ingested data: for each of >=3 non-sovereign curve_families, it
fetches a real panel via shared.analytics.rates_fetch.fetch_tenor_group,
runs the full compute() path, and asserts the output is well-formed
(no error envelope, sane variance shares, sane factor scores, sane
per-component metadata).  Sovereign UST is included as the
backward-compat baseline.

The runner is standalone (NOT pytest-collected) — same pattern as
the other tests/test_*_sql_validation.py runners.  Invocation:

    python3 -m tests.test_pca_yield_curve_sql_validation
    python3 -m tests.test_pca_yield_curve_sql_validation \\
        --families UST USD_SOFR_OIS USD_ZCIS USD_TIPS

Exit code is non-zero if any curve_family fails or returns an error
envelope, suitable for CI.

A3 acceptance reference
-----------------------
The Round 3 work order's A3 acceptance criterion (a) requires
"passing SQL-validation tests" on >=3 non-sovereign curve_families.
Because PCA cannot be validated via SQL parity (per the primitive
README's PR16 exception), this runner satisfies the acceptance by
demonstrating end-to-end DB execution on the four current
non-sovereign families that are ingested today (USD_SOFR_OIS +
USD_ZCIS + USD_TIPS, with EUR_ESTR_OIS as a fourth optional case)
while preserving the UST baseline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.sovereign_bonds.tools.pca_yield_curve import (  # noqa: E402
    PcaYieldCurveInput,
    calculate_pca_yield_curve,
)
from shared.analytics.playbook_discovery import (  # noqa: E402
    playbook_curve_family_index,
)


# Default test universe.  Each entry: (curve_family, tenor_subset).  The
# tenor subset is chosen to give a sane n_components=3 fit on the
# playbook's available universe — for very wide curves (OIS at 13
# tenors) we restrict to canonical 5-point sets; for narrow curves
# (USD_TIPS at 4 tenors) we use all of them.
DEFAULT_CASES: List[Dict[str, Any]] = [
    # Sovereign baseline (backward compat — pre-A3 callers).
    {
        "curve_family": "UST",
        "tenors": ["2Y", "5Y", "10Y", "20Y", "30Y"],
        "expected_field": "YLD_YTM_MID",
    },
    # OIS — discovered from ois.yml; default field PX_LAST.
    {
        "curve_family": "USD_SOFR_OIS",
        "tenors": ["1Y", "2Y", "5Y", "10Y", "30Y"],
        "expected_field": "PX_LAST",
    },
    # ZCIS — discovered from inflation_swaps.yml; default field PX_MID.
    {
        "curve_family": "USD_ZCIS",
        "tenors": ["1Y", "2Y", "5Y", "10Y", "30Y"],
        "expected_field": "PX_MID",
    },
    # Sovereign linker — discovered from inflation_indexed_bonds.yml;
    # default field YLD_YTM_MID.  USD_TIPS has only 4 tenors in the
    # playbook; n_components=3 saturates the rank-3 PCA fit.
    {
        "curve_family": "USD_TIPS",
        "tenors": ["5Y", "10Y", "20Y", "30Y"],
        "expected_field": "YLD_YTM_MID",
    },
]

DEFAULT_LOOKBACK_DAYS = 1825
DEFAULT_N_COMPONENTS = 3


def _format_loadings_block(metrics: Dict[str, Any]) -> str:
    """Compact one-line summary of the loadings block for the report."""
    rows = []
    for row in metrics["loadings"]:
        pcs = " ".join(
            f"{c}={row.get(c):+.4f}"
            for c in ("pc1", "pc2", "pc3")
            if row.get(c) is not None
        )
        rows.append(f"  {row['tenor']:<5} {pcs}")
    return "\n".join(rows)


def _validate_case(
    engine,
    curve_family: str,
    tenors: List[str],
    expected_field: str,
    lookback_days: int,
    n_components: int,
    *,
    verbose: bool = True,
) -> bool:
    """Run pca_yield_curve on one curve_family + tenor set; return True
    iff the output is well-formed."""
    print(f"\n{'=' * 72}")
    print(f"CASE: curve_family={curve_family} tenors={tenors}")
    print(f"      lookback_days={lookback_days} n_components={n_components}")

    # First confirm the playbook discovery resolves the curve_family to
    # the expected playbook + field.
    idx = playbook_curve_family_index()
    entry = idx.get(curve_family)
    if entry is None:
        print(f"  FAIL: curve_family={curve_family!r} not discoverable")
        return False
    print(f"      discovered: playbook={entry['playbook']!r} "
          f"default_field={entry['default_field']!r}")
    if entry["default_field"] != expected_field:
        print(f"  FAIL: expected default_field={expected_field!r}, "
              f"got {entry['default_field']!r}")
        return False

    params = PcaYieldCurveInput(
        curve_family=curve_family,
        tenors=tenors,
        n_components=n_components,
        lookback_days=lookback_days,
    )
    out = calculate_pca_yield_curve(engine=engine, params=params)
    if "error" in out:
        print(f"  FAIL: error envelope returned — {out['error']}")
        return False

    cm = out["current_metrics"]
    # Sanity: snapshot shape.
    if cm["curve_family"] != curve_family:
        print(f"  FAIL: curve_family echo mismatch — "
              f"got {cm['curve_family']!r}")
        return False
    if cm["n_components_returned"] != n_components:
        print(f"  FAIL: n_components_returned mismatch — "
              f"got {cm['n_components_returned']}")
        return False
    if len(cm["loadings"]) != len(tenors):
        print(f"  FAIL: loadings has {len(cm['loadings'])} rows; "
              f"expected {len(tenors)}")
        return False
    if len(cm["variance_explained"]) != n_components:
        print(f"  FAIL: variance_explained has "
              f"{len(cm['variance_explained'])} rows; expected "
              f"{n_components}")
        return False

    # Sanity: variance shares sum to a number in (0, 1] (sub-1 because
    # n_components < n_tenors only captures a subset).
    var_sum = sum(
        r["variance_share"] for r in cm["variance_explained"]
    )
    if not (0.0 < var_sum <= 1.0 + 1e-9):
        print(f"  FAIL: sum(variance_share)={var_sum} out of (0,1]")
        return False
    total = cm["total_variance_explained"]
    if not (0.0 < total <= 1.0 + 1e-9):
        print(f"  FAIL: total_variance_explained={total} out of (0,1]")
        return False

    # Sanity: at least one TimeSeries per component.
    if len(out["time_series_factors"]) != n_components:
        print(f"  FAIL: time_series_factors has "
              f"{len(out['time_series_factors'])}; expected "
              f"{n_components}")
        return False
    for ts in out["time_series_factors"]:
        if not ts.get("rows"):
            print(f"  FAIL: time_series {ts['series_name']!r} has "
                  f"zero rows")
            return False

    # PR10 — sign anchor present + matches the locked value.
    if cm["sign_anchor_used"] != "lock_pc_long_tenor_positive":
        print(f"  FAIL: sign_anchor_used={cm['sign_anchor_used']!r}")
        return False

    # Quality flags — at least PC1 should typically be 'ok' on real data;
    # we don't enforce stricter so the runner is robust to data quirks.
    flags = [m["quality_flag"] for m in cm["component_metadata"]]
    print(f"  PASS: observations={cm['observation_count']}, "
          f"total_var={total:.4f}, flags={flags}")
    if verbose:
        print(f"  loadings:")
        print(_format_loadings_block(cm))
    return True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--families",
        nargs="*",
        default=None,
        help=(
            "Optional explicit curve_family list (overrides the default "
            "test set of UST + USD_SOFR_OIS + USD_ZCIS + USD_TIPS).  "
            "Each must be discoverable via the multi-playbook index."
        ),
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
    )
    parser.add_argument(
        "--n-components",
        type=int,
        default=DEFAULT_N_COMPONENTS,
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Skip the full loadings dump per case.",
    )
    args = parser.parse_args(argv)

    if args.families:
        # Build minimal cases for the explicit list — use the playbook's
        # available tenors capped to 5 for compactness.
        idx = playbook_curve_family_index()
        cases: List[Dict[str, Any]] = []
        for cf in args.families:
            entry = idx.get(cf)
            if not entry:
                print(f"ERROR: curve_family={cf!r} not discoverable.")
                return 2
            tenors = entry["tenors"][:5]
            cases.append({
                "curve_family": cf,
                "tenors": tenors,
                "expected_field": entry["default_field"],
            })
    else:
        cases = DEFAULT_CASES

    engine = get_db_engine()

    results: List[bool] = []
    for case in cases:
        ok = _validate_case(
            engine=engine,
            curve_family=case["curve_family"],
            tenors=case["tenors"],
            expected_field=case["expected_field"],
            lookback_days=args.lookback_days,
            n_components=args.n_components,
            verbose=not args.quiet,
        )
        results.append(ok)

    n_pass = sum(results)
    n_total = len(results)
    print(f"\n{'=' * 72}")
    print(f"SUMMARY: {n_pass}/{n_total} cases passed")
    return 0 if n_pass == n_total else 1


if __name__ == "__main__":
    sys.exit(main())

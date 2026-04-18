#!/usr/bin/env python3
"""
test_scanner_direct.py — Local CLI smoke-test for the extreme-move scanner
============================================================================

Usage (from project root):

    # Default: Scan ALL curves, top 10 results, min |z-score| 1.5
    python -m tests.test_scanner_direct

    # Custom parameters (e.g. only scan Europe, lower z-score threshold)
    python -m tests.test_scanner_direct --curves DE_BUND,FR_OAT,IT_BTP --min_z 1.0 --top 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Path setup — ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine
from rates_agent.tools.schemas import ScannerInput
from rates_agent.tools.scanner import scan_extremes


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the range/percentile scanner tool against TimescaleDB."
    )
    parser.add_argument("--curves", default="", help="Comma-separated curve families (leave empty for ALL)")
    parser.add_argument("--top", type=int, default=10, help="Max results to return (default: 10)")
    parser.add_argument("--min_z", type=float, default=1.5, help="Minimum absolute z-score (default: 1.5)")
    parser.add_argument("--field", default="YLD_YTM_MID", help="Field name (default: YLD_YTM_MID)")
    args = parser.parse_args()

    # Parse comma-separated string to list, handling empty string
    parsed_curves = [c.strip() for c in args.curves.split(",")] if args.curves.strip() else []

    params = ScannerInput(
        curve_families=parsed_curves,
        top_n=args.top,
        min_abs_z_score=args.min_z,
        field_name=args.field,
    )

    print("=" * 72)
    print("GLOBAL Z-SCORE SCANNER — DIRECT TEST")
    print("=" * 72)
    print(f"  curve_families : {parsed_curves if parsed_curves else 'ALL SOVEREIGNS'}")
    print(f"  min_abs_z_score: {params.min_abs_z_score}")
    print(f"  top_n          : {params.top_n}")
    print("-" * 72)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running scan_extremes...")
    result = scan_extremes(engine=engine, params=params)

    print("[3/3] Result:\n")
    if "error" in result:
        print(f"  ⚠  TOOL RETURNED AN ERROR:\n  {result['error']}")
        sys.exit(1)

    print("  ┌─ SCAN SUMMARY ────────────────────────────────")
    print(f"  │  {result.get('scan_summary')}")
    print("  └────────────────────────────────────────────────")

    results = result.get("results", [])
    if not results:
        print("\n  No instruments met the z-score threshold.")
    else:
        print("\n  ┌─ TOP EXTREMES ────────────────────────────────")
        for r in results:
            curve = r.get("curve_family")
            tenor = r.get("tenor")
            z = r.get("z_score")
            yld = r.get("current_yield_pct")
            chg = r.get("daily_change_bps")
            pct = r.get("percentile_252d")
            sig = r.get("signal")
            
            # Format nicely
            print(f"  │ #{r.get('rank')}: {curve} {tenor:<4} | Z: {z:>+6.2f} | Yld: {yld:.3f}% ({chg:>+5.1f}bp) | Pct: {pct}% | {sig}")
        print("  └────────────────────────────────────────────────")

    print("\n" + "=" * 72)
    print("FULL JSON OUTPUT:")
    print("=" * 72)
    print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    main()
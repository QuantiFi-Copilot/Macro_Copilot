#!/usr/bin/env python3
"""
test_yield_levels_direct.py — Local CLI smoke-test for yield_levels
=====================================================================

Usage (from project root):

    # Default: UST 10Y, 1-year lookback
    python -m tests.test_yield_levels_direct

    # Custom parameters
    python -m tests.test_yield_levels_direct --curve DE_BUND --tenor 5Y --days 730

This script:
1.  Connects to TimescaleDB using the production database connection.
2.  Instantiates a ``YieldLevelInput`` from CLI args.
3.  Calls ``get_yield_levels`` and pretty-prints the JSON result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — ensure project root is on sys.path regardless of cwd
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from rates_agent.tools.schemas import YieldLevelInput  # noqa: E402
from rates_agent.tools.yield_levels import get_yield_levels  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the yield_levels math tool against a live TimescaleDB."
    )
    parser.add_argument(
        "--curve",
        default="UST",
        help="curve_family value (default: UST)",
    )
    parser.add_argument(
        "--tenor",
        default="10Y",
        help="Tenor point (default: 10Y)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Lookback in calendar days (default: 365)",
    )
    parser.add_argument(
        "--field",
        default="YLD_YTM_MID",
        help="field_name filter (default: YLD_YTM_MID)",
    )
    args = parser.parse_args()

    # ---- Build validated input ----
    params = YieldLevelInput(
        curve_family=args.curve,
        tenor=args.tenor,
        lookback_days=args.days,
        field_name=args.field,
    )

    print("=" * 72)
    print("YIELD LEVELS TOOL — DIRECT TEST")
    print("=" * 72)
    print(f"  curve_family : {params.curve_family}")
    print(f"  tenor        : {params.tenor}")
    print(f"  lookback_days: {params.lookback_days}")
    print(f"  field_name   : {params.field_name}")
    print("-" * 72)

    # ---- Connect ----
    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    # ---- Execute ----
    print("[2/3] Running get_yield_levels...")
    result = get_yield_levels(engine=engine, params=params)

    # ---- Display ----
    print("[3/3] Result:\n")

    if "error" in result:
        print(f"  ⚠  TOOL RETURNED AN ERROR:\n  {result['error']}")
        sys.exit(1)

    # Print current_metrics cleanly
    metrics = result["current_metrics"]
    print("  ┌─ CURRENT METRICS ─────────────────────────────")
    print(f"  │  As-of date     : {metrics.get('as_of_date')}")
    print(f"  │  Curve / Tenor  : {metrics.get('curve_family')} {metrics.get('tenor')}")
    print(f"  │  Current Yield  : {metrics.get('current_yield_pct'):.4f}%")
    
    if metrics.get("daily_change_bps") is not None:
        print(f"  │  Daily Δ        : {metrics['daily_change_bps']:+.2f} bps")
    if metrics.get("weekly_change_bps") is not None:
        print(f"  │  Weekly Δ       : {metrics['weekly_change_bps']:+.2f} bps")
    if metrics.get("monthly_change_bps") is not None:
        print(f"  │  Monthly Δ      : {metrics['monthly_change_bps']:+.2f} bps")
        
    if metrics.get("z_score") is not None:
        print(f"  │  Z-score (252d) : {metrics['z_score']:+.4f}")
        
    if metrics.get("high_252d_pct") is not None:
        print(f"  │  252d High      : {metrics['high_252d_pct']:.4f}%")
    if metrics.get("low_252d_pct") is not None:
        print(f"  │  252d Low       : {metrics['low_252d_pct']:.4f}%")
    if metrics.get("percentile_252d") is not None:
        print(f"  │  Percentile     : {metrics['percentile_252d']:.1f}%")
        
    print(f"  │  Obs Count      : {metrics.get('observation_count')}")
    print("  └────────────────────────────────────────────────")

    # Full JSON to stdout for piping / inspection
    print("\n" + "=" * 72)
    print("FULL JSON OUTPUT:")
    print("=" * 72)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
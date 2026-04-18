#!/usr/bin/env python3
"""
test_butterfly_direct.py — Local CLI smoke-test for butterfly tool
===================================================================

Usage (from project root):

    # Default: UST 2s5s10s, 1-year lookback
    python -m tests.test_butterfly_direct

    # Custom parameters
    python -m tests.test_butterfly_direct --curve DE_BUND --short 2Y --belly 5Y --long 10Y
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
from rates_agent.tools.schemas import ButterflyInput
from rates_agent.tools.butterfly import calculate_butterfly


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the butterfly tool against TimescaleDB."
    )
    parser.add_argument("--curve", default="UST", help="Curve family (default: UST)")
    parser.add_argument("--short", default="2Y", help="Short wing (default: 2Y)")
    parser.add_argument("--belly", default="5Y", help="Belly point (default: 5Y)")
    parser.add_argument("--long", default="10Y", help="Long wing (default: 10Y)")
    parser.add_argument("--days", type=int, default=365, help="Lookback days (default: 365)")
    parser.add_argument("--field", default="YLD_YTM_MID", help="Field name (default: YLD_YTM_MID)")
    args = parser.parse_args()

    params = ButterflyInput(
        curve_family=args.curve,
        short_tenor=args.short,
        belly_tenor=args.belly,
        long_tenor=args.long,
        lookback_days=args.days,
        field_name=args.field,
    )

    print("=" * 72)
    print("BUTTERFLY TOOL — DIRECT TEST")
    print("=" * 72)
    print(f"  curve_family : {params.curve_family}")
    print(f"  wings / belly: {params.short_tenor} | {params.belly_tenor} | {params.long_tenor}")
    print(f"  lookback_days: {params.lookback_days}")
    print("-" * 72)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running calculate_butterfly...")
    result = calculate_butterfly(engine=engine, params=params)

    print("[3/3] Result:\n")
    if "error" in result:
        print(f"  ⚠  TOOL RETURNED AN ERROR:\n  {result['error']}")
        sys.exit(1)

    metrics = result["current_metrics"]
    print("  ┌─ CURRENT METRICS ─────────────────────────────")
    print(f"  │  As-of date     : {metrics.get('as_of_date')}")
    print(f"  │  Butterfly Label: {metrics.get('butterfly_label')}")
    print(f"  │  Current Bfly   : {metrics.get('current_butterfly_bps'):+.2f} bps")
    if metrics.get("daily_change_bps") is not None:
        print(f"  │  Daily Δ        : {metrics['daily_change_bps']:+.2f} bps")
    if metrics.get("current_z_score") is not None:
        print(f"  │  Z-score (252d) : {metrics['current_z_score']:+.4f}")
    if metrics.get("percentile_252d") is not None:
        print(f"  │  Percentile     : {metrics['percentile_252d']:.1f}%")
    print(f"  │  Short Wing ({params.short_tenor}-{params.belly_tenor}) : {metrics.get('wing_short_bps')} bps")
    print(f"  │  Long Wing  ({params.belly_tenor}-{params.long_tenor}): {metrics.get('wing_long_bps')} bps")
    print("  └────────────────────────────────────────────────")

    print("\n" + "=" * 72)
    print("FULL JSON OUTPUT:")
    print("=" * 72)
    print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    main()
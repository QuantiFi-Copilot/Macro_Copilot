#!/usr/bin/env python3
"""
test_cross_market_direct.py — Local CLI smoke-test for cross_market_spread
===========================================================================

Usage (from project root):

    # Default: IT_BTP vs DE_BUND 10Y, 1-year lookback
    python -m tests.test_cross_market_direct

    # Custom parameters (e.g. US vs Germany 10Y)
    python -m tests.test_cross_market_direct --curve1 UST --curve2 DE_BUND --tenor 10Y
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
from rates_agent.tools.schemas import CrossMarketSpreadInput
from rates_agent.tools.cross_market_spread import calculate_cross_market_spread


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the cross_market_spread tool against TimescaleDB."
    )
    parser.add_argument("--curve1", default="IT_BTP", help="Numerator curve (default: IT_BTP)")
    parser.add_argument("--curve2", default="DE_BUND", help="Denominator curve (default: DE_BUND)")
    parser.add_argument("--tenor", default="10Y", help="Tenor point (default: 10Y)")
    parser.add_argument("--days", type=int, default=365, help="Lookback days (default: 365)")
    parser.add_argument("--field", default="YLD_YTM_MID", help="Field name (default: YLD_YTM_MID)")
    args = parser.parse_args()

    params = CrossMarketSpreadInput(
        curve_family_1=args.curve1,
        curve_family_2=args.curve2,
        tenor=args.tenor,
        lookback_days=args.days,
        field_name=args.field,
    )

    print("=" * 72)
    print("CROSS-MARKET SPREAD TOOL — DIRECT TEST")
    print("=" * 72)
    print(f"  curve_family_1 : {params.curve_family_1}")
    print(f"  curve_family_2 : {params.curve_family_2}")
    print(f"  tenor          : {params.tenor}")
    print(f"  lookback_days  : {params.lookback_days}")
    print("-" * 72)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running calculate_cross_market_spread...")
    result = calculate_cross_market_spread(engine=engine, params=params)

    print("[3/3] Result:\n")
    if "error" in result:
        print(f"  ⚠  TOOL RETURNED AN ERROR:\n  {result['error']}")
        sys.exit(1)

    metrics = result["current_metrics"]
    print("  ┌─ CURRENT METRICS ─────────────────────────────")
    print(f"  │  As-of date     : {metrics.get('as_of_date')}")
    print(f"  │  Spread Label   : {metrics.get('spread_label')}")
    print(f"  │  Current Spread : {metrics.get('current_spread_bps'):+.2f} bps")
    if metrics.get("daily_change_bps") is not None:
        print(f"  │  Daily Δ        : {metrics['daily_change_bps']:+.2f} bps")
    if metrics.get("monthly_change_bps") is not None:
        print(f"  │  Monthly Δ      : {metrics['monthly_change_bps']:+.2f} bps")
    if metrics.get("current_z_score") is not None:
        print(f"  │  Z-score (252d) : {metrics['current_z_score']:+.4f}")
    if metrics.get("percentile_252d") is not None:
        print(f"  │  Percentile     : {metrics['percentile_252d']:.1f}%")
    print(f"  │  {metrics.get('curve_family_1')} Yield : {metrics.get('curve_family_1_yield')}%")
    print(f"  │  {metrics.get('curve_family_2')} Yield : {metrics.get('curve_family_2_yield')}%")
    print("  └────────────────────────────────────────────────")

    print("\n" + "=" * 72)
    print("FULL JSON OUTPUT:")
    print("=" * 72)
    print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    main()
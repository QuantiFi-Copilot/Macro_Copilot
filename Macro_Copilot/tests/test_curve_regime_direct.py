#!/usr/bin/env python3
"""
test_curve_regime_direct.py — Local CLI smoke-test for curve regime classifier
==============================================================================

Usage (from project root):

    # Default: UST 2s10s, 1-day lookback
    python -m tests.test_curve_regime_direct

    # Custom parameters (e.g. UK Gilts, 22-day lookback)
    python -m tests.test_curve_regime_direct --curve UK_GILT --front 2Y --back 10Y --lookback 22d
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
from rates_agent.tools.schemas import CurveRegimeInput
from rates_agent.tools.curve_regime import classify_curve_regime


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the curve regime classifier tool against TimescaleDB."
    )
    parser.add_argument("--curve", default="UST", help="Curve family (default: UST)")
    parser.add_argument("--front", default="2Y", help="Front leg (default: 2Y)")
    parser.add_argument("--back", default="10Y", help="Back leg (default: 10Y)")
    parser.add_argument("--lookback", default="1d", help="Lookback period: 1d, 5d, 22d (default: 1d)")
    parser.add_argument("--field", default="YLD_YTM_MID", help="Field name (default: YLD_YTM_MID)")
    args = parser.parse_args()

    params = CurveRegimeInput(
        curve_family=args.curve,
        front_tenor=args.front,
        back_tenor=args.back,
        lookback_period=args.lookback,
        field_name=args.field,
    )

    print("=" * 72)
    print("CURVE REGIME TOOL — DIRECT TEST")
    print("=" * 72)
    print(f"  curve_family   : {params.curve_family}")
    print(f"  legs           : {params.front_tenor} vs {params.back_tenor}")
    print(f"  lookback_period: {params.lookback_period}")
    print("-" * 72)

    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    print("[2/3] Running classify_curve_regime...")
    result = classify_curve_regime(engine=engine, params=params)

    print("[3/3] Result:\n")
    if "error" in result:
        print(f"  ⚠  TOOL RETURNED AN ERROR:\n  {result['error']}")
        sys.exit(1)

    metrics = result["current_metrics"]
    print("  ┌─ CURRENT METRICS ─────────────────────────────")
    print(f"  │  As-of date     : {metrics.get('as_of_date')} (Prior: {metrics.get('prior_date')})")
    print(f"  │  Spread Label   : {metrics.get('spread_label')}")
    print(f"  │  REGIME TAG     : ** {metrics.get('regime_tag')} **")
    print(f"  │  Description    : {metrics.get('regime_description')}")
    print(f"  │  ")
    print(f"  │  Front Change   : {metrics.get('front_change_bps'):+.2f} bps ({metrics.get('front_tenor')})")
    print(f"  │  Back Change    : {metrics.get('back_change_bps'):+.2f} bps ({metrics.get('back_tenor')})")
    print(f"  │  Spread Change  : {metrics.get('spread_change_bps'):+.2f} bps")
    print("  └────────────────────────────────────────────────")

    print("\n" + "=" * 72)
    print("FULL JSON OUTPUT:")
    print("=" * 72)
    print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    main()
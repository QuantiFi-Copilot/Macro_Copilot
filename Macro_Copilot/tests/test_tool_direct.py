#!/usr/bin/env python3
"""
test_tool_direct.py — Local CLI smoke-test for curve_spread
============================================================

Usage (from project root):

    # Default: UST 2s10s, 1-year lookback
    python -m tests.test_tool_direct

    # Custom parameters
    python -m tests.test_tool_direct --curve DE_BUND --short 2Y --long 10Y --days 730

    # Quick check with a short window
    python -m tests.test_tool_direct --days 90

This script:
1.  Connects to TimescaleDB using the same ``database.database.get_db_engine``
    used by the production ingestion pipeline.
2.  Instantiates a ``CurveSpreadInput`` from CLI args (or sensible defaults).
3.  Calls ``calculate_curve_spread`` and pretty-prints the JSON result.

It does NOT require the MCP server, LangGraph orchestrator, or React frontend.
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
from rates_agent.tools.schemas import CurveSpreadInput  # noqa: E402
from rates_agent.tools.curve_spread import calculate_curve_spread  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the curve_spread math tool against a live TimescaleDB."
    )
    parser.add_argument(
        "--curve",
        default="UST",
        help="curve_family value (default: UST)",
    )
    parser.add_argument(
        "--short",
        default="2Y",
        help="Short-leg tenor (default: 2Y)",
    )
    parser.add_argument(
        "--long",
        default="10Y",
        help="Long-leg tenor (default: 10Y)",
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
    params = CurveSpreadInput(
        curve_family=args.curve,
        short_tenor=args.short,
        long_tenor=args.long,
        lookback_days=args.days,
        field_name=args.field,
    )

    print("=" * 72)
    print("CURVE SPREAD TOOL — DIRECT TEST")
    print("=" * 72)
    print(f"  curve_family : {params.curve_family}")
    print(f"  short_tenor  : {params.short_tenor}")
    print(f"  long_tenor   : {params.long_tenor}")
    print(f"  lookback_days: {params.lookback_days}")
    print(f"  field_name   : {params.field_name}")
    print("-" * 72)

    # ---- Connect ----
    print("[1/3] Creating DB engine...")
    engine = get_db_engine()

    # ---- Execute ----
    print("[2/3] Running calculate_curve_spread...")
    result = calculate_curve_spread(engine=engine, params=params)

    # ---- Display ----
    print("[3/3] Result:\n")

    if "error" in result:
        print(f"  ⚠  TOOL RETURNED AN ERROR:\n  {result['error']}")
        sys.exit(1)

    # Print current_metrics cleanly
    metrics = result["current_metrics"]
    print("  ┌─ CURRENT METRICS ─────────────────────────────")
    print(f"  │  As-of date     : {metrics['as_of_date']}")
    print(f"  │  Curve          : {metrics['curve_family']}")
    print(f"  │  Spread label   : {metrics['spread_label']}")
    print(f"  │  Current spread : {metrics['current_spread_bps']:+.2f} bps")
    if metrics["daily_change_bps"] is not None:
        print(f"  │  Daily change   : {metrics['daily_change_bps']:+.2f} bps")
    if metrics["current_z_score"] is not None:
        print(f"  │  Z-score        : {metrics['current_z_score']:+.4f}")
    print(f"  │  Rolling window : {metrics['rolling_window_days']}d")
    if metrics["short_tenor_yield"] is not None:
        print(f"  │  Short yield    : {metrics['short_tenor_yield']:.4f}%")
    if metrics["long_tenor_yield"] is not None:
        print(f"  │  Long yield     : {metrics['long_tenor_yield']:.4f}%")
    print("  └────────────────────────────────────────────────")

    ts = result["time_series"]
    print(f"\n  Time-series rows: {len(ts)}")
    if ts:
        print(f"  First: {ts[0]['date']}  |  Last: {ts[-1]['date']}")

    # Full JSON to stdout for piping / inspection
    print("\n" + "=" * 72)
    print("FULL JSON OUTPUT (pipe to jq for formatting):")
    print("=" * 72)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
test_fx_vol_scanner_sql_validation.py
======================================

SQL-validates ``run_fx_vol_scanner`` cross-section against an
independent SQL baseline:
  - column set parity: SQL pair-set matches tool's pair-set per scope
  - latest vol per pair matches SQL truth
  - vol_signed rank ordering reproducible from raw SQL values

PR16 admission gate for the Phase E1 vol scanner primitive.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.vol._shared import fx_families_for_scope  # noqa: E402
from fx_agent.vol.tools.vol_scanner import (  # noqa: E402
    FXVolScannerInput,
    run_fx_vol_scanner,
)


TOLERANCE_VOL_ABS = 0.001

DEFAULT_SCOPE_CASES = [
    ("G10", "1M"),
    ("EM", "1M"),
    ("G10_CROSSES", "1M"),
    ("ALL", "1M"),
]


def sql_baseline_universe(
    engine, *, fx_families: List[str], tenor: str, field_name: str,
) -> Dict[str, float]:
    """Return {pair: latest_vol} from SQL for the requested scope."""
    sql = text(
        """
        WITH latest AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                d.trade_date,
                d.field_value::float AS vol,
                ROW_NUMBER() OVER (
                    PARTITION BY im.attributes ->> 'pair'
                    ORDER BY d.trade_date DESC
                ) AS rn
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_vol'
              AND im.attributes ->> 'fx_family' = ANY(:fx_families)
              AND im.tenor = :tenor
              AND im.attributes ->> 'smile_point' = 'ATM'
              AND d.field_name = :field_name
        )
        SELECT pair, vol FROM latest WHERE rn = 1 ORDER BY pair
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "fx_families": fx_families, "tenor": tenor, "field_name": field_name,
        }).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX VOL SCANNER SQL VALIDATION — {len(DEFAULT_SCOPE_CASES)} scope case(s)")
    print("=" * 72)
    fail_count = 0
    for scope, tenor in DEFAULT_SCOPE_CASES:
        tool_out = run_fx_vol_scanner(
            engine, FXVolScannerInput(
                tenor=tenor, market_scope=scope, rank_by="vol_signed",
                field_name=args.field,
            ),
        )
        families = fx_families_for_scope(scope)
        sql_universe = sql_baseline_universe(
            engine, fx_families=families, tenor=tenor, field_name=args.field,
        )

        mismatches: List[str] = []

        # 1. Universe pair-set parity
        tool_pairs = {r["pair"] for r in tool_out["rows"]}
        sql_pairs = set(sql_universe)
        if tool_pairs != sql_pairs:
            diff_in = tool_pairs - sql_pairs
            diff_out = sql_pairs - tool_pairs
            mismatches.append(
                f"pair-set mismatch: tool_only={sorted(diff_in)} "
                f"sql_only={sorted(diff_out)}"
            )

        # 2. Per-pair latest vol parity (use the intersection only)
        for r in tool_out["rows"]:
            p = r["pair"]
            if p not in sql_universe:
                continue
            a, b = r["current_atm_vol_pct"], sql_universe[p]
            if abs(a - b) > TOLERANCE_VOL_ABS:
                mismatches.append(
                    f"{p}: tool={a} sql={b} abs_diff={abs(a-b):.6f}"
                )

        # 3. vol_signed ranking reproducible: tool's ranks should be
        # ordering by vol descending
        vols_in_order = [r["current_atm_vol_pct"] for r in tool_out["rows"]]
        if vols_in_order != sorted(vols_in_order, reverse=True):
            mismatches.append("vol_signed ranking not descending")

        status = "PASS" if not mismatches else "FAIL"
        if mismatches:
            fail_count += 1
        print(f"  [{status}] scope={scope} tenor={tenor} ({len(tool_pairs)} pairs)")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_SCOPE_CASES) - fail_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

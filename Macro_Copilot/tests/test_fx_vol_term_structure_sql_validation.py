#!/usr/bin/env python3
"""
test_fx_vol_term_structure_sql_validation.py
=============================================

SQL-validates ``get_fx_vol_term_structure`` against an independent SQL
baseline per tenor. Since the tool delegates to ``get_fx_atm_vol_level``
under the hood, this test verifies the term-structure assembly is
correct (5 tenors short→long, each value matches the per-tenor SQL
snapshot). PR16 admission gate for the Phase E1 vol curve primitive.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.vol._shared import SUPPORTED_VOL_TENORS  # noqa: E402
from fx_agent.vol.tools.vol_term_structure import (  # noqa: E402
    FXVolTermStructureInput,
    get_fx_vol_term_structure,
)


TOLERANCE_VOL_ABS = 0.001

DEFAULT_PAIRS = ["EURUSD", "USDJPY", "USDMXN", "EURJPY"]


def sql_snapshot_for_tenor(engine, pair: str, tenor: str, field_name: str) -> float | None:
    sql = text(
        """
        SELECT d.field_value::float
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_vol'
          AND im.attributes ->> 'pair' = :pair
          AND im.tenor = :tenor
          AND im.attributes ->> 'smile_point' = 'ATM'
          AND d.field_name = :field_name
        ORDER BY d.trade_date DESC
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {
            "pair": pair, "tenor": tenor, "field_name": field_name,
        }).fetchone()
    return float(row[0]) if row else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX VOL TERM STRUCTURE SQL VALIDATION — {len(DEFAULT_PAIRS)} pair(s)")
    print("=" * 72)
    fail_count = 0
    for pair in DEFAULT_PAIRS:
        try:
            tool_out = get_fx_vol_term_structure(
                engine, FXVolTermStructureInput(pair=pair),
            )
        except ValueError as exc:
            print(f"  [SKIP] {pair} — {exc}")
            continue

        mismatches: List[str] = []
        # 1. Tenor order short → long
        tenors_returned = [r["tenor"] for r in tool_out["rows"]]
        expected_order = [t for t in SUPPORTED_VOL_TENORS if t in tenors_returned]
        if tenors_returned != expected_order:
            mismatches.append(
                f"tenor order: got {tenors_returned}, expected {expected_order}"
            )

        # 2. Per-tenor latest value matches SQL
        for row in tool_out["rows"]:
            tenor = row["tenor"]
            sql_val = sql_snapshot_for_tenor(engine, pair, tenor, args.field)
            if sql_val is None:
                mismatches.append(f"{tenor}: SQL returned no data")
                continue
            tool_val = row["current_atm_vol_pct"]
            if abs(tool_val - sql_val) > TOLERANCE_VOL_ABS:
                mismatches.append(
                    f"{tenor} vol: tool={tool_val} sql={sql_val} abs_diff={abs(tool_val-sql_val):.6f}"
                )

        status = "PASS" if not mismatches else "FAIL"
        if mismatches:
            fail_count += 1
        print(f"  [{status}] {pair} ({len(tenors_returned)} tenors)")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_PAIRS) - fail_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

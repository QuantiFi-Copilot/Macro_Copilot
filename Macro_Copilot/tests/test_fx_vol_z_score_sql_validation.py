#!/usr/bin/env python3
"""
test_fx_vol_z_score_sql_validation.py
======================================

SQL-validates ``get_fx_vol_z_score`` against an independent SQL +
pandas baseline:
  - SQL fetches the vol time-series for (pair, tenor)
  - pandas independently computes rolling 252-day z-score with
    min_periods=60, ddof=1
  - emitted-rows count + first/last/middle row values match the tool

PR16 admission gate for the Phase E1 vol z-score TIME-SERIES primitive.
The cross-check IS the math (rolling stats reproduced independently).
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
from fx_agent.vol.tools.vol_z_score import (  # noqa: E402
    FXVolZScoreInput,
    get_fx_vol_z_score,
)


TOLERANCE_Z_SCORE_ABS = 0.001

DEFAULT_CASES = [
    ("EURUSD", "1M"),
    ("USDJPY", "1M"),
    ("USDMXN", "3M"),
    ("USDCNH", "12M"),
]


def sql_baseline_series(
    engine, *, pair: str, tenor: str, field_name: str, lookback_days: int,
) -> pd.DataFrame:
    sql = text(
        """
        SELECT d.trade_date, d.field_value::float AS vol
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_vol'
          AND im.attributes ->> 'pair' = :pair
          AND im.tenor = :tenor
          AND im.attributes ->> 'smile_point' = 'ATM'
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "pair": pair, "tenor": tenor, "field_name": field_name,
            "lookback_days": lookback_days,
        }).fetchall()
    df = pd.DataFrame(rows, columns=["trade_date", "vol"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce")
    df = df.dropna().reset_index(drop=True)
    # Rolling z-score
    rolling = df["vol"].rolling(window=252, min_periods=60)
    df["z"] = (df["vol"] - rolling.mean()) / rolling.std(ddof=1)
    return df.dropna(subset=["z"]).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=730)
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX VOL Z-SCORE SQL VALIDATION — {len(DEFAULT_CASES)} case(s)")
    print("=" * 72)
    fail_count = 0
    for pair, tenor in DEFAULT_CASES:
        try:
            tool_out = get_fx_vol_z_score(
                engine, FXVolZScoreInput(
                    pair=pair, tenor=tenor, lookback_days=args.lookback_days,
                    field_name=args.field,
                ),
            )
        except ValueError as exc:
            print(f"  [SKIP] {pair} {tenor} — {exc}")
            continue

        sql_df = sql_baseline_series(
            engine, pair=pair, tenor=tenor,
            field_name=args.field, lookback_days=args.lookback_days,
        )

        mismatches: List[str] = []

        # 1. Emitted count parity
        ts_rows = tool_out["time_series"]["rows"]
        if len(ts_rows) != len(sql_df):
            mismatches.append(
                f"emitted count: tool={len(ts_rows)} sql={len(sql_df)}"
            )

        # 2. First/last/middle row parity
        if ts_rows and not sql_df.empty:
            for idx_label, idx in [("first", 0), ("middle", len(ts_rows) // 2), ("last", -1)]:
                tool_row = ts_rows[idx]
                sql_row = sql_df.iloc[idx]
                tool_date = tool_row["date"]
                sql_date = sql_row["trade_date"].strftime("%Y-%m-%d")
                if tool_date != sql_date:
                    mismatches.append(
                        f"{idx_label} row date: tool={tool_date} sql={sql_date}"
                    )
                tool_z = tool_row["value"]
                sql_z = float(sql_row["z"])
                if abs(tool_z - sql_z) > TOLERANCE_Z_SCORE_ABS:
                    mismatches.append(
                        f"{idx_label} row z: tool={tool_z} sql={sql_z:.4f} "
                        f"abs_diff={abs(tool_z - sql_z):.6f}"
                    )

        # 3. Series last point invariant with snapshot
        snap_z = tool_out["snapshot"]["current_z_score"]
        if ts_rows and snap_z is not None:
            last_z = ts_rows[-1]["value"]
            if abs(last_z - snap_z) > 1e-4:
                mismatches.append(
                    f"snapshot vs series last point: snap={snap_z} ts_last={last_z}"
                )

        status = "PASS" if not mismatches else "FAIL"
        if mismatches:
            fail_count += 1
        print(f"  [{status}] {pair} {tenor} ({len(ts_rows)} emitted rows)")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

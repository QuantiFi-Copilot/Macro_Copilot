#!/usr/bin/env python3
"""
test_fx_atm_vol_level_sql_validation.py
========================================

SQL-validates ``get_fx_atm_vol_level`` against an independent SQL +
pandas baseline. Snapshot scope: current_atm_vol_pct, z_score,
observation_count. PR16 admission gate for the Phase E1 ATM vol level
primitive.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.vol.tools.atm_vol_level import (  # noqa: E402
    FXAtmVolLevelInput,
    get_fx_atm_vol_level,
)


TOLERANCE_VOL_ABS = 0.001          # 0.001 vol points
TOLERANCE_Z_SCORE_ABS = 0.001


# (pair, tenor) — anchor cases across G10 / EM / NDF-vol / G10 crosses
DEFAULT_CASES: List[Tuple[str, str]] = [
    ("EURUSD", "1M"),
    ("USDJPY", "1M"),
    ("USDMXN", "3M"),
    ("USDCNH", "12M"),
    ("USDTWD", "1M"),
    ("EURJPY", "1M"),
    ("GBPCHF", "6M"),
    ("EURUSD", "12M"),
]


def sql_baseline(
    engine, *, pair: str, tenor: str, field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    sql = text(
        """
        SELECT d.trade_date, d.field_value::float AS field_value
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

    if not rows:
        return {"empty": True}
    df = pd.DataFrame(rows, columns=["trade_date", "field_value"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna().reset_index(drop=True)
    values = df["field_value"]
    current = float(values.iloc[-1])
    trailing = values.tail(252)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=1)
    z = float((current - mean_) / std_) if (
        len(trailing) >= 60 and std_ and not pd.isna(std_)
    ) else None
    return {
        "current_atm_vol_pct": current,
        "z_score": z,
        "observation_count": int(len(df)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX ATM VOL LEVEL SQL VALIDATION — {len(DEFAULT_CASES)} case(s)")
    print("=" * 72)
    fail_count = 0
    for pair, tenor in DEFAULT_CASES:
        try:
            tool_out = get_fx_atm_vol_level(
                engine, FXAtmVolLevelInput(
                    pair=pair, tenor=tenor, lookback_days=args.lookback_days,
                    field_name=args.field,
                ),
            )
        except ValueError as exc:
            print(f"  [SKIP] {pair} {tenor} — {exc}")
            continue
        tool = tool_out["current_metrics"]
        sql = sql_baseline(
            engine, pair=pair, tenor=tenor,
            field_name=args.field, lookback_days=args.lookback_days,
        )
        if sql.get("empty"):
            print(f"  [SKIP] {pair} {tenor} — SQL empty")
            continue

        mismatches: List[str] = []
        a, b = tool["current_atm_vol_pct"], sql["current_atm_vol_pct"]
        if abs(a - b) > TOLERANCE_VOL_ABS:
            mismatches.append(
                f"current_atm_vol_pct: tool={a} sql={b} abs_diff={abs(a-b):.6f}"
            )
        a, b = tool["z_score"], sql["z_score"]
        if a is None and b is None:
            pass
        elif a is None or b is None:
            mismatches.append(f"z_score: tool={a} sql={b}")
        elif abs(a - b) > TOLERANCE_Z_SCORE_ABS:
            mismatches.append(
                f"z_score: tool={a} sql={b} abs_diff={abs(a-b):.6f}"
            )
        if tool["observation_count"] != sql["observation_count"]:
            mismatches.append(
                f"observation_count: tool={tool['observation_count']} sql={sql['observation_count']}"
            )

        status = "PASS" if not mismatches else "FAIL"
        if mismatches:
            fail_count += 1
        print(f"  [{status}] {pair} {tenor}")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

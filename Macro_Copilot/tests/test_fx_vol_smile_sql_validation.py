#!/usr/bin/env python3
"""
test_fx_vol_smile_sql_validation.py
========================================

SQL-validates ``get_fx_vol_smile`` against an independent SQL +
pandas baseline. Snapshot scope: for each of the 5 smile points
(ATM, 25R, 25B, 10R, 10B) — current_vol_pts, z_score,
observation_count. PR16 admission gate for the Phase E2 aggregate
smile primitive.

ATM is queried from instrument_type='fx_vol' (smile_point='ATM');
25R/25B/10R/10B from instrument_type='fx_vol_smile'.
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
from fx_agent.vol.tools.vol_smile import (  # noqa: E402
    FXVolSmileInput,
    get_fx_vol_smile,
)


TOLERANCE_VOL_ABS = 0.001
TOLERANCE_Z_SCORE_ABS = 0.001


# (pair, tenor) — anchor cases
DEFAULT_CASES: List[Tuple[str, str]] = [
    ("EURUSD", "1M"),
    ("USDJPY", "1M"),
    ("USDMXN", "3M"),
    ("USDCNH", "12M"),
]


def _series_stats(df: pd.DataFrame) -> Dict[str, Any]:
    df = df.copy()
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna().reset_index(drop=True)
    if df.empty:
        return {"empty": True}
    values = df["field_value"]
    current = float(values.iloc[-1])
    trailing = values.tail(252)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=1)
    z = float((current - mean_) / std_) if (
        len(trailing) >= 60 and std_ and not pd.isna(std_)
    ) else None
    return {
        "current_vol_pts": current,
        "z_score": z,
        "observation_count": int(len(df)),
    }


def sql_baseline_atm(
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
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "pair": pair, "tenor": tenor,
            "field_name": field_name, "lookback_days": lookback_days,
        }).fetchall()
    df = pd.DataFrame(rows, columns=["trade_date", "field_value"]) if rows else pd.DataFrame(columns=["trade_date", "field_value"])
    return _series_stats(df)


def sql_baseline_smile_point(
    engine, *, pair: str, smile_point: str, tenor: str, field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    sql = text(
        """
        SELECT d.trade_date, d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_vol_smile'
          AND im.attributes ->> 'pair' = :pair
          AND im.attributes ->> 'smile_point' = :smile_point
          AND im.tenor = :tenor
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "pair": pair, "smile_point": smile_point, "tenor": tenor,
            "field_name": field_name, "lookback_days": lookback_days,
        }).fetchall()
    df = pd.DataFrame(rows, columns=["trade_date", "field_value"]) if rows else pd.DataFrame(columns=["trade_date", "field_value"])
    return _series_stats(df)


_POINT_KEY_MAP = {
    "atm": ("ATM", "atm"),
    "rr_25": ("25R", "rr_25"),
    "bf_25": ("25B", "bf_25"),
    "rr_10": ("10R", "rr_10"),
    "bf_10": ("10B", "bf_10"),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX VOL SMILE SQL VALIDATION — {len(DEFAULT_CASES)} case(s) × 5 points")
    print("=" * 72)
    fail_count = 0
    skip_count = 0
    for pair, tenor in DEFAULT_CASES:
        try:
            tool_out = get_fx_vol_smile(
                engine, FXVolSmileInput(
                    pair=pair, tenor=tenor,
                    lookback_days=args.lookback_days, field_name=args.field,
                ),
            )
        except ValueError as exc:
            print(f"  [SKIP] {pair} {tenor} — {exc}")
            skip_count += 1
            continue
        tool_metrics = tool_out["current_metrics"]

        # Build SQL baselines for all 5 points
        baselines: Dict[str, Dict[str, Any]] = {}
        baselines["atm"] = sql_baseline_atm(
            engine, pair=pair, tenor=tenor,
            field_name=args.field, lookback_days=args.lookback_days,
        )
        for tool_key, (smile_point, _) in _POINT_KEY_MAP.items():
            if tool_key == "atm":
                continue
            baselines[tool_key] = sql_baseline_smile_point(
                engine, pair=pair, smile_point=smile_point, tenor=tenor,
                field_name=args.field, lookback_days=args.lookback_days,
            )

        mismatches: List[str] = []
        for tool_key in _POINT_KEY_MAP:
            tool_point = tool_metrics[tool_key]
            sql = baselines[tool_key]
            if sql.get("empty"):
                mismatches.append(f"{tool_key}: SQL empty (tool returned a value)")
                continue

            a, b = tool_point["current_vol_pts"], sql["current_vol_pts"]
            if abs(a - b) > TOLERANCE_VOL_ABS:
                mismatches.append(
                    f"{tool_key} current_vol_pts: tool={a} sql={b} abs_diff={abs(a-b):.6f}"
                )
            a, b = tool_point["z_score"], sql["z_score"]
            if a is None and b is None:
                pass
            elif a is None or b is None:
                mismatches.append(f"{tool_key} z_score: tool={a} sql={b}")
            elif abs(a - b) > TOLERANCE_Z_SCORE_ABS:
                mismatches.append(
                    f"{tool_key} z_score: tool={a} sql={b} abs_diff={abs(a-b):.6f}"
                )
            if tool_point["observation_count"] != sql["observation_count"]:
                mismatches.append(
                    f"{tool_key} observation_count: tool={tool_point['observation_count']} sql={sql['observation_count']}"
                )

        status = "PASS" if not mismatches else "FAIL"
        if mismatches:
            fail_count += 1
        print(f"  [{status}] {pair} {tenor}")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count - skip_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

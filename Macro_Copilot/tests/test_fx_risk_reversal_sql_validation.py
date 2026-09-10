#!/usr/bin/env python3
"""
test_fx_risk_reversal_sql_validation.py
========================================

SQL-validates ``get_fx_risk_reversal`` against an independent SQL +
pandas baseline. Snapshot scope: current_risk_reversal_vol_pts,
z_score, observation_count. PR16 admission gate for the Phase E2
risk-reversal smile primitive.

Mirror of test_fx_atm_vol_level_sql_validation.py for the fx_vol_smile
substrate at smile_point ∈ {'25R', '10R'}.
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
from fx_agent.vol.tools.risk_reversal import (  # noqa: E402
    FXRiskReversalInput,
    get_fx_risk_reversal,
)


TOLERANCE_VOL_ABS = 0.001          # 0.001 vol points
TOLERANCE_Z_SCORE_ABS = 0.001


# (pair, delta_anchor, tenor) — anchor cases across G10 USD majors,
# EM USD-leg, and NDF currencies. fx_vol_smile coverage is USD-leg
# only (no G10 crosses) per the smile-differential convention.
DEFAULT_CASES: List[Tuple[str, int, str]] = [
    ("EURUSD", 25, "1M"),
    ("EURUSD", 10, "1M"),
    ("USDJPY", 25, "1M"),
    ("USDMXN", 25, "3M"),
    ("USDCNH", 25, "12M"),
    ("USDBRL", 25, "1M"),
    ("EURUSD", 25, "12M"),
    ("USDJPY", 10, "3M"),
]


def sql_baseline(
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
        "current_risk_reversal_vol_pts": current,
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
    print(f"FX RISK REVERSAL SQL VALIDATION — {len(DEFAULT_CASES)} case(s)")
    print("=" * 72)
    fail_count = 0
    skip_count = 0
    for pair, delta, tenor in DEFAULT_CASES:
        smile_point = f"{delta}R"
        try:
            tool_out = get_fx_risk_reversal(
                engine, FXRiskReversalInput(
                    pair=pair, delta_anchor=delta, tenor=tenor,
                    lookback_days=args.lookback_days, field_name=args.field,
                ),
            )
        except ValueError as exc:
            print(f"  [SKIP] {pair} {smile_point} {tenor} — {exc}")
            skip_count += 1
            continue
        tool = tool_out["current_metrics"]
        sql = sql_baseline(
            engine, pair=pair, smile_point=smile_point, tenor=tenor,
            field_name=args.field, lookback_days=args.lookback_days,
        )
        if sql.get("empty"):
            print(f"  [SKIP] {pair} {smile_point} {tenor} — SQL empty")
            skip_count += 1
            continue

        mismatches: List[str] = []
        a, b = tool["current_risk_reversal_vol_pts"], sql["current_risk_reversal_vol_pts"]
        if abs(a - b) > TOLERANCE_VOL_ABS:
            mismatches.append(
                f"current_risk_reversal_vol_pts: tool={a} sql={b} abs_diff={abs(a-b):.6f}"
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
        print(f"  [{status}] {pair} {smile_point} {tenor}")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count - skip_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

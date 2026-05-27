#!/usr/bin/env python3
"""
test_fx_vol_calendar_spread_sql_validation.py
=============================================

SQL-validates ``get_fx_vol_calendar_spread`` against an independent
SQL + pandas baseline. Recomputes:
  - short-tenor implied vol from fx_vol substrate
  - long-tenor implied vol from fx_vol substrate
  - signed spread per spread_direction
  - 252-day z-score / observation count on the spread series

PR16 admission gate for the Phase E3 calendar-spread primitive.
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
from fx_agent.vol.tools.vol_calendar_spread import (  # noqa: E402
    FXVolCalendarSpreadInput,
    get_fx_vol_calendar_spread,
)


TOLERANCE_VOL_ABS = 0.001
TOLERANCE_Z_SCORE_ABS = 0.001


# (pair, short_tenor, long_tenor, spread_direction)
DEFAULT_CASES: List[Tuple[str, str, str, str]] = [
    ("EURUSD", "1M", "3M", "long_minus_short"),
    ("EURUSD", "1M", "12M", "long_minus_short"),
    ("EURUSD", "1M", "12M", "short_minus_long"),
    ("USDJPY", "1M", "3M", "long_minus_short"),
    ("USDMXN", "3M", "6M", "long_minus_short"),
    ("USDCNH", "1M", "12M", "long_minus_short"),
]


def _fetch_leg(engine, *, pair: str, tenor: str, field_name: str, lookback_days: int) -> pd.Series:
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
            "pair": pair, "tenor": tenor, "field_name": field_name,
            "lookback_days": lookback_days,
        }).fetchall()
    if not rows:
        return pd.Series(dtype="float64")
    df = pd.DataFrame(rows, columns=["trade_date", "field_value"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna().sort_values("trade_date")
    return pd.Series(df["field_value"].values, index=df["trade_date"])


def sql_baseline(
    engine, *, pair: str, short_tenor: str, long_tenor: str,
    spread_direction: str, field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    short_s = _fetch_leg(engine, pair=pair, tenor=short_tenor,
                        field_name=field_name, lookback_days=lookback_days)
    long_s = _fetch_leg(engine, pair=pair, tenor=long_tenor,
                       field_name=field_name, lookback_days=lookback_days)
    if short_s.empty or long_s.empty:
        return {"empty": True}
    joined = pd.concat({"short": short_s, "long": long_s}, axis=1, join="inner").dropna()
    if joined.empty:
        return {"empty": True}
    if spread_direction == "long_minus_short":
        spread = joined["long"] - joined["short"]
    else:
        spread = joined["short"] - joined["long"]
    current = float(spread.iloc[-1])
    trailing = spread.tail(252)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=1)
    z = float((current - mean_) / std_) if (
        len(trailing) >= 60 and std_ and not pd.isna(std_)
    ) else None
    return {
        "current_short_vol_pct": float(joined["short"].iloc[-1]),
        "current_long_vol_pct": float(joined["long"].iloc[-1]),
        "current_spread_vol_pts": current,
        "z_score": z,
        "observation_count": int(len(spread)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX VOL CALENDAR SPREAD SQL VALIDATION — {len(DEFAULT_CASES)} case(s)")
    print("=" * 72)
    fail_count = 0
    skip_count = 0
    for pair, short_t, long_t, direction in DEFAULT_CASES:
        try:
            tool_out = get_fx_vol_calendar_spread(
                engine, FXVolCalendarSpreadInput(
                    pair=pair, short_tenor=short_t, long_tenor=long_t,
                    spread_direction=direction,
                    lookback_days=args.lookback_days, field_name=args.field,
                ),
            )
        except ValueError as exc:
            print(f"  [SKIP] {pair} {short_t}/{long_t} {direction} — {exc}")
            skip_count += 1
            continue
        tool = tool_out["current_metrics"]
        sql = sql_baseline(
            engine, pair=pair, short_tenor=short_t, long_tenor=long_t,
            spread_direction=direction, field_name=args.field,
            lookback_days=args.lookback_days,
        )
        if sql.get("empty"):
            print(f"  [SKIP] {pair} {short_t}/{long_t} {direction} — SQL empty")
            skip_count += 1
            continue

        mismatches: List[str] = []
        for key in ("current_short_vol_pct", "current_long_vol_pct", "current_spread_vol_pts"):
            a, b = tool[key], sql[key]
            if abs(a - b) > TOLERANCE_VOL_ABS:
                mismatches.append(f"{key}: tool={a} sql={b} abs_diff={abs(a-b):.6f}")
        a, b = tool["z_score"], sql["z_score"]
        if a is None and b is None:
            pass
        elif a is None or b is None:
            mismatches.append(f"z_score: tool={a} sql={b}")
        elif abs(a - b) > TOLERANCE_Z_SCORE_ABS:
            mismatches.append(f"z_score: tool={a} sql={b} abs_diff={abs(a-b):.6f}")
        if tool["observation_count"] != sql["observation_count"]:
            mismatches.append(
                f"observation_count: tool={tool['observation_count']} sql={sql['observation_count']}"
            )

        status = "PASS" if not mismatches else "FAIL"
        if mismatches:
            fail_count += 1
        print(f"  [{status}] {pair} {short_t}/{long_t} {direction}")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count - skip_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

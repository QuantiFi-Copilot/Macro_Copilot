#!/usr/bin/env python3
"""
test_fx_implied_yield_differential_sql_validation.py
====================================================

SQL-validates ``get_fx_implied_yield_differential`` against an
independent SQL + pandas baseline. Recomputes:
  - inner-joined (spot, forward_points) history
  - JPY-aware divisor → fp_spot_units
  - raw CIP differential (F/S - 1) * (annual / tenor_days) * 100
  - sign-adjusted local_minus_usd per usd_leg_position
  - 252-day z-score / observation count on the differential series

PR16 admission gate for the Phase B+ implied-yield-differential
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
from fx_agent.forwards.tools.implied_yield_differential import (  # noqa: E402
    FXImpliedYieldDifferentialInput,
    get_fx_implied_yield_differential,
)


TOLERANCE_PCT_ABS = 0.005          # 0.5 bp
TOLERANCE_Z_SCORE_ABS = 0.005


_TENOR_DAYS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}
_JPY_DIVISOR = 100.0
_DEFAULT_DIVISOR = 10000.0
_ANNUALIZATION = 252


def _resolve_sign(pair: str) -> int:
    base, quote = pair[:3], pair[3:]
    if base == "USD":
        return +1
    if quote == "USD":
        return -1
    raise ValueError(f"pair {pair} has no USD leg")


# (pair, tenor) — anchor cases across G10 + EM
DEFAULT_CASES: List[Tuple[str, str]] = [
    ("EURUSD", "1M"),
    ("USDJPY", "1M"),
    ("AUDUSD", "1M"),
    ("USDCAD", "3M"),
    ("USDMXN", "3M"),
    ("USDZAR", "1M"),
    ("USDTRY", "3M"),
    ("EURUSD", "12M"),
]


def sql_baseline(
    engine, *, pair: str, tenor: str, field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    sql = text(
        """
        WITH spot_history AS (
            SELECT d.trade_date, d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND im.attributes ->> 'pair' = :pair
              AND d.field_name = :field_name
              AND d.trade_date >= :start_date
        ),
        fwd_history AS (
            SELECT d.trade_date, d.field_value::float AS forward_points
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_forward'
              AND im.attributes ->> 'pair' = :pair
              AND im.tenor = :tenor
              AND d.field_name = :field_name
              AND d.trade_date >= :start_date
        )
        SELECT s.trade_date, s.spot, f.forward_points
        FROM spot_history s
        INNER JOIN fwd_history f ON s.trade_date = f.trade_date
        ORDER BY s.trade_date ASC
        """
    )
    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=lookback_days)
    ).date()
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "pair": pair, "tenor": tenor, "field_name": field_name,
            "start_date": start_date,
        }).fetchall()
    if not rows:
        return {"empty": True}
    df = pd.DataFrame(rows, columns=["trade_date", "spot", "forward_points"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
    df["forward_points"] = pd.to_numeric(df["forward_points"], errors="coerce")
    df = df.dropna(subset=["spot", "forward_points"])
    df = df.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
    df = df.set_index("trade_date").ffill(limit=5).dropna()
    if df.empty:
        return {"empty": True}

    divisor = _JPY_DIVISOR if "JPY" in pair else _DEFAULT_DIVISOR
    df["fp_spot_units"] = df["forward_points"] / divisor
    df["outright"] = df["spot"] + df["fp_spot_units"]
    df["raw_diff_pct"] = (
        (df["outright"] / df["spot"] - 1.0)
        * (_ANNUALIZATION / _TENOR_DAYS[tenor])
        * 100.0
    )
    sign = _resolve_sign(pair)
    df["local_minus_usd_pct"] = sign * df["raw_diff_pct"]
    diff_series = df["local_minus_usd_pct"]
    current = float(diff_series.iloc[-1])
    trailing = diff_series.tail(252)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=1)
    z = float((current - mean_) / std_) if (
        len(trailing) >= 60 and std_ and not pd.isna(std_)
    ) else None
    return {
        "current_implied_yield_differential_pct": current,
        "z_score": z,
        "observation_count": int(len(diff_series)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX IMPLIED YIELD DIFFERENTIAL SQL VALIDATION — {len(DEFAULT_CASES)} case(s)")
    print("=" * 72)
    fail_count = 0
    skip_count = 0
    for pair, tenor in DEFAULT_CASES:
        try:
            tool_out = get_fx_implied_yield_differential(
                engine, FXImpliedYieldDifferentialInput(
                    pair=pair, tenor=tenor,
                    lookback_days=args.lookback_days, field_name=args.field,
                ),
            )
        except ValueError as exc:
            print(f"  [SKIP] {pair} {tenor} — {exc}")
            skip_count += 1
            continue
        tool = tool_out["current_metrics"]
        sql = sql_baseline(
            engine, pair=pair, tenor=tenor,
            field_name=args.field, lookback_days=args.lookback_days,
        )
        if sql.get("empty"):
            print(f"  [SKIP] {pair} {tenor} — SQL empty")
            skip_count += 1
            continue

        mismatches: List[str] = []
        a, b = tool["current_implied_yield_differential_pct"], sql["current_implied_yield_differential_pct"]
        if abs(a - b) > TOLERANCE_PCT_ABS:
            mismatches.append(
                f"current_implied_yield_differential_pct: tool={a} sql={b} abs_diff={abs(a-b):.6f}"
            )
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
        print(f"  [{status}] {pair} {tenor}")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count - skip_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

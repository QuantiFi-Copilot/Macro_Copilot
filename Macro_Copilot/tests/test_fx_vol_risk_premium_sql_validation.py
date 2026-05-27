#!/usr/bin/env python3
"""
test_fx_vol_risk_premium_sql_validation.py
==========================================

SQL-validates ``get_fx_vol_risk_premium`` against an independent SQL +
pandas baseline. Recomputes:
  - implied vol from fx_vol substrate (latest non-null)
  - realized vol from fx_spot via numpy log returns + rolling std
  - VRP = implied - realized
  - 252-day z-score / observation count on the VRP series

PR16 admission gate for the Phase E3 vol risk premium primitive.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.vol._shared import tenor_trading_days  # noqa: E402
from fx_agent.vol.tools.vol_risk_premium import (  # noqa: E402
    FXVolRiskPremiumInput,
    get_fx_vol_risk_premium,
)


TOLERANCE_VOL_ABS = 0.01           # 0.01 vol points
TOLERANCE_Z_SCORE_ABS = 0.05


# (pair, tenor, realized_window_basis)
DEFAULT_CASES: List[Tuple[str, str, str]] = [
    ("EURUSD", "1M", "tenor_matched"),
    ("EURUSD", "3M", "tenor_matched"),
    ("EURUSD", "3M", "fixed_30d"),
    ("USDJPY", "1M", "tenor_matched"),
    ("USDMXN", "3M", "tenor_matched"),
    ("USDCNH", "1M", "tenor_matched"),
]


def sql_baseline(
    engine, *, pair: str, tenor: str, realized_window_basis: str,
    field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    realized_window_days = (
        tenor_trading_days(tenor) if realized_window_basis == "tenor_matched" else 30
    )
    spot_start = date.today() - timedelta(
        days=lookback_days + realized_window_days * 2 + 14
    )

    # Implied
    implied_sql = text(
        """
        SELECT d.trade_date, d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_vol'
          AND im.attributes ->> 'pair' = :pair
          AND im.tenor = :tenor
          AND d.field_name = :field_name
          AND d.trade_date >= :start_date
        ORDER BY d.trade_date ASC
        """
    )
    spot_sql = text(
        """
        SELECT d.trade_date, d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_spot'
          AND im.attributes ->> 'pair' = :pair
          AND d.field_name = :field_name
          AND d.trade_date >= :start_date
        ORDER BY d.trade_date ASC
        """
    )
    with engine.connect() as conn:
        ir = conn.execute(implied_sql, {
            "pair": pair, "tenor": tenor, "field_name": field_name,
            "start_date": spot_start.isoformat(),
        }).fetchall()
        sr = conn.execute(spot_sql, {
            "pair": pair, "field_name": field_name,
            "start_date": spot_start.isoformat(),
        }).fetchall()

    if not ir or not sr:
        return {"empty": True}

    imp_df = pd.DataFrame(ir, columns=["trade_date", "field_value"])
    imp_df["trade_date"] = pd.to_datetime(imp_df["trade_date"])
    imp_df["field_value"] = pd.to_numeric(imp_df["field_value"], errors="coerce")
    imp_df = imp_df.dropna(subset=["field_value"]).sort_values("trade_date")
    implied = pd.Series(imp_df["field_value"].values, index=imp_df["trade_date"])

    spot_df = pd.DataFrame(sr, columns=["trade_date", "field_value"])
    spot_df["trade_date"] = pd.to_datetime(spot_df["trade_date"])
    spot_df["field_value"] = pd.to_numeric(spot_df["field_value"], errors="coerce")
    spot_df = spot_df.dropna(subset=["field_value"])
    spot_df = spot_df.drop_duplicates(subset=["trade_date"], keep="last")
    spot_df = spot_df.set_index("trade_date").sort_index()
    spot_df = spot_df.ffill(limit=3)
    spot = spot_df["field_value"]
    log_ret = np.log(spot) - np.log(spot.shift(1))
    rolling_std = log_ret.rolling(window=realized_window_days, min_periods=realized_window_days).std(ddof=1)
    realized = rolling_std * math.sqrt(252) * 100.0

    joined = pd.concat({"implied": implied, "realized": realized}, axis=1, join="inner").dropna()
    cutoff = pd.Timestamp(date.today() - timedelta(days=lookback_days))
    joined = joined.loc[joined.index >= cutoff]
    if joined.empty:
        return {"empty": True}

    joined["vrp"] = joined["implied"] - joined["realized"]
    vrp = joined["vrp"]
    current = float(vrp.iloc[-1])
    trailing = vrp.tail(252)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=1)
    z = float((current - mean_) / std_) if (
        len(trailing) >= 60 and std_ and not pd.isna(std_)
    ) else None
    return {
        "current_implied_vol_pct": float(joined["implied"].iloc[-1]),
        "current_realized_vol_pct": float(joined["realized"].iloc[-1]),
        "current_vol_risk_premium_vol_pts": current,
        "z_score": z,
        "observation_count": int(len(vrp)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX VOL RISK PREMIUM SQL VALIDATION — {len(DEFAULT_CASES)} case(s)")
    print("=" * 72)
    fail_count = 0
    skip_count = 0
    for pair, tenor, basis in DEFAULT_CASES:
        try:
            tool_out = get_fx_vol_risk_premium(
                engine, FXVolRiskPremiumInput(
                    pair=pair, tenor=tenor, realized_window_basis=basis,
                    lookback_days=args.lookback_days, field_name=args.field,
                ),
            )
        except ValueError as exc:
            print(f"  [SKIP] {pair} {tenor} {basis} — {exc}")
            skip_count += 1
            continue
        tool = tool_out["current_metrics"]
        sql = sql_baseline(
            engine, pair=pair, tenor=tenor, realized_window_basis=basis,
            field_name=args.field, lookback_days=args.lookback_days,
        )
        if sql.get("empty"):
            print(f"  [SKIP] {pair} {tenor} {basis} — SQL empty")
            skip_count += 1
            continue

        mismatches: List[str] = []
        for key in (
            "current_implied_vol_pct",
            "current_realized_vol_pct",
            "current_vol_risk_premium_vol_pts",
        ):
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
        print(f"  [{status}] {pair} {tenor} {basis}")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count - skip_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

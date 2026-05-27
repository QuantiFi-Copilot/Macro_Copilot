#!/usr/bin/env python3
"""
test_fx_ndf_implied_carry_sql_validation.py
============================================

SQL-validates ``calculate_fx_ndf_implied_carry`` snapshot rows against
an independent SQL+pandas baseline that reproduces:

  - last common (NDF outright, underlying spot) trade_date
  - implied_carry_annualized_pct = (outright/spot - 1) * 252/tenor_days * 100
  - rolling 252-day z-score of the per-date implied-carry series

PR16 admission gate for the Phase D NDF carry primitive.
Usage:
    python -m tests.test_fx_ndf_implied_carry_sql_validation
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
from fx_agent.ndf._shared import SUPPORTED_NDF_CODES  # noqa: E402
from fx_agent.ndf.tools.ndf_implied_carry import (  # noqa: E402
    FXNDFImpliedCarryInput,
    calculate_fx_ndf_implied_carry,
)


TOLERANCE_SPOT_REL = 0.0001
TOLERANCE_OUTRIGHT_REL = 0.0001
TOLERANCE_CARRY_PCT_ABS = 0.05  # 0.05 vol-pct points
TOLERANCE_Z_SCORE_ABS = 0.005

TENOR_DAYS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}


def sql_baseline_one(
    engine, *, ndf_code: str, tenor: str, spot_pair: str,
    field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    sql = text(
        """
        WITH spot_hist AS (
            SELECT d.trade_date, d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND im.attributes ->> 'pair' = :spot_pair
              AND d.field_name = :field_name
              AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ),
        ndf_hist AS (
            SELECT d.trade_date, d.field_value::float AS outright
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_ndf'
              AND im.attributes ->> 'ndf_code' = :ndf_code
              AND im.tenor = :tenor
              AND d.field_name = :field_name
              AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        )
        SELECT n.trade_date, n.outright, s.spot
        FROM ndf_hist n
        INNER JOIN spot_hist s ON s.trade_date = n.trade_date
        ORDER BY n.trade_date
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "ndf_code": ndf_code, "spot_pair": spot_pair, "tenor": tenor,
            "field_name": field_name, "lookback_days": lookback_days,
        }).fetchall()

    if not rows:
        return {"empty": True}

    df = pd.DataFrame(rows, columns=["trade_date", "outright", "spot"])
    df["outright"] = pd.to_numeric(df["outright"], errors="coerce")
    df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
    df = df.dropna().reset_index(drop=True)

    tenor_days = TENOR_DAYS[tenor]
    df["carry"] = (df["outright"] / df["spot"] - 1.0) * (252 / tenor_days) * 100.0

    last = df.iloc[-1]
    z_window, z_min, z_ddof = 252, 60, 1
    trailing = df["carry"].tail(z_window)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=z_ddof)
    z = float((float(last["carry"]) - mean_) / std_) if (
        len(trailing) >= z_min and std_ and not pd.isna(std_)
    ) else None

    return {
        "spot": float(last["spot"]),
        "outright": float(last["outright"]),
        "implied_carry_annualized_pct": float(last["carry"]),
        "z_score": z,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenor", default="1M")
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()

    engine = get_db_engine()
    tool_out = calculate_fx_ndf_implied_carry(
        engine, FXNDFImpliedCarryInput(
            tenor=args.tenor, lookback_days=args.lookback_days,
            field_name=args.field,
        ),
    )

    print("=" * 72)
    print(f"FX NDF IMPLIED CARRY SQL VALIDATION — tenor={args.tenor}")
    print("=" * 72)
    fail_count = 0
    for row in tool_out["rows"]:
        code = row["ndf_code"]
        spot_pair = SUPPORTED_NDF_CODES[code]  # settlement spot
        sql = sql_baseline_one(
            engine, ndf_code=code, tenor=args.tenor,
            spot_pair=spot_pair, field_name=args.field,
            lookback_days=args.lookback_days,
        )
        if sql.get("empty"):
            print(f"  [SKIP] {code} — SQL baseline empty")
            continue

        mismatches: List[str] = []
        for f, tol_rel in (("spot", TOLERANCE_SPOT_REL),
                           ("outright", TOLERANCE_OUTRIGHT_REL)):
            a = row[f]
            b = sql[f]
            rel = abs(a - b) / max(abs(b), 1e-12)
            if rel > tol_rel:
                mismatches.append(f"{f}: tool={a} sql={b} rel_diff={rel:.6f}")

        a = row["implied_carry_annualized_pct"]
        b = sql["implied_carry_annualized_pct"]
        if abs(a - b) > TOLERANCE_CARRY_PCT_ABS:
            mismatches.append(
                f"implied_carry_annualized_pct: tool={a} sql={b} "
                f"abs_diff={abs(a-b):.6f} (tol {TOLERANCE_CARRY_PCT_ABS})"
            )

        a = row["carry_z_score"]
        b = sql["z_score"]
        if a is None and b is None:
            pass
        elif a is None or b is None:
            mismatches.append(f"carry_z_score: tool={a} sql={b}")
        elif abs(a - b) > TOLERANCE_Z_SCORE_ABS:
            mismatches.append(
                f"carry_z_score: tool={a} sql={b} abs_diff={abs(a-b):.6f}"
            )

        status = "PASS" if not mismatches else "FAIL"
        if mismatches:
            fail_count += 1
        print(f"  [{status}] {code}")
        for m in mismatches:
            print(f"           {m}")

    print("-" * 72)
    pass_count = len(tool_out["rows"]) - fail_count
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

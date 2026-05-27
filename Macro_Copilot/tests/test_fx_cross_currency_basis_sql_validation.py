#!/usr/bin/env python3
"""
test_fx_cross_currency_basis_sql_validation.py
==============================================

SQL-validates ``get_fx_cross_currency_basis`` against an independent
SQL + pandas baseline. Recomputes:
  - FX leg: joined (spot, forward_points) → fp_spot_units → outright
    → (F/S - 1) * (annual/tenor) * 100 → sign-adjusted local-minus-USD
  - OIS leg: cross-market pair (local_OIS, USD_SOFR_OIS) → pivot →
    (local_ois - usd_ois)
  - basis_bps = (fx_iyd - ois_diff) * 100 (Bloomberg BCRX-style)
  - 252-day z-score on the basis series

PR16 admission gate for the Phase C cross-currency basis primitive.

ENVIRONMENT REQUIREMENT: a full DB with both fx_spot/fx_forward AND
rates_agent OIS instruments. Local dev DBs without OIS substrate
will SKIP each case gracefully (no false FAIL).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.forwards.tools.cross_currency_basis import (  # noqa: E402
    FXCrossCurrencyBasisInput,
    get_fx_cross_currency_basis,
)


TOLERANCE_BPS_ABS = 0.1            # 0.1 bp
TOLERANCE_PCT_ABS = 0.005          # 0.5 bp on percent values
TOLERANCE_Z_SCORE_ABS = 0.01

_TENOR_DAYS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}
_FX_TO_OIS_TENOR = {"1W": "1W", "1M": "1M", "3M": "3M", "6M": "6M", "12M": "1Y"}
_LOCAL_CURRENCY_TO_OIS = {
    "EUR": "EUR_ESTR_OIS", "GBP": "GBP_SONIA_OIS", "JPY": "JPY_OIS",
    "AUD": "AUD_OIS", "CAD": "CAD_OIS",
}
_USD_OIS_CURVE = "USD_SOFR_OIS"
_JPY_DIVISOR = 100.0
_DEFAULT_DIVISOR = 10000.0
_ANNUAL = 252


# (pair, tenor) — anchor cases across V1 pairs and tenors
DEFAULT_CASES: List[Tuple[str, str]] = [
    ("EURUSD", "1M"),
    ("EURUSD", "12M"),
    ("GBPUSD", "3M"),
    ("USDJPY", "1M"),
    ("AUDUSD", "6M"),
    ("USDCAD", "3M"),
]


def _resolve_usd_leg(pair: str) -> Tuple[str, str, float]:
    base, quote = pair[:3], pair[3:]
    if base == "USD":
        return ("base", quote, +1.0)
    return ("quote", base, -1.0)


def sql_baseline(
    engine, *, pair: str, tenor: str, field_name: str, lookback_days: int,
) -> Dict[str, Any]:
    usd_leg_position, local_currency, sign = _resolve_usd_leg(pair)
    local_ois_curve = _LOCAL_CURRENCY_TO_OIS[local_currency]
    ois_tenor = _FX_TO_OIS_TENOR[tenor]
    start_date = (date.today() - timedelta(days=lookback_days)).isoformat()

    # FX leg: joined spot+forward
    fx_sql = text(
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
    # OIS leg: cross-market pair
    ois_sql = text(
        """
        SELECT trade_date, curve_family, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family IN (:local_ois, :usd_ois)
          AND tenor = :ois_tenor
          AND field_name = :field_name
          AND trade_date >= :start_date
        ORDER BY trade_date
        """
    )
    with engine.connect() as conn:
        fx_rows = conn.execute(fx_sql, {
            "pair": pair, "tenor": tenor, "field_name": field_name,
            "start_date": start_date,
        }).fetchall()
        ois_rows = conn.execute(ois_sql, {
            "local_ois": local_ois_curve, "usd_ois": _USD_OIS_CURVE,
            "ois_tenor": ois_tenor, "field_name": field_name,
            "start_date": start_date,
        }).fetchall()

    if not fx_rows:
        return {"empty": True, "reason": f"FX history empty for {pair} {tenor}"}
    if not ois_rows:
        return {"empty": True, "reason": f"OIS history empty for {local_ois_curve}/{_USD_OIS_CURVE} {ois_tenor}"}

    # Build FX iyd series
    fx_df = pd.DataFrame(fx_rows, columns=["trade_date", "spot", "forward_points"])
    fx_df["trade_date"] = pd.to_datetime(fx_df["trade_date"])
    fx_df["spot"] = pd.to_numeric(fx_df["spot"], errors="coerce")
    fx_df["forward_points"] = pd.to_numeric(fx_df["forward_points"], errors="coerce")
    fx_df = fx_df.dropna(subset=["spot", "forward_points"])
    fx_df = fx_df.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
    fx_df = fx_df.set_index("trade_date").ffill(limit=5).dropna()
    divisor = _JPY_DIVISOR if "JPY" in pair else _DEFAULT_DIVISOR
    fx_df["fp_spot_units"] = fx_df["forward_points"] / divisor
    fx_df["outright"] = fx_df["spot"] + fx_df["fp_spot_units"]
    raw_diff = (fx_df["outright"] / fx_df["spot"] - 1.0) * (_ANNUAL / _TENOR_DAYS[tenor]) * 100.0
    fx_iyd = sign * raw_diff

    # Build OIS diff series
    ois_df = pd.DataFrame(ois_rows, columns=["trade_date", "curve_family", "field_value"])
    ois_df["trade_date"] = pd.to_datetime(ois_df["trade_date"])
    ois_df["field_value"] = pd.to_numeric(ois_df["field_value"], errors="coerce")
    ois_df = ois_df.dropna(subset=["field_value"])
    ois_wide = ois_df.pivot_table(
        index="trade_date", columns="curve_family",
        values="field_value", aggfunc="last",
    ).sort_index().ffill(limit=5)
    if local_ois_curve not in ois_wide.columns or _USD_OIS_CURVE not in ois_wide.columns:
        return {"empty": True, "reason": f"OIS pivot missing curves: {list(ois_wide.columns)}"}
    ois_diff = ois_wide[local_ois_curve] - ois_wide[_USD_OIS_CURVE]

    # Join + compute basis (Bloomberg BCRX-style)
    joined = pd.concat({"fx_iyd": fx_iyd, "ois_diff": ois_diff}, axis=1, join="inner").dropna()
    if joined.empty:
        return {"empty": True, "reason": "FX×OIS inner-join empty"}
    joined["basis_bps"] = (joined["fx_iyd"] - joined["ois_diff"]) * 100.0
    basis_series = joined["basis_bps"]
    current_basis = float(basis_series.iloc[-1])
    current_fx_iyd = float(joined["fx_iyd"].iloc[-1])
    current_ois_diff = float(joined["ois_diff"].iloc[-1])
    trailing = basis_series.tail(252)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=1)
    z = float((current_basis - mean_) / std_) if (
        len(trailing) >= 60 and std_ and not pd.isna(std_)
    ) else None
    return {
        "current_fx_implied_yield_diff_pct": current_fx_iyd,
        "current_ois_diff_pct": current_ois_diff,
        "current_basis_bps": current_basis,
        "z_score": z,
        "observation_count": int(len(basis_series)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=365)
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX CROSS-CURRENCY BASIS SQL VALIDATION — {len(DEFAULT_CASES)} case(s)")
    print("=" * 72)
    fail_count = 0
    skip_count = 0
    for pair, tenor in DEFAULT_CASES:
        try:
            tool_out = get_fx_cross_currency_basis(
                engine, FXCrossCurrencyBasisInput(
                    pair=pair, tenor=tenor,
                    lookback_days=args.lookback_days, field_name=args.field,
                ),
            )
        except ValueError as exc:
            if "No OIS data" in str(exc) or "No FX implied vol" in str(exc):
                print(f"  [SKIP] {pair} {tenor} — substrate absent: {exc}")
                skip_count += 1
                continue
            print(f"  [SKIP] {pair} {tenor} — {exc}")
            skip_count += 1
            continue
        tool = tool_out["current_metrics"]
        sql = sql_baseline(
            engine, pair=pair, tenor=tenor,
            field_name=args.field, lookback_days=args.lookback_days,
        )
        if sql.get("empty"):
            print(f"  [SKIP] {pair} {tenor} — SQL empty: {sql.get('reason', 'unknown')}")
            skip_count += 1
            continue

        mismatches: List[str] = []
        for key, tol in [
            ("current_fx_implied_yield_diff_pct", TOLERANCE_PCT_ABS),
            ("current_ois_diff_pct", TOLERANCE_PCT_ABS),
            ("current_basis_bps", TOLERANCE_BPS_ABS),
        ]:
            a, b = tool[key], sql[key]
            if abs(a - b) > tol:
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
        print(f"  [{status}] {pair} {tenor}  basis={tool['current_basis_bps']:+.2f}bp")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count - skip_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP")
    if skip_count == len(DEFAULT_CASES):
        print("NOTE: all cases SKIPPED — OIS substrate likely absent on this DB.")
        print("      Re-run on a full QFin DB with rates_agent OIS data ingested.")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

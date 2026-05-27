#!/usr/bin/env python3
"""
test_fx_carry_basket_sql_validation.py
======================================

SQL-validates ``get_fx_carry_basket`` against an independent SQL +
pandas reproduction of the constituent-selection logic at the LATEST
rebalance.

Why constituent selection: the daily-return strategy index recompute
would essentially reimplement the whole tool, which doesn't add
validation value. The load-bearing logic that differs from
fx_carry / forward_curve is:
  - carry SIGNAL sign (= r_base - r_quote = -fx_carry.carry_annualized_pct)
  - signal_date = rebalance_date - signal_lag_days lookup
  - cross-sectional top/bottom-N selection at signal_date

This test independently reproduces the constituent set at the latest
rebalance date for each (scope, tenor, top_n, construction) combo
and verifies it matches the tool output.

PR16 admission gate for the Phase B+ carry-basket primitive.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.forwards.tools.carry_basket import (  # noqa: E402
    FXCarryBasketInput,
    get_fx_carry_basket,
)


_TENOR_DAYS = {"1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}
_JPY_DIVISOR = 100.0
_DEFAULT_DIVISOR = 10000.0
_ANNUALIZATION = 252
_REBALANCE_DAYS = 21
_SIGNAL_LAG = 1
_FFILL_LIMIT = 5

_SCOPE_TO_FAMILIES = {
    "G10": ["G10_FORWARDS"],
    "EM": ["EM_FORWARDS"],
    "ALL": ["G10_FORWARDS", "EM_FORWARDS"],
}


# (market_scope, tenor, top_n, basket_construction)
DEFAULT_CASES: List[Tuple[str, str, int, str]] = [
    ("G10", "1M", 3, "long_short_top_n"),
    ("G10", "1M", 3, "long_only_top_n"),
    ("EM", "3M", 3, "long_short_top_n"),
]


def _reproduce_carry_wide(
    engine, *, market_scope: str, tenor: str,
    field_name: str, lookback_days: int,
) -> pd.DataFrame:
    """Re-fetch the cross-sectional history and produce the wide
    carry-signal matrix (dates × pairs). Signal = -(F/S - 1) * annual
    / tenor * 100 (canonical long-pair carry)."""
    sql = text(
        """
        WITH spot_history AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                d.trade_date,
                d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND d.field_name = :field_name
              AND d.trade_date >= :start_date
        ),
        fwd_history AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                d.trade_date,
                d.field_value::float AS forward_points
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_forward'
              AND im.attributes ->> 'fx_family' = ANY(:fx_families)
              AND im.tenor = :tenor
              AND d.field_name = :field_name
              AND d.trade_date >= :start_date
        )
        SELECT s.pair, s.trade_date, s.spot, f.forward_points
        FROM spot_history s
        INNER JOIN fwd_history f
            ON s.pair = f.pair AND s.trade_date = f.trade_date
        ORDER BY s.pair, s.trade_date
        """
    )
    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=lookback_days)
    ).date()
    with engine.connect() as conn:
        rows = conn.execute(sql, {
            "tenor": tenor,
            "field_name": field_name,
            "start_date": start_date,
            "fx_families": _SCOPE_TO_FAMILIES[market_scope],
        }).fetchall()
    df = pd.DataFrame(rows, columns=["pair", "trade_date", "spot", "forward_points"])
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
    df["forward_points"] = pd.to_numeric(df["forward_points"], errors="coerce")
    df = df.dropna(subset=["spot", "forward_points"])
    df["fp_spot_units"] = df.apply(
        lambda r: r["forward_points"] / (_JPY_DIVISOR if "JPY" in r["pair"] else _DEFAULT_DIVISOR),
        axis=1,
    )
    # Canonical long-pair carry signal = -(r_quote - r_base)
    df["carry_signal_pct"] = -(
        (df["fp_spot_units"] / df["spot"])
        * (_ANNUALIZATION / _TENOR_DAYS[tenor])
        * 100.0
    )
    wide = (
        df.pivot_table(
            index="trade_date", columns="pair",
            values="carry_signal_pct", aggfunc="last",
        )
        .sort_index()
        .ffill(limit=_FFILL_LIMIT)
    )
    return wide


def _select_constituents_at_latest_rebalance(
    carry_wide: pd.DataFrame, *, top_n: int, basket_construction: str,
) -> Tuple[Set[str], Set[str]]:
    """Replay the rebalance schedule and return (long_set, short_set)
    at the LAST rebalance period."""
    trading_dates = list(carry_wide.index)
    n = len(trading_dates)
    cursor = _SIGNAL_LAG
    last_long: Set[str] = set()
    last_short: Set[str] = set()
    while cursor < n:
        signal_idx = cursor - _SIGNAL_LAG
        if signal_idx < 0:
            cursor += _REBALANCE_DAYS
            continue
        signal_date = trading_dates[signal_idx]
        if signal_date not in carry_wide.index:
            cursor += _REBALANCE_DAYS
            continue
        signal_row = carry_wide.loc[signal_date].dropna()
        if basket_construction == "long_short_top_n":
            if len(signal_row) < 2 * top_n:
                cursor += _REBALANCE_DAYS
                continue
            sorted_pairs = signal_row.sort_values(ascending=False)
            last_long = set(sorted_pairs.head(top_n).index.tolist())
            last_short = set(sorted_pairs.tail(top_n).index.tolist())
        else:  # long_only_top_n
            if len(signal_row) < top_n:
                cursor += _REBALANCE_DAYS
                continue
            sorted_pairs = signal_row.sort_values(ascending=False)
            last_long = set(sorted_pairs.head(top_n).index.tolist())
            last_short = set()
        cursor += _REBALANCE_DAYS
    return last_long, last_short


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", default="PX_LAST")
    parser.add_argument("--lookback-days", type=int, default=730)
    args = parser.parse_args()

    engine = get_db_engine()
    print("=" * 72)
    print(f"FX CARRY BASKET SQL VALIDATION — {len(DEFAULT_CASES)} case(s)")
    print("=" * 72)
    fail_count = 0
    skip_count = 0
    for scope, tenor, top_n, construction in DEFAULT_CASES:
        try:
            tool_out = get_fx_carry_basket(
                engine, FXCarryBasketInput(
                    market_scope=scope, tenor=tenor, top_n=top_n,
                    basket_construction=construction,
                    lookback_days=args.lookback_days, field_name=args.field,
                ),
            )
        except ValueError as exc:
            print(f"  [SKIP] {scope} {tenor} top_n={top_n} {construction} — {exc}")
            skip_count += 1
            continue
        tool_constituents = tool_out["constituent_pairs"]
        tool_long = {c.split(" ")[0] for c in tool_constituents if "(long)" in c}
        tool_short = {c.split(" ")[0] for c in tool_constituents if "(short)" in c}

        # SQL+pandas reproduction
        carry_wide = _reproduce_carry_wide(
            engine, market_scope=scope, tenor=tenor,
            field_name=args.field, lookback_days=args.lookback_days,
        )
        sql_long, sql_short = _select_constituents_at_latest_rebalance(
            carry_wide, top_n=top_n, basket_construction=construction,
        )

        mismatches: List[str] = []
        if tool_long != sql_long:
            mismatches.append(
                f"long mismatch: tool={sorted(tool_long)} sql={sorted(sql_long)}"
            )
        if tool_short != sql_short:
            mismatches.append(
                f"short mismatch: tool={sorted(tool_short)} sql={sorted(sql_short)}"
            )

        status = "PASS" if not mismatches else "FAIL"
        if mismatches:
            fail_count += 1
        print(f"  [{status}] {scope} {tenor} top_n={top_n} {construction}")
        for m in mismatches:
            print(f"           {m}")

    pass_count = len(DEFAULT_CASES) - fail_count - skip_count
    print("-" * 72)
    print(f"SQL validation summary: {pass_count} PASS / {fail_count} FAIL / {skip_count} SKIP")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

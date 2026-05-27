#!/usr/bin/env python3
"""Shared capture helper for Phase B+ carry-extension parity fixtures.

For each tool we capture ONE canonical case (per "minimales" discipline):
  - implied_yield_differential: EURUSD 1M
  - carry_basket:               G10 1M top_n=3 long_short_top_n

Each fixture stores:
  - input params
  - raw_queries (per substrate): the rows the SQL fetch returned
  - expected_output (full tool dict)
  - SHA-256 hash of raw rows for tamper detection

The parity test (tests/test_fx_carry_extensions_parity.py) monkey-
patches the DB-touching call sites, replays captured rows, runs the
tool, and asserts the output matches expected_output.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.forwards.tools.carry_basket import (  # noqa: E402
    FXCarryBasketInput,
    get_fx_carry_basket,
)
from fx_agent.forwards.tools.implied_yield_differential import (  # noqa: E402
    FXImpliedYieldDifferentialInput,
    get_fx_implied_yield_differential,
)


def _sha256(rows: List[Dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _serialise_rows(df: pd.DataFrame, columns: List[str]) -> List[Dict[str, Any]]:
    out = []
    for _, row in df.iterrows():
        rec: Dict[str, Any] = {"trade_date": pd.to_datetime(row["trade_date"]).strftime("%Y-%m-%d")}
        for c in columns:
            v = row.get(c)
            rec[c] = float(v) if v is not None and not pd.isna(v) else None
        out.append(rec)
    return out


def _iyd_history_sql() -> Any:
    return text(
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


def _basket_history_sql() -> Any:
    return text(
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


def capture_implied_yield_differential(engine) -> Dict[str, Any]:
    params = {
        "pair": "EURUSD", "tenor": "1M",
        "lookback_days": 365, "field_name": "PX_LAST",
    }
    start_date = date.today() - timedelta(days=params["lookback_days"])
    with engine.connect() as conn:
        df = pd.read_sql(_iyd_history_sql(), conn, params={
            "pair": params["pair"], "tenor": params["tenor"],
            "field_name": params["field_name"],
            "start_date": start_date.isoformat(),
        })
    rows = _serialise_rows(df, ["spot", "forward_points"])
    tool_out = get_fx_implied_yield_differential(
        engine, FXImpliedYieldDifferentialInput(**params)
    )
    return {
        "fixture_name": "eurusd_1m",
        "tool_module": "fx_agent.forwards.tools.implied_yield_differential",
        "tool_function": "get_fx_implied_yield_differential",
        "capture": {
            "captured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "capture_method": "live_db_v1",
            "database_name": "macrodata",
            "start_date": start_date.isoformat(),
            "raw_rows_count": len(rows),
            "raw_rows_sha256": _sha256(rows),
        },
        "input": {
            "params": params,
            "queries": [
                {"substrate": "joined_spot_forward", "pair": params["pair"], "rows": rows},
            ],
        },
        "expected_output": tool_out,
    }


def capture_carry_basket(engine) -> Dict[str, Any]:
    params = {
        "market_scope": "G10", "tenor": "1M", "top_n": 3,
        "basket_construction": "long_short_top_n",
        "lookback_days": 730, "field_name": "PX_LAST",
    }
    fx_families = ["G10_FORWARDS"]  # G10 scope
    start_date = date.today() - timedelta(days=params["lookback_days"])
    with engine.connect() as conn:
        df = pd.read_sql(_basket_history_sql(), conn, params={
            "tenor": params["tenor"],
            "field_name": params["field_name"],
            "start_date": start_date.isoformat(),
            "fx_families": fx_families,
        })
    # Per-pair row serialisation
    pair_rows: Dict[str, List[Dict[str, Any]]] = {}
    for pair, group in df.groupby("pair"):
        pair_rows[pair] = _serialise_rows(group, ["spot", "forward_points"])
    tool_out = get_fx_carry_basket(engine, FXCarryBasketInput(**params))
    all_rows = [r for rows in pair_rows.values() for r in rows]
    queries = [
        {"substrate": "joined_spot_forward", "pair": pair, "rows": rows}
        for pair, rows in pair_rows.items()
    ]
    return {
        "fixture_name": "g10_1m_top3_long_short",
        "tool_module": "fx_agent.forwards.tools.carry_basket",
        "tool_function": "get_fx_carry_basket",
        "capture": {
            "captured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "capture_method": "live_db_v1",
            "database_name": "macrodata",
            "start_date": start_date.isoformat(),
            "raw_rows_count": len(all_rows),
            "raw_rows_sha256": _sha256(all_rows),
        },
        "input": {
            "params": params,
            "fx_families": fx_families,
            "queries": queries,
        },
        "expected_output": tool_out,
    }


def main() -> int:
    engine = get_db_engine()
    fx_fixtures_dir = Path(__file__).resolve().parent

    iyd_path = fx_fixtures_dir / "implied_yield_differential_v1" / "eurusd_1m.json"
    cb_path = fx_fixtures_dir / "carry_basket_v1" / "g10_1m_top3_long_short.json"

    print("Capturing implied_yield_differential...")
    iyd = capture_implied_yield_differential(engine)
    iyd_path.write_text(json.dumps(iyd, indent=2, default=str))
    print(f"  -> {iyd_path} ({iyd['capture']['raw_rows_count']} rows)")

    print("Capturing carry_basket...")
    cb = capture_carry_basket(engine)
    cb_path.write_text(json.dumps(cb, indent=2, default=str))
    print(f"  -> {cb_path} ({cb['capture']['raw_rows_count']} rows across {len(cb['input']['queries'])} pairs)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

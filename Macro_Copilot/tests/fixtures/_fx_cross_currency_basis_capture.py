#!/usr/bin/env python3
"""Capture helper for Phase C cross_currency_basis parity fixture.

ONE canonical case (minimales per Codex): EURUSD 1M.

Captures BOTH legs (FX joined spot+forward + OIS cross-market pair)
so the parity test can replay both fetches independently. SHA-256
tamper detection.
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
from fx_agent.forwards.tools.cross_currency_basis import (  # noqa: E402
    FXCrossCurrencyBasisInput,
    get_fx_cross_currency_basis,
)


def _sha256(rows: List[Dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _serialise_fx_rows(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out = []
    for _, row in df.iterrows():
        out.append({
            "trade_date": pd.to_datetime(row["trade_date"]).strftime("%Y-%m-%d"),
            "spot": float(row["spot"]) if pd.notna(row["spot"]) else None,
            "forward_points": float(row["forward_points"]) if pd.notna(row["forward_points"]) else None,
        })
    return out


def _serialise_ois_rows(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out = []
    for _, row in df.iterrows():
        out.append({
            "trade_date": pd.to_datetime(row["trade_date"]).strftime("%Y-%m-%d"),
            "curve_family": str(row["curve_family"]),
            "field_value": float(row["field_value"]) if pd.notna(row["field_value"]) else None,
        })
    return out


def capture_eurusd_1m(engine) -> Dict[str, Any]:
    params = {
        "pair": "EURUSD", "tenor": "1M",
        "lookback_days": 365, "field_name": "PX_LAST",
    }
    start_date = date.today() - timedelta(days=params["lookback_days"])

    # FX leg: joined spot + forward
    fx_sql = text("""
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
    """)

    # OIS leg: cross-market pair (EUR_ESTR_OIS, USD_SOFR_OIS) at 1M
    ois_sql = text("""
        SELECT trade_date, curve_family, field_value
        FROM macro_data.v_market_data_daily_enriched
        WHERE curve_family IN ('EUR_ESTR_OIS', 'USD_SOFR_OIS')
          AND tenor = '1M'
          AND field_name = 'PX_LAST'
          AND trade_date >= :start_date
        ORDER BY trade_date
    """)
    with engine.connect() as conn:
        fx_df = pd.read_sql(fx_sql, conn, params={
            "pair": params["pair"], "tenor": params["tenor"],
            "field_name": params["field_name"],
            "start_date": start_date.isoformat(),
        })
        ois_df = pd.read_sql(ois_sql, conn, params={"start_date": start_date.isoformat()})

    fx_rows = _serialise_fx_rows(fx_df)
    ois_rows = _serialise_ois_rows(ois_df)
    print(f"  Captured {len(fx_rows)} FX rows, {len(ois_rows)} OIS rows")

    # Run the tool to capture expected output
    tool_out = get_fx_cross_currency_basis(
        engine, FXCrossCurrencyBasisInput(**params)
    )
    print(f"  Tool current_basis_bps = {tool_out['current_metrics']['current_basis_bps']}")

    all_rows = fx_rows + ois_rows
    return {
        "fixture_name": "eurusd_1m",
        "tool_module": "fx_agent.forwards.tools.cross_currency_basis",
        "tool_function": "get_fx_cross_currency_basis",
        "capture": {
            "captured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "capture_method": "live_db_v1_with_sreeram_ois_parquet",
            "database_name": "macrodata",
            "start_date": start_date.isoformat(),
            "raw_rows_count": len(all_rows),
            "raw_rows_sha256": _sha256(all_rows),
        },
        "input": {
            "params": params,
            "queries": [
                {"substrate": "fx_joined_spot_forward", "pair": params["pair"], "rows": fx_rows},
                {"substrate": "ois_cross_market_pair",
                 "curves": ["EUR_ESTR_OIS", "USD_SOFR_OIS"],
                 "tenor": "1M", "rows": ois_rows},
            ],
        },
        "expected_output": tool_out,
    }


def main() -> int:
    engine = get_db_engine()
    out_path = Path(__file__).resolve().parent / "cross_currency_basis_v1" / "eurusd_1m.json"
    print(f"Capturing cross_currency_basis EURUSD 1M parity fixture...")
    fixture = capture_eurusd_1m(engine)
    out_path.write_text(json.dumps(fixture, indent=2, default=str))
    print(f"  -> {out_path} ({fixture['capture']['raw_rows_count']} rows total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

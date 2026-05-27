#!/usr/bin/env python3
"""Shared capture helper for Phase E2 smile parity fixtures.

For each tool we capture ONE canonical case (per Codex 2026-05-27
"parity fixtures minimales per compute path"):
  - risk_reversal: EURUSD 25R 1M
  - butterfly:     EURUSD 25B 1M
  - vol_smile:     EURUSD 1M (5 series: ATM + 25R + 25B + 10R + 10B)

Each fixture stores:
  - input params
  - raw_rows (the dataframe rows the SQL fetch returned)
  - expected_output (full tool dict)
  - SHA-256 hash of raw_rows for tamper detection

The parity test (tests/test_fx_smile_parity.py) monkey-patches
``pd.read_sql`` to replay the captured rows, runs the tool, and
asserts the output matches expected_output within float tolerance.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.database import get_db_engine  # noqa: E402
from fx_agent.vol.tools.butterfly import (  # noqa: E402
    FXButterflyInput,
    get_fx_butterfly,
)
from fx_agent.vol.tools.risk_reversal import (  # noqa: E402
    FXRiskReversalInput,
    get_fx_risk_reversal,
)
from fx_agent.vol.tools.vol_smile import (  # noqa: E402
    FXVolSmileInput,
    get_fx_vol_smile,
)


def _atm_sql() -> Any:
    return text(
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


def _smile_sql() -> Any:
    return text(
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


def _sha256(rows: List[Dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _serialise_rows(df: pd.DataFrame) -> List[Dict[str, Any]]:
    out = []
    for _, row in df.iterrows():
        out.append({
            "trade_date": pd.to_datetime(row["trade_date"]).strftime("%Y-%m-%d"),
            "field_value": float(row["field_value"]) if pd.notna(row["field_value"]) else None,
        })
    return out


def capture_risk_reversal(engine) -> Dict[str, Any]:
    params = {"pair": "EURUSD", "delta_anchor": 25, "tenor": "1M",
              "lookback_days": 365, "field_name": "PX_LAST"}
    with engine.connect() as conn:
        df = pd.read_sql(_smile_sql(), conn, params={
            "pair": "EURUSD", "smile_point": "25R", "tenor": "1M",
            "field_name": "PX_LAST", "lookback_days": 365,
        })
    raw_rows = _serialise_rows(df)
    tool_out = get_fx_risk_reversal(engine, FXRiskReversalInput(**params))
    return {
        "fixture_name": "eurusd_25r_1m",
        "tool_module": "fx_agent.vol.tools.risk_reversal",
        "tool_function": "get_fx_risk_reversal",
        "capture": {
            "captured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "capture_method": "live_db_v1",
            "database_name": "macrodata",
            "raw_rows_count": len(raw_rows),
            "raw_rows_sha256": _sha256(raw_rows),
        },
        "input": {
            "params": params,
            "queries": [
                {"substrate": "fx_vol_smile", "smile_point": "25R", "rows": raw_rows},
            ],
        },
        "expected_output": tool_out,
    }


def capture_butterfly(engine) -> Dict[str, Any]:
    params = {"pair": "EURUSD", "delta_anchor": 25, "tenor": "1M",
              "lookback_days": 365, "field_name": "PX_LAST"}
    with engine.connect() as conn:
        df = pd.read_sql(_smile_sql(), conn, params={
            "pair": "EURUSD", "smile_point": "25B", "tenor": "1M",
            "field_name": "PX_LAST", "lookback_days": 365,
        })
    raw_rows = _serialise_rows(df)
    tool_out = get_fx_butterfly(engine, FXButterflyInput(**params))
    return {
        "fixture_name": "eurusd_25b_1m",
        "tool_module": "fx_agent.vol.tools.butterfly",
        "tool_function": "get_fx_butterfly",
        "capture": {
            "captured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "capture_method": "live_db_v1",
            "database_name": "macrodata",
            "raw_rows_count": len(raw_rows),
            "raw_rows_sha256": _sha256(raw_rows),
        },
        "input": {
            "params": params,
            "queries": [
                {"substrate": "fx_vol_smile", "smile_point": "25B", "rows": raw_rows},
            ],
        },
        "expected_output": tool_out,
    }


def capture_vol_smile(engine) -> Dict[str, Any]:
    params = {"pair": "EURUSD", "tenor": "1M",
              "lookback_days": 365, "field_name": "PX_LAST"}
    queries: List[Dict[str, Any]] = []
    with engine.connect() as conn:
        # ATM
        atm_df = pd.read_sql(_atm_sql(), conn, params={
            "pair": "EURUSD", "tenor": "1M",
            "field_name": "PX_LAST", "lookback_days": 365,
        })
        atm_rows = _serialise_rows(atm_df)
        queries.append({"substrate": "fx_vol", "smile_point": "ATM", "rows": atm_rows})
        # 4 smile points
        for sp in ("25R", "25B", "10R", "10B"):
            df = pd.read_sql(_smile_sql(), conn, params={
                "pair": "EURUSD", "smile_point": sp, "tenor": "1M",
                "field_name": "PX_LAST", "lookback_days": 365,
            })
            queries.append({
                "substrate": "fx_vol_smile",
                "smile_point": sp,
                "rows": _serialise_rows(df),
            })

    tool_out = get_fx_vol_smile(engine, FXVolSmileInput(**params))
    all_rows = [r for q in queries for r in q["rows"]]
    return {
        "fixture_name": "eurusd_1m_5pt_smile",
        "tool_module": "fx_agent.vol.tools.vol_smile",
        "tool_function": "get_fx_vol_smile",
        "capture": {
            "captured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "capture_method": "live_db_v1",
            "database_name": "macrodata",
            "raw_rows_count": len(all_rows),
            "raw_rows_sha256": _sha256(all_rows),
        },
        "input": {
            "params": params,
            "queries": queries,
        },
        "expected_output": tool_out,
    }


def main() -> int:
    engine = get_db_engine()
    fx_fixtures_dir = Path(__file__).resolve().parent

    rr_path = fx_fixtures_dir / "risk_reversal_v1" / "eurusd_25r_1m.json"
    bf_path = fx_fixtures_dir / "butterfly_v1" / "eurusd_25b_1m.json"
    sm_path = fx_fixtures_dir / "vol_smile_v1" / "eurusd_1m_5pt_smile.json"

    print("Capturing risk_reversal...")
    rr = capture_risk_reversal(engine)
    rr_path.write_text(json.dumps(rr, indent=2, default=str))
    print(f"  -> {rr_path} ({rr['capture']['raw_rows_count']} rows)")

    print("Capturing butterfly...")
    bf = capture_butterfly(engine)
    bf_path.write_text(json.dumps(bf, indent=2, default=str))
    print(f"  -> {bf_path} ({bf['capture']['raw_rows_count']} rows)")

    print("Capturing vol_smile...")
    sm = capture_vol_smile(engine)
    sm_path.write_text(json.dumps(sm, indent=2, default=str))
    print(f"  -> {sm_path} ({sm['capture']['raw_rows_count']} rows across 5 series)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

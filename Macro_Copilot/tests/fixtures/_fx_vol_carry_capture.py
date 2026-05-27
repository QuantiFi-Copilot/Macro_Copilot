#!/usr/bin/env python3
"""Shared capture helper for Phase E3 vol-carry parity fixtures.

For each tool we capture ONE canonical case (per "minimales" discipline):
  - vol_risk_premium:     EURUSD 1M tenor_matched
  - vol_calendar_spread:  EURUSD 1M/3M long_minus_short

Each fixture stores:
  - input params
  - raw_queries (per substrate query: the captured rows the SQL fetch
    returned, indexed so the parity test can replay them)
  - expected_output (full tool dict)
  - SHA-256 hash of raw rows for tamper detection

The parity test (tests/test_fx_vol_carry_parity.py) monkey-patches
``pd.read_sql`` and ``fetch_fx_spot_series`` to replay the captured
rows, runs the tool, and asserts the output matches expected_output
within float tolerance.
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
from fx_agent.vol._shared import tenor_trading_days  # noqa: E402
from fx_agent.vol.tools.vol_calendar_spread import (  # noqa: E402
    FXVolCalendarSpreadInput,
    get_fx_vol_calendar_spread,
)
from fx_agent.vol.tools.vol_risk_premium import (  # noqa: E402
    FXVolRiskPremiumInput,
    get_fx_vol_risk_premium,
)
from shared.analytics.fx_fetch import fetch_fx_spot_series  # noqa: E402


def _sha256(rows: List[Dict[str, Any]]) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _serialise_rows(df: pd.DataFrame, *, date_col: str = "trade_date") -> List[Dict[str, Any]]:
    out = []
    for _, row in df.iterrows():
        out.append({
            "trade_date": pd.to_datetime(row[date_col]).strftime("%Y-%m-%d"),
            "field_value": float(row["field_value"]) if pd.notna(row["field_value"]) else None,
        })
    return out


def _implied_sql() -> Any:
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
          AND d.trade_date >= :start_date
        ORDER BY d.trade_date ASC
        """
    )


def capture_vol_risk_premium(engine) -> Dict[str, Any]:
    params = {
        "pair": "EURUSD", "tenor": "1M",
        "realized_window_basis": "tenor_matched",
        "lookback_days": 365, "field_name": "PX_LAST",
    }
    realized_window_days = tenor_trading_days(params["tenor"])
    spot_start = date.today() - timedelta(
        days=params["lookback_days"] + realized_window_days * 2 + 14
    )

    with engine.connect() as conn:
        implied_df = pd.read_sql(_implied_sql(), conn, params={
            "pair": params["pair"], "tenor": params["tenor"],
            "field_name": params["field_name"],
            "start_date": spot_start.isoformat(),
        })
    implied_rows = _serialise_rows(implied_df)

    spot_df = fetch_fx_spot_series(
        engine=engine, pair=params["pair"],
        start_date=spot_start, field_name=params["field_name"],
    )
    spot_rows = _serialise_rows(spot_df)

    tool_out = get_fx_vol_risk_premium(
        engine, FXVolRiskPremiumInput(**params)
    )
    all_rows = implied_rows + spot_rows
    return {
        "fixture_name": "eurusd_1m_tenor_matched",
        "tool_module": "fx_agent.vol.tools.vol_risk_premium",
        "tool_function": "get_fx_vol_risk_premium",
        "capture": {
            "captured_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "capture_method": "live_db_v1",
            "database_name": "macrodata",
            "spot_start": spot_start.isoformat(),
            "raw_rows_count": len(all_rows),
            "raw_rows_sha256": _sha256(all_rows),
        },
        "input": {
            "params": params,
            "queries": [
                {"substrate": "fx_vol", "tenor": params["tenor"], "rows": implied_rows},
                {"substrate": "fx_spot", "rows": spot_rows},
            ],
        },
        "expected_output": tool_out,
    }


def capture_vol_calendar_spread(engine) -> Dict[str, Any]:
    params = {
        "pair": "EURUSD", "short_tenor": "1M", "long_tenor": "3M",
        "spread_direction": "long_minus_short",
        "lookback_days": 365, "field_name": "PX_LAST",
    }
    # Calendar-spread compute uses CURRENT_DATE - lookback_days in SQL,
    # so capture rows for that window per leg.
    cutoff_start = date.today() - timedelta(days=params["lookback_days"])
    queries = []
    with engine.connect() as conn:
        for tenor_key, tenor_val in [("short", params["short_tenor"]), ("long", params["long_tenor"])]:
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
            df = pd.read_sql(sql, conn, params={
                "pair": params["pair"], "tenor": tenor_val,
                "field_name": params["field_name"],
                "lookback_days": params["lookback_days"],
            })
            queries.append({
                "substrate": "fx_vol",
                "tenor": tenor_val,
                "tenor_role": tenor_key,
                "rows": _serialise_rows(df),
            })

    tool_out = get_fx_vol_calendar_spread(
        engine, FXVolCalendarSpreadInput(**params)
    )
    all_rows = [r for q in queries for r in q["rows"]]
    return {
        "fixture_name": "eurusd_1m_3m_long_minus_short",
        "tool_module": "fx_agent.vol.tools.vol_calendar_spread",
        "tool_function": "get_fx_vol_calendar_spread",
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

    vrp_path = fx_fixtures_dir / "vol_risk_premium_v1" / "eurusd_1m_tenor_matched.json"
    cal_path = fx_fixtures_dir / "vol_calendar_spread_v1" / "eurusd_1m_3m_long_minus_short.json"

    print("Capturing vol_risk_premium...")
    vrp = capture_vol_risk_premium(engine)
    vrp_path.write_text(json.dumps(vrp, indent=2, default=str))
    print(f"  -> {vrp_path} ({vrp['capture']['raw_rows_count']} rows)")

    print("Capturing vol_calendar_spread...")
    cal = capture_vol_calendar_spread(engine)
    cal_path.write_text(json.dumps(cal, indent=2, default=str))
    print(f"  -> {cal_path} ({cal['capture']['raw_rows_count']} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())

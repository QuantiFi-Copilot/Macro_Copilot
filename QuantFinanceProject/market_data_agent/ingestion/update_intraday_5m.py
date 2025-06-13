# market_data_agent/ingestion/update_intraday_5m.py
"""
Rolling 14-day 5-minute updater (IST-robust)

• Reads last stored bar per symbol.
• If the DB already has the latest bar, it skips the symbol.
• Otherwise it pulls just the missing window (min(last+5 min, now)).
• Inserts with upsert and silently ignores duplicates.
• All times are handled in Asia/Kolkata and written *naïve* (no tzinfo).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
from kiteconnect import KiteConnect
from sqlalchemy import text

from market_data_agent.config.universe import SYMBOLS
from market_data_agent.ingestion.kite_client import KiteDataClient
from market_data_agent.storage.database import (
    insert_intraday_5m,
    engine,
    get_intraday_5m,
)

# ──────────────────────────────────────────────────────────────────────────────
IST          = ZoneInfo("Asia/Kolkata")
API_KEY      = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")     # refreshed elsewhere
WINDOW_DAYS  = 14
# ──────────────────────────────────────────────────────────────────────────────


def _ist_now_floor5() -> datetime:
    """
    Current time in IST, floored to the previous 5-minute mark and tz-aware.
    """
    now = datetime.now(IST).replace(second=0, microsecond=0)
    return now - timedelta(minutes=now.minute % 5)


def _fetch_and_upsert(symbol: str, since: datetime, until: datetime):
    """
    Fetch missing 5-minute bars and upsert them; both `since` and `until`
    must be tz-aware Asia/Kolkata datetimes.
    """
    client = KiteDataClient()

    # MODIFIED SECTION: Pass datetime objects directly to the client
    raw = client.fetch_ohlcv(
        symbol,
        since,
        until,
        interval="5minute",
    )

    rows = raw.get(symbol, [])
    if not rows:
        print(f"⚠ {symbol}: API returned no 5-min bars")
        return 0

    df = pd.DataFrame(rows)
    # Ensure tz-aware → tz-naïve IST for storage
    df["date"] = (
        pd.to_datetime(df["date"])
        .dt.tz_convert(IST)      # convert if it came in UTC
        .dt.tz_localize(None)    # strip tzinfo before writing to DB
    )

    insert_intraday_5m(symbol, df)
    print(
        f"↳ {symbol}: added {len(df):>3} bars "
        f"({df['date'].iloc[0]} → {df['date'].iloc[-1]})"
    )
    return len(df)


def update_intraday_5m():
    """
    Public entry-point for Prefect. Attempts to keep a rolling 14 days of
    5-minute data for every symbol in `SYMBOLS`.
    """
    cutoff = _ist_now_floor5() - timedelta(days=WINDOW_DAYS)
    cutoff_str = cutoff.replace(tzinfo=None).isoformat()  # Convert to naive IST string

    # prune anything older than 14 days (cheap in TimescaleDB)
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DELETE FROM market_data.intraday_5min_ohlcv
                WHERE time < :cutoff
                """
            ),
            {"cutoff": cutoff.replace(tzinfo=None)},  # stored tz-naïve
        )

    total_new = 0
    for symbol in SYMBOLS:
        try:
            # FIX: Pass the required since_ts parameter
            df_existing = get_intraday_5m(symbol, cutoff_str)

            if df_existing.empty:
                need_from = cutoff
            else:
                last_ts = df_existing["date"].max()
                # The column is tz-naïve IST; re-attach tzinfo for maths
                last_ts = last_ts.replace(tzinfo=IST)
                need_from = last_ts + timedelta(minutes=5)

            need_to = _ist_now_floor5()

            if need_from >= need_to:
                print(f"✓ {symbol}: up-to-date")
                continue

            total_new += _fetch_and_upsert(symbol, need_from, need_to)

        except Exception as exc:  # noqa: BLE001
            print(f"‼ {symbol}: {exc!s}")

    print(f"✅ 5-minute updater finished – {total_new} bars added in total.")


# Allow manual execution:  python -m market_data_agent.ingestion.update_intraday_5m
if __name__ == "__main__":
    update_intraday_5m()
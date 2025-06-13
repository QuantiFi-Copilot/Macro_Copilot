# market_data_agent/ingestion/update_daily.py
"""
Daily updater (robust version)

• Uses BOOTSTRAP_START_DATE from .env (or 5 years ago) when back-filling a
  newly-added symbol.
• Skips API call entirely if the current day's bar is already present.
• Fetches in ≤ 730-day chunks so Zerodha’s 2 000-day limit is never hit.
"""

from __future__ import annotations
import os
from datetime import date, timedelta
import pandas as pd

from market_data_agent.config.universe import SYMBOLS
from market_data_agent.ingestion.kite_client import KiteDataClient

from market_data_agent.storage.database import (
    get_daily,
    insert_daily,
)

from market_data_agent.utils.time import localize_df

TODAY = date.today()
START_DATE = date.fromisoformat(
    os.getenv("BOOTSTRAP_START_DATE", (TODAY - timedelta(days=365 * 5)).isoformat())
)
CHUNK = timedelta(days=730)  # safe (< 2 000-day limit)

client = KiteDataClient()    # one session for all symbols


def fetch_chunked(symbol: str, start: date, end: date) -> pd.DataFrame:
    frames = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + CHUNK - timedelta(days=1), end)
        rows = client.fetch_ohlcv(
            symbol,
            cursor.isoformat(),
            chunk_end.isoformat(),
            interval="day",
        )
        if rows and symbol in rows and rows[symbol]:
            frames.append(pd.DataFrame(rows[symbol]))
        cursor = chunk_end + timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def update_daily():
    for symbol in SYMBOLS:
        df_existing = get_daily(symbol, START_DATE.isoformat(), TODAY.isoformat())

        # Nothing yet?  Back-fill from START_DATE
        if df_existing.empty:
            need_from = START_DATE
        else:
            last_date = df_existing["date"].max()

            # MODIFIED SECTION: Already have today’s bar → skip
            if last_date >= TODAY:
                print(f"✓ {symbol}: up-to-date")
                continue

            need_from = last_date + timedelta(days=1)

        df_new = fetch_chunked(symbol, need_from, TODAY)
        # convert UTC timestamps to naive IST before inserting
        df_new = localize_df(df_new, "date")
        if df_new.empty:
            print(f"⚠ {symbol}: API returned no rows for {need_from} → {TODAY}")
            continue

        insert_daily(symbol, df_new)
        print(f"↳ {symbol}: inserted {len(df_new)} rows ({need_from} → {TODAY})")


if __name__ == "__main__":
    update_daily()
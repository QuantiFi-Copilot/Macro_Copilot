# market_data_agent/ingestion/bootstrap_historical.py
"""
Fast + resilient bootstrap:
  • shared Kite session
  • 3-thread parallelism (stays within 3 req/sec limit)
  • chunked daily fetch (≤730 days)
  • skips symbols already current
  • keeps intraday 5-min window at last 14 days
"""

from __future__ import annotations
import os, time, traceback
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pandas as pd

from market_data_agent.config.universe import SYMBOLS
from market_data_agent.ingestion.kite_client import KiteDataClient
from market_data_agent.storage.database import (
    insert_daily, insert_intraday_5m, get_daily,
)
from market_data_agent.utils.time import localize_df

TODAY = date.today()
START_DATE = date.fromisoformat(
    os.getenv("BOOTSTRAP_START_DATE", (TODAY - timedelta(days=365 * 5)).isoformat())
)
DAILY_CHUNK = timedelta(days=1000)               # 2-year windows (<2 000 days)
INTRADAY_START = (TODAY - timedelta(days=14)).strftime("%Y-%m-%d 09:15:00")
INTRADAY_END   = TODAY.strftime("%Y-%m-%d 15:30:00")

# shared client
CLIENT = KiteDataClient()

def fetch_daily_chunks(symbol: str, start: date, end: date) -> pd.DataFrame:
    frames = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + DAILY_CHUNK - timedelta(days=1), end)
        rows = CLIENT.fetch_ohlcv(symbol, cursor.isoformat(), chunk_end.isoformat(), interval="day")
        if rows and symbol in rows and rows[symbol]:
            frames.append(pd.DataFrame(rows[symbol]))
        cursor = chunk_end + timedelta(days=1)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

def bootstrap_symbol(sym: str):
    try:
        # Daily ----------------------------------------------------------------
        df_existing = get_daily(sym, START_DATE.isoformat(), TODAY.isoformat())
        if df_existing.empty:
            fetch_from = START_DATE
        else:
            last = df_existing["date"].max().date()
            if last >= TODAY - timedelta(days=1):
                print(f"✓ {sym} daily up-to-date")
                fetch_from = None
            else:
                fetch_from = last + timedelta(days=1)

        if fetch_from:
            df_daily = fetch_daily_chunks(sym, fetch_from, TODAY)
            df_daily = localize_df(df_daily, "date")
            if not df_daily.empty:
                insert_daily(sym, df_daily)
                print(f"↳ {sym} daily +{len(df_daily)} rows")

        # 5-min intraday -------------------------------------------------------
        rows_5m = CLIENT.fetch_ohlcv(sym, INTRADAY_START, INTRADAY_END, interval="5minute")
        df_5m = pd.DataFrame(rows_5m.get(sym, []))
        df_5m = localize_df(df_5m, "date")
        if not df_5m.empty:
            insert_intraday_5m(sym, df_5m)
            print(f"↳ {sym} 5-min {len(df_5m)} rows")

    except Exception as e:
        print(f"‼ {sym}: {e}")
        traceback.print_exc()

def bootstrap_historical():
    with ThreadPoolExecutor(max_workers=3) as pool:          # ~3 req/sec
        futures = {pool.submit(bootstrap_symbol, s): s for s in SYMBOLS}
        for f in as_completed(futures):
            pass
    print("✅ Bootstrap run complete.")

if __name__ == "__main__":
    bootstrap_historical()

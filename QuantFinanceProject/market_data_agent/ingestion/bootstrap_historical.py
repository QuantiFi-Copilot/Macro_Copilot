# market_data_agent/ingestion/bootstrap_historical.py

import os
from datetime import date, timedelta
import pandas as pd
from market_data_agent.config.universe import SYMBOLS
from market_data_agent.ingestion.kite_client import KiteDataClient
from market_data_agent.storage.database import insert_daily, insert_intraday_5m


# 2. Define date ranges
TODAY = date.today()
START_DATE = (TODAY - timedelta(days=365 * 5)).isoformat()  # 5 years ago
INTRADAY_WINDOW_START = (TODAY - timedelta(days=14)).strftime("%Y-%m-%d 09:15:00")
INTRADAY_WINDOW_END = TODAY.strftime("%Y-%m-%d 15:30:00")

def bootstrap_one_symbol(symbol: str):
    client = KiteDataClient()

    # --- Fetch and insert daily OHLCV ---
    print(f"Fetching daily bars for {symbol} from {START_DATE} to {TODAY.isoformat()}...")
    daily_data = client.fetch_ohlcv(symbol, START_DATE, TODAY.isoformat(), interval="day")
    df_daily = pd.DataFrame(daily_data[symbol])
    insert_daily(symbol, df_daily)
    print(f"  → Inserted/updated {len(df_daily)} daily rows.")

    # --- Fetch and insert last-two-week 5-min bars ---
    print(f"Fetching 5-min bars for {symbol} from {INTRADAY_WINDOW_START} to {INTRADAY_WINDOW_END}...")
    intraday_data = client.fetch_ohlcv(symbol, INTRADAY_WINDOW_START, INTRADAY_WINDOW_END, interval="5minute")
    df_5min = pd.DataFrame(intraday_data[symbol])
    insert_intraday_5m(symbol, df_5min)
    print(f"  → Inserted {len(df_5min)} 5-min rows (duplicates skipped).")

if __name__ == "__main__":
    for sym in SYMBOLS:
        try:
            bootstrap_one_symbol(sym)
        except Exception as e:
            print(f"❌ Error bootstrapping {sym}: {e}")
    print("✅ Bootstrap run complete.")

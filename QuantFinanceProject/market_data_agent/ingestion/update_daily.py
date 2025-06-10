# market_data_agent/ingestion/update_daily.py

from datetime import date, timedelta
import pandas as pd

from market_data_agent.ingestion.kite_client import KiteDataClient
from market_data_agent.storage.database import insert_daily, get_daily
from market_data_agent.config.universe import SYMBOLS

def update_daily():
    client = KiteDataClient()
    today = date.today()
    fetch_to = today.isoformat()

    for symbol in SYMBOLS:
        # 2) Find the last date already in the DB for this symbol (up through today)
        df_existing = get_daily(symbol, "1900-01-01", fetch_to)
        if not df_existing.empty:
            last_date = df_existing["date"].max()
        else:
            last_date = today - timedelta(days=365 * 10)

        # 3) If last_date >= today, nothing to do
        if last_date >= today:
            print(f"{symbol}: already up to date through {last_date}.")
            continue

        # 4) We need to fetch from (last_date + 1) up to today
        fetch_from = (last_date + timedelta(days=1)).isoformat()

        print(f"Fetching daily for {symbol} from {fetch_from} to {fetch_to}...")
        data = client.fetch_ohlcv(symbol, fetch_from, fetch_to, interval="day")

        # —— INSERTED DEBUG PRINTS HERE ——
        if symbol not in data:
            print(f"  → DEBUG: key '{symbol}' not in returned data. Returned keys: {list(data.keys())}")
            df_new = pd.DataFrame()  # force an empty DataFrame so we see "no new bars."
        else:
            df_new = pd.DataFrame(data[symbol])
            print(f"  → DEBUG: df_new.head() for {symbol}:\n{df_new.head()}\n")
            print(f"  → DEBUG: df_new.tail() for {symbol}:\n{df_new.tail()}\n")
        # ————————————————————————————————

        if df_new.empty:
            print(f"  → No new daily bars for {symbol} (holiday/API returned nothing).")
            continue

        # 5) Upsert new rows
        insert_daily(symbol, df_new)
        print(f"  → Inserted/updated {len(df_new)} daily rows for {symbol}.")

    print("✅ Daily update finished.")

if __name__ == "__main__":
    update_daily()

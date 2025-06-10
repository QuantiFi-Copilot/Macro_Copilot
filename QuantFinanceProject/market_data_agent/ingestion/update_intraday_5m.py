# market_data_agent/ingestion/update_intraday_5m.py

from datetime import date, timedelta
import pandas as pd

from market_data_agent.ingestion.kite_client import KiteDataClient
from market_data_agent.storage.database import insert_intraday_5m
from market_data_agent.config.universe import SYMBOLS

def update_intraday_5m():
    client = KiteDataClient()
    today = date.today()

    # 1) Build ist_window_start as "14 days ago at 09:15 IST"
    ist_window_start = (today - timedelta(days=14)).strftime("%Y-%m-%d 09:15:00")
    #   a) Parse naive, then localize to IST, then convert to UTC
    window_start_utc = (
        pd.to_datetime(ist_window_start)                 # naive parser, as if local
          .tz_localize("Asia/Kolkata")                    # mark as IST
          .tz_convert("UTC")                               # convert to UTC
    )
    window_start_str = window_start_utc.strftime("%Y-%m-%d %H:%M:%S")

    # 2) Build ist_window_end as "today at 15:35 IST" (to capture the 15:30 bar)
    ist_window_end = today.strftime("%Y-%m-%d 15:35:00")
    window_end_utc = (
        pd.to_datetime(ist_window_end)
          .tz_localize("Asia/Kolkata")
          .tz_convert("UTC")
    )
    window_end_str = window_end_utc.strftime("%Y-%m-%d %H:%M:%S")

    for symbol in SYMBOLS:
        print(
            f"Fetching 5-min bars for {symbol} from {ist_window_start} IST "
            f"to {today.strftime('%Y-%m-%d 15:30:00')} IST "
            f"(UTC {window_start_str} → {window_end_str})..."
        )

        # 3) Fetch in UTC; Kite expects UTC timestamps
        data = client.fetch_ohlcv(
            symbol,
            window_start_str,  # e.g. "2025-05-23 03:45:00"
            window_end_str,    # e.g. "2025-06-06 10:05:00"
            interval="5minute"
        )
        df_5min = pd.DataFrame(data[symbol])

        if df_5min.empty:
            print(f"  → No 5-min bars returned for {symbol} (market holiday or no data).")
            continue

        # 4) Convert the fetched timestamps (UTC‐naive) into IST, then drop tzinfo
        df_5min["date"] = (
            pd.to_datetime(df_5min["date"], utc=True)       # parse as UTC‐aware
              .dt.tz_convert("Asia/Kolkata")                  # convert to IST
              .dt.tz_localize(None)                           # drop tzinfo, leaving naive IST
        )

        # 5) Upsert into the rolling‐window table (duplicates skipped)
        insert_intraday_5m(symbol, df_5min)
        print(f"  → Inserted {len(df_5min)} 5-min bars for {symbol} (duplicates skipped).")

    print("✅ 5-minute intraday update finished.")


if __name__ == "__main__":
    update_intraday_5m()

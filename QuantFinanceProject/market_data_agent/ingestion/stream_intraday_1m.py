# market_data_agent/ingestion/stream_intraday_1m.py

import time
from datetime import datetime, timedelta, timezone
import pandas as pd
from sqlalchemy import text
from market_data_agent.storage.database import engine

from market_data_agent.ingestion.kite_client import KiteDataClient
from market_data_agent.storage.database import insert_buffer_1m
from market_data_agent.config.universe import SYMBOLS

# IST market open/close in 24-hr
MARKET_OPEN  = "09:15:00"
MARKET_CLOSE = "15:30:00"

def is_market_open(now_ist: datetime) -> bool:
    """Returns True if now_ist is between 09:15 and 15:30 IST."""
    date_str = now_ist.strftime("%Y-%m-%d")
    open_dt  = datetime.fromisoformat(f"{date_str} {MARKET_OPEN}")
    close_dt = datetime.fromisoformat(f"{date_str} {MARKET_CLOSE}")
    return open_dt <= now_ist <= close_dt

def stream_intraday_1m():
    client = KiteDataClient()
    cleared_today = False

    while True:
        # 1) Get current time in UTC (timezone-aware)
        now_utc = datetime.now(timezone.utc)
        # Convert to IST
        now_ist = now_utc.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)

        # 2) If market is open, fetch this past minute’s candle
        if is_market_open(now_ist):
            # On first tick of the new session, clear yesterday's live buffer
            if not cleared_today:
                with engine.begin() as conn:
                    conn.execute(text("TRUNCATE market_data.intraday_1min_live"))
                cleared_today = True
                print(f"[{now_ist.strftime('%H:%M')}] Cleared 1-min buffer for new session.")

            for symbol in SYMBOLS:
                # a) Calculate the “minute window” in IST, then convert to UTC
                end_ist   = now_ist.replace(second=0, microsecond=0)
                start_ist = end_ist - timedelta(minutes=1)

                # Convert IST → UTC strings for Kite
                start_utc = start_ist.replace(tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
                end_utc   = end_ist.replace(tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
                start_str = start_utc.strftime("%Y-%m-%d %H:%M:%S")
                end_str   = end_utc.strftime("%Y-%m-%d %H:%M:%S")

                try:
                    data = client.fetch_ohlcv(
                        symbol,
                        start_str,
                        end_str,
                        interval="1minute"
                    )
                    df_1m = pd.DataFrame(data[symbol])
                    if not df_1m.empty:
                        # Convert UTC timestamps → IST, then drop tzinfo
                        df_1m["date"] = (
                            pd.to_datetime(df_1m["date"], utc=True)
                              .dt.tz_convert("Asia/Kolkata")
                              .dt.tz_localize(None)
                        )
                        insert_buffer_1m(symbol, df_1m)
                        print(f"[{end_ist.strftime('%H:%M')}] Inserted 1-min bar for {symbol}")
                    else:
                        print(f"[{end_ist.strftime('%H:%M')}] No 1-min bar returned for {symbol}")
                except Exception as e:
                    print(f"Error fetching 1-min for {symbol} at {end_ist}: {e}")

            # 3) Sleep until next minute mark (IST)
            next_minute = (now_ist + timedelta(minutes=1)).replace(second=0, microsecond=0)
            secs_to_sleep = (next_minute - now_ist).seconds
            time.sleep(secs_to_sleep)

        else:
            # Reset flag so we can clear buffer again next session
            cleared_today = False

            # If market is closed, sleep until 09:15 IST next trading day
            tomorrow = now_ist.date() + timedelta(days=1)
            next_open_ist = datetime.fromisoformat(f"{tomorrow} {MARKET_OPEN}")
            # Convert IST → UTC to compute sleep duration
            next_open_utc = next_open_ist.replace(tzinfo=timezone(timedelta(hours=5, minutes=30))).astimezone(timezone.utc)
            secs_to_sleep = (next_open_utc - now_utc).total_seconds()
            print(f"Market closed at {now_ist.time()}, sleeping until next open.")
            time.sleep(max(secs_to_sleep, 0))


if __name__ == "__main__":
    stream_intraday_1m()

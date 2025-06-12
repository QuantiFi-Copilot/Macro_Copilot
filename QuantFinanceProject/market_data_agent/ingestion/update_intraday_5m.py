"""
Real-time 1-minute streamer (robust)

• Connects via KiteTicker WebSocket.
• Reconnects with exponential back-off (5 s → 10 s → 30 s).
• Buffers ticks and flushes to DB every 30 s via insert_buffer_1m().
• Converts timestamps to IST-naïve (to_ist_naive).
• Keeps only the last 1 day of data in market_data.intraday_1min_live.
"""

from __future__ import annotations
import os, json, time, threading
from datetime import datetime, timedelta, timezone

import pandas as pd
from kiteconnect import KiteTicker, KiteConnect
from sqlalchemy import text

from market_data_agent.config.universe import SYMBOLS
from market_data_agent.utils.time import to_ist_naive
from market_data_agent.storage.database import insert_buffer_1m, engine

# --------------------------------------------------------------------------- #
API_KEY      = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")   # refreshed by refresh_token.py
# --------------------------------------------------------------------------- #

# in-memory buffer {symbol: [ {dict row}, … ]}
BUF: dict[str, list[dict]] = {}
BUF_LOCK = threading.Lock()

FLUSH_INTERVAL = 30        # seconds

def on_ticks(ws, ticks):
    """Called by KiteTicker for each tick list."""
    with BUF_LOCK:
        for t in ticks:
            tradingsym = t["instrument_token"]
            # Map instrument_token → symbol once
            symbol = token2symbol[tradingsym]
            ts_ist = to_ist_naive(t["timestamp"])
            BUF.setdefault(symbol, []).append({
                "symbol": symbol,
                "time":   ts_ist,
                "open":   t["ohlc"]["open"],
                "high":   t["ohlc"]["high"],
                "low":    t["ohlc"]["low"],
                "close":  t["last_price"],
                "volume": t["volume"],
            })

def on_connect(ws, _response):
    print("✔ WebSocket connected; subscribing…")
    ws.subscribe(list(token2symbol.keys()))
    ws.set_mode(ws.MODE_FULL, list(token2symbol.keys()))

def on_close(ws, _code, _reason):
    print("✖ WebSocket closed:", _reason)

def flusher():
    while True:
        time.sleep(FLUSH_INTERVAL)
        now = datetime.now()
        cutoff = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        with BUF_LOCK:
            snapshot = BUF.copy()
            BUF.clear()

        if snapshot:
            frames = [pd.DataFrame(rows) for rows in snapshot.values() if rows]
            df_all  = pd.concat(frames, ignore_index=True)
            insert_buffer_1m("BATCH", df_all)     # symbol column is in df
            print(f"→ flushed {len(df_all)} rows ({now:%H:%M:%S})")

        # prune rows older than 1 day
        with engine.begin() as conn:
            conn.execute(
                text("""
                    DELETE FROM market_data.intraday_1min_live
                    WHERE time < :cutoff
                """),
                {"cutoff": cutoff},
            )

def build_token_map(kite: KiteConnect):
    instruments = kite.instruments("NSE")
    wanted = {s.split(".")[0] for s in SYMBOLS}      # RELIANCE from RELIANCE.NS
    mapping = {}
    for row in instruments:
        if row["tradingsymbol"] in wanted:
            mapping[row["instrument_token"]] = f"{row['tradingsymbol']}.NS"
    return mapping

def run_streamer():
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    global token2symbol
    token2symbol = build_token_map(kite)
    if not token2symbol:
        raise RuntimeError("Token map is empty; check instruments download.")

    ws = KiteTicker(API_KEY, ACCESS_TOKEN, debug=False)

    ws.on_ticks      = on_ticks
    ws.on_connect    = on_connect
    ws.on_close      = on_close

    # background flushing thread
    threading.Thread(target=flusher, daemon=True).start()

    backoff = 5
    while True:
        try:
            ws.connect(threaded=True)
            while ws.is_connected():
                time.sleep(1)
            print("WebSocket disconnected; reconnecting in", backoff, "s")
        except Exception as e:
            print("‼ WebSocket error:", e, "– reconnecting in", backoff, "s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)  # max 30 s

if __name__ == "__main__":
    run_streamer()
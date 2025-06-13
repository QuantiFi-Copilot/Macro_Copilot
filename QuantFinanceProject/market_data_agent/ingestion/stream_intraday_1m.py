# market_data_agent/ingestion/stream_intraday_1m.py

from __future__ import annotations

import os, time, threading
from datetime import datetime, timedelta, date as Date
from zoneinfo import ZoneInfo

import pandas as pd
from kiteconnect import KiteTicker
from sqlalchemy import text

# MODIFIED: Import our own client instead of the low-level KiteConnect
from market_data_agent.ingestion.kite_client import KiteDataClient
from market_data_agent.config.universe import SYMBOLS
from market_data_agent.utils.time import to_ist_naive
from market_data_agent.storage.database import insert_buffer_1m, engine

IST = ZoneInfo("Asia/Kolkata")
FLUSH_INTERVAL = 30

# In-memory buffer and lock remain the same
BUF: dict[str, list[dict]] = {}
BUF_LOCK = threading.Lock()

def on_ticks(ws, ticks):
    with BUF_LOCK:
        for t in ticks:
            # Defensive check to ignore non-tick messages
            if isinstance(t, dict) and "instrument_token" in t and "timestamp" in t:
                token = t["instrument_token"]
                symbol = token2symbol[token]
                ts_ist = to_ist_naive(t["timestamp"])

                BUF.setdefault(symbol, []).append(
                    {
                        "symbol": symbol,
                        "time":   ts_ist,
                        "open":   t["ohlc"]["open"],
                        "high":   t["ohlc"]["high"],
                        "low":    t["ohlc"]["low"],
                        "close":  t["last_price"],
                        "volume": t["volume"],
                    }
                )

def on_connect(ws, _resp):
    print("✔ WebSocket connected; subscribing …")
    ws.subscribe(list(token2symbol.keys()))
    ws.set_mode(ws.MODE_FULL, list(token2symbol.keys()))

def on_close(ws, _code, reason):
    print("✖ WebSocket closed:", reason)

def flusher():
    """Flush buffered ticks to DB every FLUSH_INTERVAL seconds."""
    while True:
        time.sleep(FLUSH_INTERVAL)
        now = datetime.now(IST)

        with BUF_LOCK:
            snapshot = BUF.copy()
            BUF.clear()

        if snapshot:
            frames = [pd.DataFrame(v) for v in snapshot.values() if v]
            if frames:
                df_all = pd.concat(frames, ignore_index=True)
                # The DataFrame now has the correct 'time' column, pass directly.
                insert_buffer_1m(df_all)
                print(f"→ flushed {len(df_all):,} rows ({now:%H:%M:%S})")

def daily_reset_worker():
    """
    At 09:00 IST each morning: wipe buffer rows earlier than 09:15,
    ensuring a clean start for the new session.
    """
    while True:
        now = datetime.now(IST)
        today_9am = now.replace(hour=9, minute=0, second=0, microsecond=0)

        if now > today_9am:
            next_run_time = today_9am + timedelta(days=1)
        else:
            next_run_time = today_9am

        sleep_duration = (next_run_time - now).total_seconds()
        print(f"🧹 daily_reset_worker: sleeping for {sleep_duration/3600:.2f} hours until {next_run_time}")
        time.sleep(sleep_duration)

        cutoff = next_run_time.replace(minute=15).strftime("%Y-%m-%d 09:15:00")
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM market_data.intraday_1min_live WHERE time < :cutoff"),
                {"cutoff": cutoff},
            )
        print("🧹 daily_reset_worker: cleared rows < ", cutoff)
        time.sleep(60)

def backfill_today(client: KiteDataClient):
    """
    If the process starts after a trading session, pull the full 1-min candle
    set for that day (09:15–15:30) before WebSocket streaming begins.
    """
    now = datetime.now(IST)
    trade_date: Date = now.date()
    if now.time() < datetime.strptime("09:15", "%H:%M").time():
        trade_date -= timedelta(days=1)
    
    if trade_date.weekday() >= 5:
        print(f"✓ Skipping back-fill, {trade_date} is a weekend.")
        return

    start = datetime(trade_date.year, trade_date.month, trade_date.day,  9, 15, tzinfo=IST)
    end   = datetime(trade_date.year, trade_date.month, trade_date.day, 15, 30, tzinfo=IST)

    if now < end:
        print("✓ Script started before session end; skipping back-fill.")
        return

    print(f"↻ Back-filling 1-min candles for {trade_date}…")
    
    candles_data = client.fetch_ohlcv(SYMBOLS, from_date=start, to_date=end, interval="minute")

    for sym, candles in candles_data.items():
        if not candles: continue
        
        df = pd.DataFrame(candles)
        df['symbol'] = sym
        df = df.rename(columns={'date': 'time'})
        df = df[["symbol", "time", "open", "high", "low", "close", "volume"]]
        insert_buffer_1m(df)
        
    print("✓ Back-fill complete.")

def build_token_maps(client: KiteDataClient) -> tuple[dict, dict]:
    """Builds the token <-> symbol maps required by the streamer."""
    t2s, s2t = {}, {}
    for sym in SYMBOLS:
        try:
            token = client.get_instrument_token(sym)
            t2s[token] = sym
            s2t[sym] = token
        except ValueError as e:
            print(f"Warning: {e}")
    return t2s, s2t

def run_streamer():
    """Main entry point: Initializes client, threads, and reconnect loop."""
    print("🚀 Initializing Market Streamer...")
    # This single line handles token refresh and API client setup
    client = KiteDataClient()
    print("✔ Kite client initialized and token refreshed.")

    global token2symbol
    token2symbol, _ = build_token_maps(client)
    if not token2symbol:
        raise RuntimeError("Instrument token map is empty – aborting.")
    print(f"✔ Token map built for {len(token2symbol)} symbols.")

    # Pass the smart client to the back-fill function
    backfill_today(client)

    # Start background threads
    threading.Thread(target=flusher, daemon=True).start()
    threading.Thread(target=daily_reset_worker, daemon=True).start()
    print("✔ Background threads (flusher, daily_reset) started.")

    # Use the new properties on the client to initialize KiteTicker
    ws = KiteTicker(client.api_key, client.access_token, debug=False)
    ws.on_ticks   = on_ticks
    ws.on_connect = on_connect
    ws.on_close   = on_close

    # Main reconnect loop
    backoff = 5
    while True:
        if ws.is_connected():
            time.sleep(1)
            continue
        try:
            print("Attempting to connect to WebSocket...")
            ws.connect(threaded=True)
            backoff = 5 # Reset backoff after a successful connection
        except Exception as e:
            print(f"‼ WebSocket error: {e}")
        
        print(f"WebSocket disconnected – reconnecting in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)

if __name__ == "__main__":
    run_streamer()
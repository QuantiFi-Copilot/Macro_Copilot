"""
Live 1-minute streamer (IST-native + daily back-fill + rolling reset)

• All timestamps stored **IST-naïve** (no timezone column).
• Works even if you start the process **after** market close:
    └─ pulls full-day 1-min candles first, then begins WebSocket streaming.
• Keeps only the most-recent 1 trading-day of 1-minute data
  in `market_data.intraday_1min_live`.
• Robust reconnect loop with exponential back-off.
"""

from __future__ import annotations

import os, time, json, threading
from datetime import datetime, timedelta, date as Date
from zoneinfo import ZoneInfo

import pandas as pd
from kiteconnect import KiteConnect, KiteTicker
from sqlalchemy import text

from market_data_agent.config.universe import SYMBOLS
from market_data_agent.utils.time import to_ist_naive
from market_data_agent.storage.database import insert_buffer_1m, engine

# ──────────────────────────────────────────────────────────────────────────────
API_KEY       = os.getenv("KITE_API_KEY")
ACCESS_TOKEN  = os.getenv("KITE_ACCESS_TOKEN")      # refreshed elsewhere
IST           = ZoneInfo("Asia/Kolkata")
FLUSH_INTERVAL = 30                                # seconds

# in-memory buffer {symbol: [row_dict, …]}
BUF: dict[str, list[dict]] = {}
BUF_LOCK = threading.Lock()
# ──────────────────────────────────────────────────────────────────────────────


# ╭───────────────────────────── helper utils ─────────────────────────────╮ #
def now_ist() -> datetime:
    return datetime.now(tz=IST)


def today_trading_window() -> tuple[datetime, datetime]:
    """Return today's 09:15 and 15:30 IST aware datetimes."""
    d = now_ist().date()
    start = datetime(d.year, d.month, d.day,  9, 15, tzinfo=IST)
    end   = datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST)
    return start, end
# ╰────────────────────────────────────────────────────────────────────────╯ #


# ╭────────────────── WebSocket (ticks → buffer) callbacks ─────────────────╮ #
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
# ╰────────────────────────────────────────────────────────────────────────╯ #


# ╭────────────────────── background housekeeping jobs ────────────────────╮ #
def flusher():
    """Flush buffered ticks to DB every FLUSH_INTERVAL seconds."""
    while True:
        time.sleep(FLUSH_INTERVAL)
        now = now_ist()
        cutoff_prune = now - timedelta(days=1)

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

        # Rolling 1-day retention
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM market_data.intraday_1min_live "
                    "WHERE time < :cutoff"
                ),
                {"cutoff": cutoff_prune.replace(tzinfo=None)},
            )


def daily_reset_worker():
    """
    At 09:00 IST each morning: wipe buffer rows earlier than 09:15,
    ensuring a clean start for the new session.
    """
    while True:
        now = now_ist()
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
                text(
                    "DELETE FROM market_data.intraday_1min_live "
                    "WHERE time < :cutoff"
                ),
                {"cutoff": cutoff},
            )
        print("🧹 daily_reset_worker: cleared rows < ", cutoff)
        time.sleep(60)

# ╰────────────────────────────────────────────────────────────────────────╯ #


# ╭────────────────────────── initial back-fill logic ──────────────────────╮ #
def backfill_today(kite: KiteConnect, symbol2token: dict[str, int]):
    """
    If the process starts after a trading session, pull the full 1-min candle
    set for that day (09:15–15:30) before WebSocket streaming begins.
    """
    now = now_ist()
    
    # If it's before market open, the session to backfill is the previous day.
    # Otherwise, it's today's session.
    trade_date: Date = now.date()
    if now.time() < datetime.strptime("09:15", "%H:%M").time():
        trade_date -= timedelta(days=1)
    
    if trade_date.weekday() >= 5: # 5=Sat, 6=Sun
        print(f"✓ Skipping back-fill, {trade_date} is a weekend.")
        return

    start = datetime(trade_date.year, trade_date.month, trade_date.day,  9, 15, tzinfo=IST)
    end   = datetime(trade_date.year, trade_date.month, trade_date.day, 15, 30, tzinfo=IST)

    if now < end:
        print("✓ Script started before session end; skipping back-fill.")
        return

    print(f"↻ Back-filling 1-min candles for {trade_date}…")

    for sym in SYMBOLS:
        token = symbol2token.get(sym)
        if not token: continue
            
        candles = kite.historical_data(
            instrument_token=token,
            from_date=start,
            to_date=end,
            interval="minute",
        )
        if not candles: continue
            
        df = pd.DataFrame(candles)
        df['symbol'] = sym
        # Rename 'date' from Kite API to 'time' to match our DB schema
        df = df.rename(columns={'date': 'time'})
        
        # Ensure correct column order and pass directly to DB function
        df = df[["symbol", "time", "open", "high", "low", "close", "volume"]]
        insert_buffer_1m(df)
        
    print("✓ Back-fill complete.")
# ╰────────────────────────────────────────────────────────────────────────╯ #


# ╭──────────────────── utility: instruments → token map ───────────────────╮ #
def build_token_maps(kite: KiteConnect):
    instruments = kite.instruments("NSE")
    want_ns = {s.split(".")[0] for s in SYMBOLS}
    t2s, s2t = {}, {}
    for row in instruments:
        ts = row["tradingsymbol"]
        if ts in want_ns:
            symbol = f"{ts}.NS"
            token = row["instrument_token"]
            t2s[token] = symbol
            s2t[symbol] = token
    return t2s, s2t
# ╰────────────────────────────────────────────────────────────────────────╯ #


# ╭──────────────────────────── main entry-point ───────────────────────────╮ #
def run_streamer():
    kite = KiteConnect(api_key=API_KEY)
    kite.set_access_token(ACCESS_TOKEN)

    global token2symbol
    token2symbol, symbol2token = build_token_maps(kite)
    if not token2symbol:
        raise RuntimeError("Instrument token map is empty – aborting.")

    backfill_today(kite, symbol2token)

    threading.Thread(target=flusher,             daemon=True).start()
    threading.Thread(target=daily_reset_worker,  daemon=True).start()

    ws = KiteTicker(API_KEY, ACCESS_TOKEN, debug=False)
    ws.on_ticks   = on_ticks
    ws.on_connect = on_connect
    ws.on_close   = on_close

    backoff = 5
    while True:
        if ws.is_connected():
            time.sleep(1)
            continue
        try:
            print("Attempting to connect to WebSocket...")
            ws.connect(threaded=True)
            backoff = 5
        except Exception as e:
            print(f"‼ WebSocket error: {e}")
        
        print(f"WebSocket disconnected – reconnecting in {backoff}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    run_streamer()
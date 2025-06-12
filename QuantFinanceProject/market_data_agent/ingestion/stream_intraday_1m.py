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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from kiteconnect import KiteConnect, KiteTicker
from sqlalchemy import text

from market_data_agent.config.universe import SYMBOLS
from market_data_agent.utils.time import to_ist_naive  # <— helper you already added
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
            token = t["instrument_token"]
            symbol = token2symbol[token]        # guaranteed present
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
            df_all = pd.concat(frames, ignore_index=True)
            insert_buffer_1m("BATCH", df_all)   # symbol col already present
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
        tomorrow_9 = (now + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        time.sleep((tomorrow_9 - now).total_seconds())

        cutoff = tomorrow_9.strftime("%Y-%m-%d 09:15:00")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM market_data.intraday_1min_live "
                    "WHERE time < :cutoff"
                ),
                {"cutoff": cutoff},
            )
        print("🧹 daily_reset_worker: cleared rows < ", cutoff)
# ╰────────────────────────────────────────────────────────────────────────╯ #


# ╭────────────────────────── initial back-fill logic ──────────────────────╮ #
def backfill_today(kite: KiteConnect, symbol2token: dict[str, int]):
    """
    If the process starts *after* 15:30 IST, pull the full 1-min candle
    set for today (09:15–15:30) before WebSocket streaming begins.
    """
    start, end = today_trading_window()
    now = now_ist()
    if now < end:                       # market open/ongoing → skip back-fill
        return

    print("↻ Back-filling today’s 1-min candles …")

    for sym in SYMBOLS:
        token = symbol2token[sym]
        candles = kite.historical_data(
            instrument_token=token,
            from_date=start.astimezone(timezone.utc),
            to_date=end.astimezone(timezone.utc),
            interval="minute",
        )
        if not candles:
            continue
        df = pd.DataFrame(candles)
        df["time"] = (
            pd.to_datetime(df["date"])
            .dt.tz_convert(IST)
            .dt.tz_localize(None)
        )
        df = df.rename(
            columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }
        )[["time", "open", "high", "low", "close", "volume"]]
        insert_buffer_1m(sym, df)
    print("✓ Back-fill complete.")
# ╰────────────────────────────────────────────────────────────────────────╯ #


# ╭──────────────────── utility: instruments → token map ───────────────────╮ #
def build_token_maps(kite: KiteConnect):
    instruments = kite.instruments("NSE")
    want_ns = {s.split(".")[0] for s in SYMBOLS}  # RELIANCE from RELIANCE.NS
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

    # optional one-time back-fill
    backfill_today(kite, symbol2token)

    # background jobs
    threading.Thread(target=flusher,             daemon=True).start()
    threading.Thread(target=daily_reset_worker,  daemon=True).start()

    ws = KiteTicker(API_KEY, ACCESS_TOKEN, debug=False)
    ws.on_ticks   = on_ticks
    ws.on_connect = on_connect
    ws.on_close   = on_close

    backoff = 5
    while True:
        try:
            ws.connect(threaded=True)
            while ws.is_connected():
                time.sleep(1)
            print("WebSocket disconnected – reconnecting in", backoff, "s")
        except Exception as e:
            print("‼ WebSocket error:", e, "– reconnecting in", backoff, "s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    run_streamer()
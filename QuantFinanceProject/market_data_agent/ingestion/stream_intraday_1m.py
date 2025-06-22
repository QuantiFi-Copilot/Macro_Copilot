from __future__ import annotations

# ── load .env ─────────────────────────────────────────────────────────────
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(), override=True)

import time, threading, argparse
from datetime import datetime, timedelta, date as Date, time as dtime
from zoneinfo import ZoneInfo
from typing import Dict, List

import pandas as pd
from kiteconnect import KiteTicker
from kiteconnect.exceptions import InputException
from sqlalchemy import text

from market_data_agent.ingestion.kite_client import KiteDataClient
from market_data_agent.config.universe import SYMBOLS
from market_data_agent.utils.time import to_ist_naive
from market_data_agent.storage.database import insert_buffer_1m, engine

# ───────────── config ────────────────────────────────────────────────────
IST            = ZoneInfo("Asia/Kolkata")
SESSION_OPEN   = dtime(9, 15)
SESSION_CLOSE  = dtime(15, 30)
FLUSH_INTERVAL = 30

BUF: Dict[str, List[dict]] = {}
BUF_LOCK       = threading.Lock()
DEBUG          = False
def dbg(*m): 
    if DEBUG: print(*m)

# ───────────── util helpers ───────────────────────────────────────────────
def prev_trading_day(ref: Date) -> Date:
    ref -= timedelta(days=1)
    while ref.weekday() >= 5:                     # Sat/Sun
        ref -= timedelta(days=1)
    return ref

def wipe_before(date_: Date):
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM market_data.intraday_1min_live "
                 "WHERE time < :cutoff"),
            {"cutoff": datetime.combine(date_, dtime.min, IST)}
        )
    print(f"🗑️  wiped data before {date_}")

def db_has_day(date_: Date) -> bool:
    with engine.begin() as conn:
        return bool(conn.execute(
            text("SELECT 1 FROM market_data.intraday_1min_live "
                 "WHERE time::date = :d LIMIT 1"),
            {"d": date_}
        ).first())

# ───────── kite helpers ───────────────────────────────────────────────────
def build_token_maps(client: KiteDataClient):
    t2s: dict[int, str] = {}
    for sym in SYMBOLS:
        try:
            tok = client.get_instrument_token(sym)
            if tok:
                t2s[tok] = sym
        except Exception as e:
            dbg(f"⚠ token lookup failed for {sym}: {e}")
    return t2s, {s: t for t, s in t2s.items()}

def fetch_ohlcv_naive(client: KiteDataClient,
                      tok_list: List[int],
                      frm: datetime,
                      to: datetime) -> Dict[int, List]:
    """
    Call Kite historical API directly with numeric instrument tokens.
    Skip tokens whose date range is invalid.
    """
    frm = frm.replace(tzinfo=None, microsecond=0)
    to  =  to.replace(tzinfo=None, microsecond=0)
    out: Dict[int, List] = {}
    for tok in tok_list:
        try:
            rows = client._kite.historical_data(
                tok, frm, to, interval="minute", continuous=False, oi=False
            )
            out[tok] = rows
        except InputException as e:
            if "invalid from date" in str(e):
                dbg(f"⚠ {tok}: {e} – skipping")
                continue
            raise
    return out

# ───────── websocket callbacks ────────────────────────────────────────────
def on_ticks(ws, ticks):
    for t in ticks:
        try:
            ts_raw = t.get("timestamp") or t.get("exchange_timestamp")
            if not ts_raw:
                dbg("⚠ tick without timestamp:", t); continue
            if DEBUG:
                dbg("🟢", time.strftime('%H:%M:%S'),
                    t["instrument_token"], t.get("last_price"))
            sym = token2sym[t["instrument_token"]]
            BUF.setdefault(sym, []).append(
                dict(
                    symbol = sym,
                    time   = to_ist_naive(ts_raw),
                    open   = t["ohlc"]["open"],
                    high   = t["ohlc"]["high"],
                    low    = t["ohlc"]["low"],
                    close  = t["last_price"],
                    volume = t.get("volume") or t.get("volume_traded"),
                )
            )
        except Exception as e:
            dbg("⚠ malformed tick skipped:", e, t)

def on_connect(ws, _):
    print("✔ WebSocket connected; subscribing …")
    tok_list = list(token2sym.keys())
    dbg("subscribing", tok_list[:10], "…")
    ws.subscribe(tok_list)
    ws.set_mode(ws.MODE_FULL, tok_list)

def on_close(ws, code, reason):
    print("✖ WebSocket closed:", code, reason)

# ───────── background threads ─────────────────────────────────────────────
def flusher():
    while True:
        time.sleep(FLUSH_INTERVAL)
        with BUF_LOCK:
            snap = BUF.copy(); BUF.clear()
        if not snap: continue
        df = pd.concat([pd.DataFrame(v) for v in snap.values()],
                       ignore_index=True)
        try:
            insert_buffer_1m(df)
            print(f"✅ flushed {len(df):,} rows "
                  f"({datetime.now(IST):%H:%M:%S})")
        except Exception as e:
            print("❌ DB insert failed:", e)

def daily_reset_worker():
    while True:
        now = datetime.now(IST)
        next_9 = (now.replace(hour=9, minute=0, second=0, microsecond=0)
                  + timedelta(days=1))
        time.sleep((next_9 - now).total_seconds())
        wipe_before(next_9.date())
        with BUF_LOCK: BUF.clear()
        print("🧹 daily reset done @ 09:00 IST")

# ───────── back-fill logic ───────────────────────────────────────────────
def backfill_if_needed(client: KiteDataClient):
    now   = datetime.now(IST)
    today = now.date()

    if now.time() >= SESSION_CLOSE:
        if db_has_day(today):
            print("✓ Today already in DB; skip back-fill."); return
        frm = datetime.combine(today, SESSION_OPEN, IST)
        to  = datetime.combine(today, SESSION_CLOSE, IST)
    elif now.time() >= SESSION_OPEN:
        frm = datetime.combine(today, SESSION_OPEN, IST)
        to  = now
    else:
        print("⌚ Pre-market; back-fill deferred."); return

    print(f"↻ Back-filling {today} {frm.time()}–{to.time()} …")
    candles = fetch_ohlcv_naive(client, list(token2sym.keys()), frm, to)
    frames = []
    for tok, rows in candles.items():
        if rows:
            df = pd.DataFrame(rows).rename(columns={"date": "time"})
            df["symbol"] = token2sym[tok]
            frames.append(df)
    if frames:
        insert_buffer_1m(pd.concat(frames, ignore_index=True))
    print("↻ Back-fill done.")

def weekend_backfill(client: KiteDataClient):
    today = datetime.now(IST).date()
    if today.weekday() < 5: return
    fri = prev_trading_day(today)
    if db_has_day(fri):
        print("✓ Friday already in DB; nothing to do."); return
    print(f"↻ Weekend launch – back-filling Friday {fri} …")
    frm = datetime.combine(fri, SESSION_OPEN, IST)
    to  = datetime.combine(fri, SESSION_CLOSE, IST)
    candles = fetch_ohlcv_naive(client, list(token2sym.keys()), frm, to)
    frames = []
    for tok, rows in candles.items():
        if rows:
            df = pd.DataFrame(rows).rename(columns={"date": "time"})
            df["symbol"] = token2sym[tok]
            frames.append(df)
    if frames:
        insert_buffer_1m(pd.concat(frames, ignore_index=True))
    print("↻ Weekend back-fill done.")

# ───────── main loop ─────────────────────────────────────────────────────
def run_streamer():
    print("🚀 Initializing Market Streamer…")
    client = KiteDataClient(); print("✔ Kite client ready.")

    now = datetime.now(IST)
    if now.time() >= dtime(9,0):
        wipe_before(now.date())

    global token2sym
    token2sym, _ = build_token_maps(client)
    if not token2sym:
        raise RuntimeError("Instrument token map empty – aborting.")
    print(f"✔ Token map built for {len(token2sym)} symbols.")

    weekend_backfill(client)
    backfill_if_needed(client)

    threading.Thread(target=flusher, daemon=True).start()
    threading.Thread(target=daily_reset_worker, daemon=True).start()
    print("✔ Background threads started.")

    ws = KiteTicker(client.api_key, client.access_token, debug=False)
    ws.on_ticks, ws.on_connect, ws.on_close = on_ticks, on_connect, on_close

    backoff = 5
    while True:
        if ws.is_connected():
            time.sleep(1); continue
        try:
            print("Attempting to connect to WebSocket …")
            ws.connect(threaded=True); backoff = 5
        except Exception as e:
            print("‼ WebSocket error:", e)
        print(f"WebSocket disconnected – reconnecting in {backoff}s")
        time.sleep(backoff); backoff = min(backoff*2, 60)

# ───────── CLI entry ─────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run NSE 1-min streamer")
    parser.add_argument("--debug", action="store_true")
    DEBUG = parser.parse_args().debug
    run_streamer()

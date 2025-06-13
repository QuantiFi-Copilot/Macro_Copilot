# market_data_agent/storage/database.py

import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from market_data_agent.utils.time import localize_df
import pandas as pd

dotenv_path = find_dotenv()
load_dotenv(dotenv_path, override=True)

DB_USER = os.getenv("MARKET_DB_USER")
DB_PASS = os.getenv("MARKET_DB_PASSWORD")
DB_NAME = os.getenv("MARKET_DB_NAME")
DB_HOST = os.getenv("MARKET_DB_HOST", "localhost")
DB_PORT = os.getenv("MARKET_DB_PORT", "5432")

if not (DB_USER and DB_PASS and DB_NAME):
    raise RuntimeError("One or more MARKET_DB_* variables are missing in .env")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
engine: Engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    echo=False,
)

# -----------------------------------------------------------------------------
# Ensure every session defaults to Asia/Kolkata (IST)
# -----------------------------------------------------------------------------
with engine.begin() as conn:
    conn.execute(text("SET TIME ZONE 'Asia/Kolkata'"))

def insert_daily(symbol: str, df: pd.DataFrame):
    """
    Bulk upsert daily OHLCV into market_data.daily_ohlcv.
    Expects df with columns: ['date','open','high','low','close','volume'].
    Here we convert df['date'] (a timestamp with tz) into a pure date.
    """
    # Convert incoming timezone‑aware UTC timestamps to naive IST
    df = localize_df(df, "date")
    rows = []
    for row in df.to_dict(orient="records"):
        # Convert the timestamp+tz to a pure date object:
        pure_date = row["date"].date()
        rows.append({
            "symbol": symbol,
            "time": pure_date,
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        })

    stmt = text("""
    INSERT INTO market_data.daily_ohlcv(symbol, time, open, high, low, close, volume)
    VALUES(:symbol, :time, :open, :high, :low, :close, :volume)
    ON CONFLICT(symbol, time) DO UPDATE SET
      open = EXCLUDED.open,
      high = EXCLUDED.high,
      low = EXCLUDED.low,
      close = EXCLUDED.close,
      volume = EXCLUDED.volume;
    """)
    with engine.begin() as conn:
        conn.execute(stmt, rows)

def insert_intraday_5m(symbol: str, df: pd.DataFrame):
    """
    Bulk insert 5-min candles for the last 10 days into market_data.intraday_5min_ohlcv.
    Expects df with columns: ['date','open','high','low','close','volume'].
    """
    # Ensure timestamps are naive IST before insert
    df = localize_df(df, "date")
    rows = [
        {
            "symbol": symbol,
            "time": row["date"],
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
        }
        for row in df.to_dict(orient="records")
    ]
    stmt = text("""
    INSERT INTO market_data.intraday_5min_ohlcv(symbol, time, open, high, low, close, volume)
    VALUES(:symbol, :time, :open, :high, :low, :close, :volume)
    ON CONFLICT(symbol, time) DO NOTHING;
    """)
    with engine.begin() as conn:
        conn.execute(stmt, rows)


# -----------------------------------------------------------------------------
# MODIFIED FUNCTION
# -----------------------------------------------------------------------------
def insert_buffer_1m(df: pd.DataFrame):
    """
    Bulk insert 1-min candles for today into market_data.intraday_1min_live.
    Expects df with columns: ['symbol', 'time', 'open', 'high', 'low', 'close', 'volume'].
    Retention policy automatically drops data older than 1 day.
    """
    # Ensure timestamps are naive IST before insert.
    # The incoming DataFrame is now expected to have a 'time' column directly.
    df = localize_df(df, "time")

    # The DataFrame already contains the correct symbol for each row.
    # We can convert it directly to a list of dictionaries for insertion.
    rows = df.to_dict(orient="records")

    stmt = text("""
    INSERT INTO market_data.intraday_1min_live(symbol, time, open, high, low, close, volume)
    VALUES(:symbol, :time, :open, :high, :low, :close, :volume)
    ON CONFLICT(symbol, time) DO NOTHING;
    """)
    if rows:
        with engine.begin() as conn:
            conn.execute(stmt, rows)


# ────────────────────────────────────────────────────────────────────────────────
# Query Helpers (Unchanged)
# ────────────────────────────────────────────────────────────────────────────────

def get_daily(symbol: str, start: str, end: str) -> pd.DataFrame:
    """
    Returns daily OHLCV for [start,end] as a DataFrame.
    """
    sql = text("""
    SELECT time AS date, open, high, low, close, volume
    FROM market_data.daily_ohlcv
    WHERE symbol = :symbol
      AND time BETWEEN :start AND :end
    ORDER BY time;
    """)
    return pd.read_sql(sql, engine, params={"symbol": symbol, "start": start, "end": end})


def get_intraday_5m(symbol: str, since_ts: str) -> pd.DataFrame:
    """
    Returns 5-min intraday data since `since_ts` as a DataFrame.
    """
    sql = text("""
    SELECT time AS date, open, high, low, close, volume
    FROM market_data.intraday_5min_ohlcv
    WHERE symbol = :symbol
      AND time >= :since_ts
    ORDER BY time;
    """)
    return pd.read_sql(sql, engine, params={"symbol": symbol, "since_ts": since_ts})


def get_buffer_1m(symbol: str) -> pd.DataFrame:
    """
    Returns the full buffer of today's 1-min intraday data as a DataFrame.
    """
    sql = text("""
    SELECT time AS date, open, high, low, close, volume
    FROM market_data.intraday_1min_live
    WHERE symbol = :symbol
    ORDER BY time;
    """)
    return pd.read_sql(sql, engine, params={"symbol": symbol})
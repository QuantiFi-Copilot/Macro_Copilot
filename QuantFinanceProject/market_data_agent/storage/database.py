# market_data_agent/db/database.py

import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
import pandas as pd

# ────────────────────────────────────────────────────────────────────────────────
# 1) Load your DATABASE_URL (with credentials) from the project‐root .env
dotenv_path = find_dotenv()
if not dotenv_path:
    raise RuntimeError("Could not find a .env file in parent directories.")
load_dotenv(dotenv_path, override=True)

# Read individual DB credentials from environment
DB_USER = os.getenv("MARKET_DB_USER")
DB_PASS = os.getenv("MARKET_DB_PASSWORD")
DB_NAME = os.getenv("MARKET_DB_NAME")
DB_HOST = os.getenv("MARKET_DB_HOST", "localhost")
DB_PORT = os.getenv("MARKET_DB_PORT", "5432")

if not (DB_USER and DB_PASS and DB_NAME):
    raise RuntimeError("One or more MARKET_DB_* variables are missing in .env")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# 2) Create a single, shared SQLAlchemy Engine
#    pool_size/overflow tuned for moderate concurrency
engine: Engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=5,
    echo=False,
)

# ────────────────────────────────────────────────────────────────────────────────
# 3) Insert Helpers

def insert_daily(symbol: str, df: pd.DataFrame):
    """
    Bulk upsert daily OHLCV into market_data.daily_ohlcv.
    Expects df with columns: ['date','open','high','low','close','volume'].
    """
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


def insert_buffer_1m(symbol: str, df: pd.DataFrame):
    """
    Bulk insert 1-min candles for today into market_data.intraday_1min_live.
    Expects df with columns: ['date','open','high','low','close','volume'].
    Retention policy automatically drops data older than 1 day.
    """
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
    INSERT INTO market_data.intraday_1min_live(symbol, time, open, high, low, close, volume)
    VALUES(:symbol, :time, :open, :high, :low, :close, :volume)
    ON CONFLICT(symbol, time) DO NOTHING;
    """)
    with engine.begin() as conn:
        conn.execute(stmt, rows)


# ────────────────────────────────────────────────────────────────────────────────
# 4) Query Helpers

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
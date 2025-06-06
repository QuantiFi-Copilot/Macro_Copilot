# test_kite_client.py

from market_data_agent.ingestion.kite_client import KiteDataClient
from datetime import datetime, timedelta

# Initialize client (automatically refreshes token & loads instrument map)
client = KiteDataClient()

# 1. Fetch today's LTP for a single stock
ltp = client.fetch_ltp("RELIANCE")
print("✅ LTP for RELIANCE:", ltp["RELIANCE"])

# 2. Fetch historical daily OHLCV for TCS
hist = client.fetch_ohlcv(
    "TCS",
    from_date="2024-12-01",
    to_date="2024-12-15",
    interval="day"
)
print(f"✅ {len(hist['TCS'])} daily bars for TCS")
print(hist["TCS"][-1])  # Print latest candle

# 3. Fetch recent 5-minute data for INFY
start = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d 09:15:00")
end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

intraday = client.fetch_ohlcv(
    "INFY",
    from_date=start,
    to_date=end,
    interval="5minute"
)
print(f"✅ {len(intraday['INFY'])} 5-min candles for INFY")
print(intraday["INFY"][-1])

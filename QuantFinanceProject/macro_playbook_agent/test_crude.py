import pandas as pd
from sqlalchemy import create_engine

# 1. Connect to your Postgres DB
DB_URI = "postgresql://localhost:5432/macro_agent"
engine = create_engine(DB_URI)

# 2. Load raw CRUDEBRNT series
print("\n🔎 Fetching raw CRUDEBRNT data...")
df = pd.read_sql(
    "SELECT recorded_at, value FROM macro_series WHERE ticker='CRUDEBRNT' ORDER BY recorded_at",
    engine,
    parse_dates=['recorded_at']
)

if df.empty:
    print("❌ No data found for CRUDEBRNT. Check ingestion.")
    exit()

print(f"✅ Found {len(df)} rows. Showing last 5:")
print(df.tail())

# 3. Convert to datetime index and check daily frequency
df = df.set_index('recorded_at').sort_index()
df = df[~df.index.duplicated(keep='last')]  # remove any dupes just in case
print("\n📈 Daily Crude Price Date Range:")
print(f"From: {df.index.min()}  →  To: {df.index.max()}")

# 4. Resample to Monthly Average Price
monthly_avg = df['value'].resample('M').mean()
print("\n🧮 Monthly Average Crude Price (last 5 months):")
print(monthly_avg.tail())

# 5. Compute 30-day Realized Volatility
daily_returns = df['value'].pct_change()
vol_30d = daily_returns.rolling(window=30).std()
monthly_vol = vol_30d.resample('M').last()

print("\n📊 Monthly 30-Day Realized Volatility (last 5 months):")
print(monthly_vol.tail())

import pandas as pd
from pathlib import Path
from macro_playbook_agent.storage.database import create_tables, Session, MacroSeries

# Configuration
TICKER = "IN10Y_YF"  # Customize as needed
COLUMN_NAME = "Price"

# Paths
ROOT_DIR = Path(__file__).resolve().parents[2]
catalog_path = ROOT_DIR / "macro_playbook_agent" / "utils" / "macro_catalog.csv"
raw_path = ROOT_DIR / "macro_playbook_agent" / "data" / "raw" / "raw_in10y_yf.csv"  # Rename as needed

# Load catalog metadata
catalog_df = pd.read_csv(catalog_path)
meta = catalog_df[catalog_df["ticker"] == TICKER]
if meta.empty:
    raise ValueError(f"Ticker {TICKER} not found in macro_catalog.csv")
source = meta["source"].values[0]
units = meta["units"].values[0]
frequency = meta["frequency"].values[0]

# Load and clean CSV
df = pd.read_csv(raw_path)
df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%y", errors="coerce")
df = df[["Date", COLUMN_NAME]].dropna()
df.rename(columns={COLUMN_NAME: "Value"}, inplace=True)
df.sort_values("Date", inplace=True)

# Ingest into database
create_tables()
session = Session()

for _, row in df.iterrows():
    entry = MacroSeries(
        ticker=TICKER,
        value=round(float(row["Value"]), 3),
        source=source,
        units=units,
        frequency=frequency,
        recorded_at=row["Date"].date()
    )
    session.merge(entry)

session.commit()
session.close()
print(f"✅ {TICKER} ingested successfully.")
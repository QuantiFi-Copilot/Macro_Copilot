import pandas as pd
from pathlib import Path
from macro_playbook_agent.storage.database import create_tables, Session, MacroSeries

TICKER = "CRUDEBRNT"

ROOT_DIR = Path(__file__).resolve().parents[2]
catalog_path = ROOT_DIR / "macro_playbook_agent" / "utils" / "macro_catalog.csv"
raw_path = ROOT_DIR / "macro_playbook_agent" / "data" / "raw" / "raw_crude.csv"

# Load metadata
catalog_df = pd.read_csv(catalog_path)
meta = catalog_df[catalog_df["ticker"] == TICKER]
if meta.empty:
    raise ValueError(f"Ticker {TICKER} not found in macro_catalog.csv")
source = meta["source"].values[0]
units = meta["units"].values[0]
frequency = meta["frequency"].values[0]

# Load crude CSV
df = pd.read_csv(raw_path)
df["Date"] = pd.to_datetime(df["Date"], errors="coerce", infer_datetime_format=True)
df = df[["Date", "Price"]].dropna()
df.sort_values("Date", inplace=True)

# Ingest
create_tables()
session = Session()

for _, row in df.iterrows():
    entry = MacroSeries(
        ticker=TICKER,
        value=round(float(row["Price"]), 2),
        source=source,
        units=units,
        frequency=frequency,
        recorded_at=row["Date"].date()
    )
    session.merge(entry)

session.commit()
session.close()

print(f"✅ {TICKER} ingested successfully.")
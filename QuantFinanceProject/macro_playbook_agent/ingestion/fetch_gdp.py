import sys
from pathlib import Path
import pandas as pd
from datetime import datetime

# 🔧 Set project root
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

from macro_playbook_agent.storage.database import create_tables, Session, MacroSeries

# 🔎 Metadata config
TICKER = "INGDP_NOMINAL"
catalog_path = ROOT_DIR / "macro_playbook_agent" / "utils" / "macro_catalog.csv"
catalog_df = pd.read_csv(catalog_path)

meta = catalog_df[catalog_df["ticker"] == TICKER]
if meta.empty:
    raise ValueError(f"Ticker {TICKER} not found in macro_catalog.csv")

source = meta["source"].values[0]
units = meta["units"].values[0]
frequency = meta["frequency"].values[0]

# 📄 Load GDP data
raw_path = ROOT_DIR / "macro_playbook_agent" / "data" / "raw" / "raw_gdp.csv"
df = pd.read_csv(raw_path, parse_dates=["Date"])
df.sort_values("Date", inplace=True)

# 💾 Ingest to DB
create_tables()
session = Session()

for _, row in df.iterrows():
    if pd.notnull(row["GDPIN"]):  # Make sure "GDPIN" is correct
        entry = MacroSeries(
            ticker=TICKER,
            value=round(float(row["GDPIN"]), 2),
            source=source,
            units=units,
            frequency=frequency,
            recorded_at=row["Date"].date()
        )
        session.merge(entry)

session.commit()
session.close()

print(f"✅ {TICKER} ingested successfully.")
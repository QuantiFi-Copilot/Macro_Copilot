import sys
import os
import pandas as pd
from datetime import datetime
from pathlib import Path

# 🔧 Add root project directory to sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

from macro_playbook_agent.storage.database import create_tables, Session, MacroSeries

# 📌 Ticker setup
TICKER = "INPFCE"

import pandas as pd

# 📋 Load metadata from macro_catalog.csv
catalog_path = ROOT_DIR / "macro_playbook_agent" / "utils" / "macro_catalog.csv"
catalog_df = pd.read_csv(catalog_path)

meta = catalog_df[catalog_df["ticker"] == TICKER]
if meta.empty:
    raise ValueError(f"Ticker {TICKER} not found in macro_catalog.csv")

source = meta["source"].values[0]
units = meta["units"].values[0]
frequency = meta["frequency"].values[0]

# 📄 Load PFCE data
raw_path = ROOT_DIR / "macro_playbook_agent" / "data" / "raw" / "raw_pfce.csv"
df = pd.read_csv(raw_path)
df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%y")
df.sort_values("Date", inplace=True)

# 💾 Set up DB
create_tables()
session = Session()

# 🚀 Ingest quarterly PFCE data
for _, row in df.iterrows():
    if pd.notnull(row["RPRCIN"]):
        entry = MacroSeries(
            ticker=TICKER,
            value=round(float(row["RPRCIN"]), 2),
            source=source,
            units=units,
            frequency=frequency,
            recorded_at=row["Date"].date()
        )
        session.merge(entry)

session.commit()
session.close()

print(f"✅ {TICKER} ingested successfully.")

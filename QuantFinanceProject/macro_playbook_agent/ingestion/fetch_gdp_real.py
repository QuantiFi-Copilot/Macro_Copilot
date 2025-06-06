import sys
import os
import pandas as pd
from datetime import datetime
from pathlib import Path

# 🔧 Add root project directory to sys.path
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

from macro_playbook_agent.storage.database import create_tables, Session, MacroSeries

TICKER = "INGDPABS"
catalog_path = ROOT_DIR / "macro_playbook_agent" / "utils" / "macro_catalog.csv"
catalog_df = pd.read_csv(str(catalog_path))

# Lookup metadata
meta = catalog_df[catalog_df["ticker"] == TICKER]
if meta.empty:
    raise ValueError(f"Ticker {TICKER} not found in macro_catalog.csv")

source = meta["source"].values[0]
units = meta["units"].values[0]
frequency = meta["frequency"].values[0]

# 📄 Load real GDP (constant prices, 10 million INR)
raw_path = ROOT_DIR / "macro_playbook_agent" / "data" / "raw" / "raw_gdp_real.csv"
df = pd.read_csv(str(raw_path), parse_dates=["Date"])
df.sort_values("Date", inplace=True)

# 🧮 Convert 10 million INR → INR Cr
df["GDP_Cr"] = df["MOS_GDP.GDP_REAL.Q.IN"] / 10

# 💾 Set up DB
create_tables()
session = Session()

# 🚀 Ingest real GDP (constant prices)
for _, row in df.iterrows():
    entry = MacroSeries(
        ticker=TICKER,
        value=round(float(row["GDP_Cr"]), 2),
        source=source,
        units=units,
        frequency=frequency,
        recorded_at=row["Date"].date()
    )
    session.merge(entry)

session.commit()
session.close()

print(f"✅ {TICKER} ingested successfully.")
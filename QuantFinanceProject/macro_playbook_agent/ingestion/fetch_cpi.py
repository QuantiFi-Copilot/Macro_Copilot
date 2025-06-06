import sys
import os
import pandas as pd
from datetime import datetime
from pathlib import Path

# 🔧 Add root project directory to sys.path so we can import database.py
ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT_DIR))

from macro_playbook_agent.storage.database import create_tables, Session, MacroSeries

TICKER = "CPIIN"
catalog_path = ROOT_DIR / "macro_playbook_agent" / "utils" / "macro_catalog.csv"
catalog_df = pd.read_csv(catalog_path)

# Lookup metadata
meta = catalog_df[catalog_df["ticker"] == TICKER]
if meta.empty:
    raise ValueError(f"Ticker {TICKER} not found in macro_catalog.csv")

source = meta["source"].values[0]
units = meta["units"].values[0]
frequency = meta["frequency"].values[0]

# Load data
df = pd.read_csv(ROOT_DIR / "macro_playbook_agent" / "data" / "raw" / "raw_cpi.csv", index_col='Date', parse_dates=['Date'])

create_tables()
session = Session()

for date, row in df.iterrows():
    value = float(row[TICKER])
    entry = MacroSeries(
        ticker=TICKER,
        value=value,
        source=source,
        units=units,
        frequency=frequency,
        recorded_at=date.date()
    )
    session.merge(entry)

session.commit()
session.close()

print(f"✅ {TICKER} ingested successfully from CSV.")
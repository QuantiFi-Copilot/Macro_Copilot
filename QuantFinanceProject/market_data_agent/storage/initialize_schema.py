# market_data_agent/db/create_schema.py

import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv, find_dotenv

# 1) Load .env and get DB URL
dotenv_path = find_dotenv()
load_dotenv(dotenv_path)
DATABASE_URL = "postgresql://quantuser:myStrongPass@tsdb:5432/quantdata"

# 2) Create engine
engine = create_engine(DATABASE_URL)

def run_schema():
    # 3) Read the SQL file
    here = os.path.dirname(__file__)
    sql_path = os.path.join(here, "schema.sql")
    with open(sql_path, "r") as f:
        ddl = f.read()

    # 4) Execute all commands
    with engine.begin() as conn:
        conn.execute(text(ddl))
    print("✅ market_data schema created or verified.")

if __name__ == "__main__":
    run_schema()

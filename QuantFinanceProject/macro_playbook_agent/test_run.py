from sqlalchemy import create_engine
import pandas as pd

engine = create_engine("postgresql://localhost:5432/macro_agent")
for ticker in ["INWPI","GOLDUSD","USDINR","INGDPABS","INREPO","MBIN","INPFCE","INGDPGR_DIRECT","IN10Y_YF"]:
    count = pd.read_sql(f"SELECT count(*) AS c FROM macro_series WHERE ticker = '{ticker}'", engine).iloc[0,0]
    print(f"{ticker:12} → {count} rows")

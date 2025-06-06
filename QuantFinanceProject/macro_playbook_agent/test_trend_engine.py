import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from pathlib import Path
from macro_playbook_agent.processing.trend_engine import run_trend_engine
from macro_playbook_agent.signals import transforms

# adjust to your local paths/URI as needed
signal_catalog_path = Path("macro_playbook_agent/utils/signal_catalog.csv")
db_uri               = "postgresql://localhost:5432/macro_agent"

def fetch_series(engine, ticker):
    query = f"""
        SELECT recorded_at, value
        FROM macro_series
        WHERE ticker = '{ticker}'
        ORDER BY recorded_at
    """
    df = pd.read_sql(query, engine, parse_dates=["recorded_at"])
    return df.set_index("recorded_at").sort_index()

def compute_manual_transform(series: pd.Series, transform_type: str, window: int) -> pd.Series:
    fn_map = {
        'yoy': transforms.compute_yoy,
        'ma': transforms.compute_ma,
        'delta': transforms.compute_delta,
        'zscore': transforms.compute_zscore
    }
    return fn_map[transform_type](series, window)

def spot_check_transforms():
    engine       = create_engine(db_uri)
    catalog      = pd.read_csv(signal_catalog_path)
    engine_df    = run_trend_engine(signal_catalog_path, db_uri).set_index("recorded_at")

    for _, row in catalog.iterrows():
        ticker    = row["ticker"]
        sig_name  = row["signal_name"]
        t_type    = row["transformation_type"]
        window    = int(row["window"])
        freq      = row["analysis_freq"]
        agg       = row["agg_method"]
        lag_days  = int(row["effective_lag_days"])

        print(f"\n=== Validating {sig_name} ({t_type}) ===")
        raw_df = fetch_series(engine, ticker)
        if raw_df.empty:
            print("No raw data found.")
            continue

        # --- MANUAL COMPUTATION ---
        if t_type == "volatility":
            # 1) log returns on raw daily series
            log_ret = np.log(raw_df["value"]).diff()
            # 2) rolling std over window days
            vol_raw = log_ret.rolling(window=window).std()
            # 3) annualize
            vol_ann = vol_raw * np.sqrt(252)
            # 4) pick last-of-month
            manual = vol_ann.resample("ME").last()
            # 5) align to period start (month-begin)
            manual.index = manual.index.to_period("M").to_timestamp()
            # 6) apply publication lag
            manual = manual.shift(freq=f"{lag_days}D")

        else:
            # 1) bucket to analysis freq
            series = raw_df["value"].resample(freq).agg(agg)
            # 2) lag
            series = series.shift(freq=f"{lag_days}D")
            # 3) apply transform
            manual = compute_manual_transform(series, t_type, window)

        # --- ENGINE OUTPUT ---
        engine_sig = engine_df[sig_name]

        # --- ALIGN & COMPARE ---
        compare = pd.concat([manual.rename("manual"), engine_sig.rename("engine")], axis=1).dropna()
        if compare.empty:
            print("⚠️ No overlapping data to compare.")
            continue

        compare["abs_diff"] = (compare["manual"] - compare["engine"]).abs()
        print(compare.head())
        print(f"Max absolute difference: {compare['abs_diff'].max():.6f}")

def main():
    print("=== SPOT-CHECK TRANSFORM ACCURACY (INCLUDING REALIZED VOL) ===")
    spot_check_transforms()

if __name__ == "__main__":
    main()

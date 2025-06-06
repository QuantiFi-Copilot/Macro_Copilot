import numpy as np
import pandas as pd
import sqlalchemy
from pathlib import Path
from macro_playbook_agent.signals import transforms

def load_signal_catalog(path: Path) -> pd.DataFrame:
    """Load the enriched signal catalog with freq, agg, and lag metadata."""
    return pd.read_csv(path)

def fetch_macro_series(engine, ticker: str) -> pd.DataFrame:
    """Fetch raw time series for a given ticker from Postgres."""
    query = f"""
        SELECT recorded_at, value
        FROM macro_series
        WHERE ticker = '{ticker}'
        ORDER BY recorded_at
    """
    df = pd.read_sql(query, engine, parse_dates=['recorded_at'])
    return (
        df.drop_duplicates(subset='recorded_at')
          .set_index('recorded_at')
          .sort_index()
    )

def apply_transformation(series: pd.Series, transform_type: str, window: int) -> pd.Series:
    """Route to the correct transform function for YoY, MA, Delta, Z-score."""
    fn_map = {
        'yoy': transforms.compute_yoy,
        'ma': transforms.compute_ma,
        'delta': transforms.compute_delta,
        'zscore': transforms.compute_zscore
    }
    if transform_type not in fn_map:
        raise ValueError(f"Unknown transformation: {transform_type}")
    return fn_map[transform_type](series, window)

def run_trend_engine(signal_catalog_path: Path, db_uri: str) -> pd.DataFrame:
    """
    Build the full signal matrix:
      - Resamples each series to its analysis frequency
      - Applies publication lag
      - Computes transforms including annualized log-return volatility
    """
    engine  = sqlalchemy.create_engine(db_uri)
    catalog = load_signal_catalog(signal_catalog_path)

    signal_dict = {}
    for _, row in catalog.iterrows():
        ticker    = row['ticker']
        sig_name  = row['signal_name']
        t_type    = row['transformation_type']
        window    = int(row['window'])
        freq      = row['analysis_freq']      # e.g. 'ME', 'Q'
        agg       = row['agg_method']         # e.g. 'mean', 'last'
        lag_days  = int(row['effective_lag_days'])

        raw_df = fetch_macro_series(engine, ticker)
        if raw_df.empty:
            signal_dict[sig_name] = pd.Series(dtype=float)
            continue

        # --- VOLATILITY SPECIAL CASE: annualized log-return vol ---
        if t_type == 'volatility':
            # 1) log returns
            log_ret = np.log(raw_df['value']).diff()
            # 2) rolling std over window days
            vol_raw = log_ret.rolling(window=window).std()
            # 3) annualize (√252)
            vol_ann = vol_raw * np.sqrt(252)
            # 4) take last vol of each month
            vol_monthly = vol_ann.resample('M').last()
            # 5) align index to month-start
            vol_monthly.index = vol_monthly.index.to_period('M').to_timestamp()
            # 6) apply publication lag
            sig = vol_monthly.shift(freq=f"{lag_days}D")
            signal_dict[sig_name] = sig
            continue

        # --- GENERIC TRANSFORMS (yoy, ma, delta, zscore) ---
        # 1) bucket to analysis frequency
        series = raw_df['value'].resample(freq).agg(agg)
        # 2) shift by publication lag
        series = series.shift(freq=f"{lag_days}D")
        # 3) apply the transform
        sig = apply_transformation(series, t_type, window)
        signal_dict[sig_name] = sig

    # 4) concatenate all signals and forward-fill missing
    output = pd.concat(signal_dict, axis=1).ffill()
    output.index.name = 'recorded_at'
    return output.reset_index()

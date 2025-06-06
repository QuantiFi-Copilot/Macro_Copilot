import pandas as pd

def compute_yoy(series: pd.Series, periods: int = 12) -> pd.Series:
    """
    Compute year-over-year percent change.
    """
    return (series / series.shift(periods)) - 1

def compute_ma(series: pd.Series, window: int = 3) -> pd.Series:
    """
    Compute simple moving average.
    """
    return series.rolling(window=window).mean()

def compute_delta(series: pd.Series, periods: int = 1) -> pd.Series:
    """
    Compute change over time (e.g., month-on-month or quarter-on-quarter delta).
    """
    return series - series.shift(periods)

def compute_zscore(series: pd.Series, window: int = 36) -> pd.Series:
    """
    Compute rolling z-score (standardized deviation from rolling mean).
    """
    mean = series.rolling(window=window).mean()
    std = series.rolling(window=window).std()
    return (series - mean) / std
def compute_volatility(series: pd.Series, window: int = 30) -> pd.Series:
    return series.pct_change().rolling(window).std()
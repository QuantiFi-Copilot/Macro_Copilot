"""
spreads.py — Generic two-point spread analytics
================================================

Pure-math helpers for computing spreads between two rate series with a
fixed-window rolling z-score.  Used by sovereign_bonds curve_spread and
cross_market_spread, and the forthcoming OIS equivalents.

These helpers are domain-agnostic: they operate on pandas objects and
return pandas objects.  Domain tools are responsible for fetching data,
validating inputs, and assembling the final output schema.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

import pandas as pd


# Fixed 1-year rolling window for z-score calculation (trading days).
# Kept constant regardless of the display lookback a user asks for, so
# z-scores are comparable across queries.
Z_SCORE_WINDOW: int = 252
Z_SCORE_MIN_PERIODS: int = 60


def safe_float(value: Any) -> Optional[float]:
    """Convert a value to a Python float rounded to 4 decimals.
    Returns ``None`` for None, NaN, or un-castable values."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


def pivot_and_align_tenors(
    raw_df: pd.DataFrame,
    required_tenors: Iterable[str],
    *,
    date_col: str = "trade_date",
    key_col: str = "tenor",
    value_col: str = "field_value",
    ffill_limit: int = 5,
) -> pd.DataFrame:
    """
    Pivot a long-format rate DataFrame to wide (date × tenor) and
    forward-fill across holiday gaps.

    Steps
    -----
    1. Coerce ``date_col`` to datetime, ``value_col`` to numeric.
    2. Drop duplicate ``(date, key)`` pairs, keeping the last.
    3. Pivot to ``(date × key)`` with ``value_col``.
    4. Sort by date index.
    5. Forward-fill up to ``ffill_limit`` consecutive missing values
       (bridges holiday mismatches across related curves).
    6. Drop rows where any of ``required_tenors`` is still NaN.

    Returns
    -------
    pd.DataFrame
        Wide-format DataFrame (possibly empty if no overlapping dates
        remain after alignment).
    """
    df = raw_df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.drop_duplicates(subset=[date_col, key_col], keep="last")

    wide = df.pivot(index=date_col, columns=key_col, values=value_col)
    wide = wide.sort_index()
    wide = wide.ffill(limit=ffill_limit)
    wide = wide.dropna(subset=list(required_tenors))
    return wide


def compute_spread_bps(
    wide: pd.DataFrame,
    *,
    minuend_col: str,
    subtrahend_col: str,
) -> pd.Series:
    """
    Compute ``(minuend - subtrahend) * 100`` in basis points, rounded to
    2 decimal places.

    Inputs are assumed to be percentage-scale rates (e.g. 4.25 = 4.25%).
    """
    return ((wide[minuend_col] - wide[subtrahend_col]) * 100).round(2)


def rolling_zscore(
    series: pd.Series,
    *,
    window: int = Z_SCORE_WINDOW,
    min_periods: int = Z_SCORE_MIN_PERIODS,
    round_decimals: int = 4,
) -> pd.Series:
    """
    Rolling z-score = ``(x - rolling_mean) / rolling_std``.

    Uses pandas' default sample std (``ddof=1``).  Rounded to
    ``round_decimals``.
    """
    rolling_mean = series.rolling(window=window, min_periods=min_periods).mean()
    rolling_std = series.rolling(window=window, min_periods=min_periods).std()
    return ((series - rolling_mean) / rolling_std).round(round_decimals)

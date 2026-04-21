"""
levels.py — Generic level-stat analytics for rates tools
=========================================================

Small composable primitives shared across any "where is X sitting" style
tool: yield levels, OIS rate levels, cross-market spread levels, and the
per-instrument metric computation inside scanner-style tools.

Each helper is intentionally narrow so tools can assemble only the
pieces they need — no monolithic ``compute_level_metrics`` composite
(that would force a least-common-denominator schema and couple all
callers to the same decimal precision and field names).

Convention: caller supplies a clean ``pd.Series`` indexed by date, in
percentage scale (e.g. 4.25 = 4.25%).  Helpers return None-safe scalars
or composite dicts; they never raise on missing data.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

import pandas as pd

from shared.analytics.spreads import safe_float


# ============================================================================
# BPS CHANGE
# ============================================================================

def bps_change(current: Any, previous: Any) -> Optional[float]:
    """Compute change in basis points between two percentage-scale values.

    ``(current - previous) * 100``, rounded to 2 decimal places.
    Returns None if either value is None or NaN.

    Convention: inputs are percent (e.g. 4.25 = 4.25%), output is bps
    (1bp = 0.01%).  For already-bps series (spread time series), use
    simple subtraction instead — this helper always multiplies by 100.
    """
    if current is None or previous is None:
        return None
    try:
        cur = float(current)
        prev = float(previous)
        if math.isnan(cur) or math.isnan(prev):
            return None
        return round((cur - prev) * 100, 2)
    except (TypeError, ValueError):
        return None


def delta_bps(current: Any, previous: Any) -> Optional[float]:
    """Subtraction in basis points when both inputs are already in bps.

    Used for spread series (where values are already bps).  Returns
    ``current - previous`` rounded to 2 decimals; None-safe.
    """
    if current is None or previous is None:
        return None
    try:
        cur = float(current)
        prev = float(previous)
        if math.isnan(cur) or math.isnan(prev):
            return None
        return round(cur - prev, 2)
    except (TypeError, ValueError):
        return None


# ============================================================================
# PERIOD CHANGES
# ============================================================================

# Canonical iloc offsets used across all rates level tools.  After ffill
# the series is approximately trading-daily, so these correspond to
# roughly 1 trading day, 5 trading days, and ~1 calendar month.
DEFAULT_PERIOD_OFFSETS: dict[str, int] = {
    "daily": 2,
    "weekly": 6,
    "monthly": 22,
}


def period_changes(
    series: pd.Series,
    *,
    offsets: Mapping[str, int] = DEFAULT_PERIOD_OFFSETS,
    already_bps: bool = False,
) -> dict[str, Optional[float]]:
    """Compute change in bps over each (label, iloc-offset) pair.

    Parameters
    ----------
    series : pd.Series
        Clean, sorted, ffilled series.  Indexed by date.
    offsets : Mapping[str, int]
        Maps a label (e.g. ``"daily"``) to an iloc offset (e.g. ``2``
        for "current vs the observation at iloc[-2]").  The default
        mirrors the canonical daily/weekly/monthly convention used by
        every rates tool.
    already_bps : bool
        False (default) when ``series`` is in percent — changes are
        multiplied by 100 to produce bps.  True when ``series`` is
        already in bps (e.g. a spread series) — changes are a plain
        subtraction.

    Returns
    -------
    dict[str, Optional[float]]
        One entry per offset label.  Value is None when the series is
        too short for that offset.
    """
    if len(series) == 0:
        return {label: None for label in offsets}

    current = series.iloc[-1]
    change_fn = delta_bps if already_bps else bps_change

    out: dict[str, Optional[float]] = {}
    for label, offset in offsets.items():
        if len(series) >= offset:
            out[label] = change_fn(current, series.iloc[-offset])
        else:
            out[label] = None
    return out


# ============================================================================
# TRAILING HIGH / LOW / PERCENTILE
# ============================================================================

def trailing_high_low_percentile(
    series: pd.Series,
    *,
    window: int = 252,
    decimals: int = 4,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Trailing ``window``-day high, low, and percentile of the latest
    observation within the window.

    ``percentile = (current - low) / (high - low) * 100``, rounded to 1
    decimal.  Returns (None, None, None) when the series is empty or
    all values are NaN.  Percentile is None when high == low (flat
    window) to avoid divide-by-zero.

    ``decimals`` controls the rounding of ``high`` and ``low`` — match
    the caller's convention (4 for yields in %, 2 for bps quantities).
    """
    if len(series) == 0:
        return None, None, None

    trailing = series.iloc[-window:] if len(series) >= window else series
    high = safe_float(trailing.max(), decimals=decimals)
    low = safe_float(trailing.min(), decimals=decimals)

    current_raw = series.iloc[-1]
    try:
        current = float(current_raw)
        if math.isnan(current):
            current = None
    except (TypeError, ValueError):
        current = None

    if high is None or low is None or current is None or high == low:
        percentile = None
    else:
        percentile = round((current - low) / (high - low) * 100, 1)

    return high, low, percentile


# ============================================================================
# SINGLE-SERIES CLEAN
# ============================================================================

def clean_single_series(
    raw_df: pd.DataFrame,
    *,
    date_col: str = "trade_date",
    value_col: str = "field_value",
    ffill_limit: int = 5,
) -> pd.DataFrame:
    """Prepare a single-series long-format DataFrame for level analytics.

    Steps (order matters, matches the idiom every non-spread tool
    independently re-implemented):

      1. Coerce ``date_col`` to datetime
      2. Coerce ``value_col`` to numeric (``errors="coerce"``)
      3. Drop rows where value became NaN after coercion
      4. Drop duplicate dates, keeping the last observation
      5. Set ``date_col`` as index, sort ascending
      6. Forward-fill up to ``ffill_limit`` consecutive missing values
         to bridge public-holiday gaps

    Returns a new DataFrame (does not mutate the input).  Callers
    typically chain ``df[value_col]`` to pull the clean Series.
    """
    df = raw_df.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    df = df.dropna(subset=[value_col])
    df = df.drop_duplicates(subset=[date_col], keep="last")
    df = df.set_index(date_col).sort_index()
    df = df.ffill(limit=ffill_limit)
    return df

"""
spreads.py — Generic two-point spread analytics
================================================

Pure-math helpers for computing spreads between two rate series with a
fixed-window rolling z-score.  Used by sovereign_bonds curve_spread,
cross_market_spread, butterfly, scanner, and the OIS equivalents.

These helpers are domain-agnostic: they operate on pandas objects and
return pandas objects.  Domain tools are responsible for fetching data,
validating inputs, and assembling the final output schema.

Parameterisation contract (commit 2 of the tool-config refactor pilot)
----------------------------------------------------------------------
Every methodology decision encoded in this module is exposed as an
explicit kwarg with the *current* value as the default:

    rolling_zscore:   window, min_periods, ddof, round_decimals
    compute_spread_bps:    round_decimals
    pivot_and_align_tenors: ffill_limit
    safe_float:            decimals

The two module-level constants (``Z_SCORE_WINDOW``, ``Z_SCORE_MIN_PERIODS``)
are kept for backward-compat — they are the *default* values for
``rolling_zscore`` and are still imported by the existing tools that
reference them for fetch-buffer math.  In subsequent commits of the
pilot each tool will pull these values from its own ``config.yaml`` and
pass them explicitly to the primitives; until that migration lands,
inheriting the module defaults preserves the math byte-for-byte.

Backward compatibility guarantee
--------------------------------
This commit is purely additive: the only changes are NEW kwargs whose
defaults reproduce the previously-hardcoded values exactly.  No kwargs
were renamed, removed, or had their semantics changed.  Specifically:

- ``compute_spread_bps`` had ``.round(2)`` hardcoded; the new
  ``round_decimals: int = 2`` kwarg keeps the default at 2.
- ``rolling_zscore`` previously called ``rolling(...).std()`` with
  pandas' default ``ddof=1``; the new ``ddof: int = 1`` kwarg keeps
  the default at 1.

Both changes were verified to produce bit-identical output against
synthetic series before this commit landed.
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional

import pandas as pd


__all__ = [
    "Z_SCORE_WINDOW",
    "Z_SCORE_MIN_PERIODS",
    "compute_spread_bps",
    "pivot_and_align_tenors",
    "rolling_zscore",
    "safe_float",
]


# ---------------------------------------------------------------------------
# Module-level defaults
# ---------------------------------------------------------------------------
# These are the *default values* for the corresponding ``rolling_zscore``
# kwargs.  They are exposed at module level because several tools also
# reference them for fetch-buffer math (e.g. ``int(Z_SCORE_WINDOW * 1.5)``
# to size the calendar-day backfill window).  Once those tools migrate
# to config-driven loading, they will read the equivalent value from
# their own ``config.yaml`` and pass it explicitly to ``rolling_zscore``.
# Until the migration is complete, callers that pass no override
# inherit these defaults so the math is unchanged.
Z_SCORE_WINDOW: int = 252
Z_SCORE_MIN_PERIODS: int = 60


# ===========================================================================
# SAFE FLOAT
# ===========================================================================

def safe_float(value: Any, decimals: int = 4) -> Optional[float]:
    """Convert a value to a Python float rounded to ``decimals`` places.

    Returns ``None`` for None, NaN, or un-castable values.

    ``decimals`` defaults to 4 to preserve backwards compatibility with
    the original single-argument form used by the curve_spread refactor.
    Callers that historically rounded to 2 decimals (bps quantities,
    percentiles, etc.) should pass ``decimals=2`` explicitly — don't rely
    on the shared default matching a tool-local choice that was tighter.
    """
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, decimals)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# PIVOT + ALIGN
# ===========================================================================

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

    Parameters
    ----------
    ffill_limit : int
        Maximum consecutive trading days of missing data to bridge via
        forward-fill.  Default 5 (one trading week) — long enough to
        cover most holiday gaps, short enough that a stale series is
        flagged as missing rather than silently extrapolated.

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


# ===========================================================================
# SPREAD IN BPS
# ===========================================================================

def compute_spread_bps(
    wide: pd.DataFrame,
    *,
    minuend_col: str,
    subtrahend_col: str,
    round_decimals: int = 2,
) -> pd.Series:
    """
    Compute ``(minuend - subtrahend) * 100`` in basis points.

    Inputs are assumed to be percentage-scale rates (e.g. 4.25 = 4.25%).

    Parameters
    ----------
    round_decimals : int
        Number of decimal places to round the bps output to.  Default
        2 (one-hundredth of a basis point) matches the precision
        previously hardcoded into this primitive; tools should override
        when they need different precision and document it in their
        ``config.yaml``.
    """
    return ((wide[minuend_col] - wide[subtrahend_col]) * 100).round(round_decimals)


# ===========================================================================
# ROLLING Z-SCORE
# ===========================================================================

def rolling_zscore(
    series: pd.Series,
    *,
    window: int = Z_SCORE_WINDOW,
    min_periods: int = Z_SCORE_MIN_PERIODS,
    ddof: int = 1,
    round_decimals: int = 4,
) -> pd.Series:
    """
    Rolling z-score = ``(x - rolling_mean) / rolling_std``.

    Parameters
    ----------
    window : int
        Rolling-window length in observations (typically trading days).
        Default 252 — 1 trading year.
    min_periods : int
        Minimum observations required before the rolling mean / std are
        emitted.  Default 60 (~3 months) — enough that the stat is not
        meaningfully degraded by sample size on the first emitted row.
    ddof : int
        Delta degrees of freedom for the rolling std.  Default 1
        (sample standard deviation), matching pandas' own default and
        the convention the codebase has used since inception.  Set to
        0 for population std; mixing 0 and 1 across tools would be a
        methodology drift the consistency lint should catch.
    round_decimals : int
        Number of decimal places to round the z-score to.  Default 4.
    """
    rolling_mean = series.rolling(window=window, min_periods=min_periods).mean()
    rolling_std = series.rolling(window=window, min_periods=min_periods).std(ddof=ddof)
    return ((series - rolling_mean) / rolling_std).round(round_decimals)

"""
yield_levels.py — Deterministic Yield Level Monitor
=====================================================

Returns the current yield, period changes (daily/weekly/monthly in bps),
a 252-day rolling z-score, and deterministic context (1-year high, low,
percentile) for a single point on a sovereign yield curve.

This is the foundational building block that almost every other tool
chains with.  A PM asking "where's the 10Y?" or "how much have 2Y Gilts
sold off this week?" hits this tool.  The spread tool tells you the
relationship between two points; this tool tells you about the point
itself.

Data flow
---------
1.  **Fetch** — single-tenor query against the enriched view.
2.  **Clean** — sort, dedup, forward-fill holiday gaps.
3.  **Math** — daily/weekly/monthly change in bps, 252-day rolling
    z-score, trailing high/low/percentile.
4.  **Return** — ``YieldLevelOutput`` with ``current_metrics`` only.
    No time-series is sent to the LLM.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    YieldLevelInput,
    YieldLevelMetrics,
    YieldLevelOutput,
)


# ============================================================================
# PRIVATE HELPERS
# ============================================================================

_FETCH_SQL = text("""
    SELECT
        trade_date,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family = :curve_family
      AND tenor        = :tenor
      AND field_name   = :field_name
      AND trade_date  >= :start_date
    ORDER BY trade_date
""")


def _fetch_raw(
    engine: Engine,
    curve_family: str,
    tenor: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """Run the parameterized query and return a DataFrame."""
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_SQL,
            {
                "curve_family": curve_family,
                "tenor": tenor,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


def _safe_float(value: Any, decimals: int = 4) -> Optional[float]:
    """Convert to Python float, returning None for NaN / None."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, decimals)
    except (TypeError, ValueError):
        return None


def _bps_change(current: float, previous: Any) -> Optional[float]:
    """Compute change in basis points (1bp = 0.01%).  Returns None if
    either value is missing."""
    if previous is None:
        return None
    try:
        prev = float(previous)
        if math.isnan(prev):
            return None
        return round((current - prev) * 100, 2)
    except (TypeError, ValueError):
        return None


# ============================================================================
# PUBLIC API
# ============================================================================

def get_yield_levels(
    engine: Engine,
    params: YieldLevelInput,
) -> Dict[str, Any]:
    """
    Return current yield level, period changes, z-score, and
    deterministic context (high/low/percentile) for a single curve
    point.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : YieldLevelInput
        Validated input with curve_family, tenor, lookback_days,
        field_name.

    Returns
    -------
    dict
        Serialized ``YieldLevelOutput``.  On failure, returns a dict
        with an ``"error"`` key.
    """

    # ------------------------------------------------------------------
    # 1. Date window — same buffering pattern as curve_spread.py
    # ------------------------------------------------------------------
    Z_SCORE_WINDOW: int = 252
    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = _fetch_raw(
        engine=engine,
        curve_family=params.curve_family,
        tenor=params.tenor,
        field_name=params.field_name,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenor='{params.tenor}', field='{params.field_name}' "
                f"since {start_date.isoformat()}.  "
                "Please verify the curve family and tenor exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Clean
    # ------------------------------------------------------------------
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.dropna(subset=["field_value"])
    raw_df = raw_df.drop_duplicates(subset=["trade_date"], keep="last")
    raw_df = raw_df.set_index("trade_date").sort_index()

    # Forward-fill to bridge holiday gaps (max 5 business days).
    raw_df = raw_df.ffill(limit=5)

    if raw_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for "
                f"'{params.curve_family}' {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 4. Math
    # ------------------------------------------------------------------
    yields = raw_df["field_value"]
    current_yield = float(yields.iloc[-1])

    # --- Period changes (bps) ---
    daily_change = _bps_change(
        current_yield,
        yields.iloc[-2] if len(yields) >= 2 else None,
    )
    weekly_change = _bps_change(
        current_yield,
        yields.iloc[-6] if len(yields) >= 6 else None,
    )
    monthly_change = _bps_change(
        current_yield,
        yields.iloc[-22] if len(yields) >= 22 else None,
    )

    # --- Rolling z-score (252-day window) ---
    rolling_mean = yields.rolling(window=Z_SCORE_WINDOW, min_periods=60).mean()
    rolling_std = yields.rolling(window=Z_SCORE_WINDOW, min_periods=60).std()
    z_series = (yields - rolling_mean) / rolling_std
    current_z = _safe_float(z_series.iloc[-1])

    # --- Trailing high / low / percentile (252 trading days) ---
    # Use the actual trailing window, not the full buffer.
    trailing = yields.iloc[-Z_SCORE_WINDOW:] if len(yields) >= Z_SCORE_WINDOW else yields
    high_252 = _safe_float(trailing.max())
    low_252 = _safe_float(trailing.min())

    if high_252 is not None and low_252 is not None and high_252 != low_252:
        percentile = round(
            (current_yield - low_252) / (high_252 - low_252) * 100, 1
        )
    else:
        percentile = None

    # --- Observation count (in the display window) ---
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_yields = yields.loc[yields.index >= cutoff]
    obs_count = len(display_yields)

    if obs_count == 0:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family}' {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 5. Build output
    # ------------------------------------------------------------------
    metrics = YieldLevelMetrics(
        as_of_date=yields.index[-1].strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        tenor=params.tenor,
        current_yield_pct=_safe_float(current_yield),
        daily_change_bps=daily_change,
        weekly_change_bps=weekly_change,
        monthly_change_bps=monthly_change,
        z_score=current_z,
        high_252d_pct=high_252,
        low_252d_pct=low_252,
        percentile_252d=percentile,
        observation_count=obs_count,
    )

    output = YieldLevelOutput(current_metrics=metrics)
    return output.model_dump()

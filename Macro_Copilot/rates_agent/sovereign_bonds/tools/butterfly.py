"""
butterfly.py — Deterministic Butterfly / Curvature Calculator
==============================================================

Computes the 3-point curvature (butterfly) on a single sovereign yield
curve.  The butterfly measures how "rich" or "cheap" the belly is relative
to the wings.

Formula:  butterfly_bps = 2 × belly − short − long   (in basis points)

A *positive* butterfly means the belly is cheap (yielding more than the
linear interpolation of the wings).  A *negative* butterfly means the belly
is rich (yielding less — typical of a smooth curve).

This is the natural extension of the 2-point curve_spread tool.  Desks
trade 2s5s10s, 5s10s30s, and other butterflies constantly to express views
on curve *shape* without taking outright duration risk.

Data flow
---------
1.  **Fetch** — three-tenor query against the enriched view (single curve).
2.  **Pivot** — each tenor becomes a column with trade_date as index.
3.  **Fill** — forward-fill to bridge holiday gaps (max 5 days).
4.  **Math** — butterfly bps, 252-day rolling z-score, daily change,
    component 2-leg spreads for decomposition.
5.  **Return** — ``ButterflyOutput`` with ``current_metrics`` only
    (time_series withheld from LLM).
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    ButterflyInput,
    ButterflyCurrentMetrics,
    ButterflyOutput,
    ButterflyTimeSeriesRow,
)


# ============================================================================
# PRIVATE HELPERS
# ============================================================================

_FETCH_SQL = text("""
    SELECT
        trade_date,
        tenor,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family  = :curve_family
      AND tenor         IN (:short_tenor, :belly_tenor, :long_tenor)
      AND field_name    = :field_name
      AND trade_date   >= :start_date
    ORDER BY trade_date
""")


def _fetch_raw(
    engine: Engine,
    curve_family: str,
    short_tenor: str,
    belly_tenor: str,
    long_tenor: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """Run the parameterized query and return a long-format DataFrame."""
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_SQL,
            {
                "curve_family": curve_family,
                "short_tenor": short_tenor,
                "belly_tenor": belly_tenor,
                "long_tenor": long_tenor,
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
    """Compute change in basis points.  Returns None if either value is missing."""
    if previous is None:
        return None
    try:
        prev = float(previous)
        if math.isnan(prev):
            return None
        return round(current - prev, 2)
    except (TypeError, ValueError):
        return None


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_butterfly(
    engine: Engine,
    params: ButterflyInput,
) -> Dict[str, Any]:
    """
    Calculate the 3-point butterfly (curvature) on a sovereign yield curve.

    butterfly_bps = (2 × belly − short − long) × 100

    Also returns the two component 2-leg spreads for decomposition:
      - wing_short_bps = (belly − short) × 100     (front-end steepness)
      - wing_long_bps  = (long − belly) × 100      (back-end steepness)

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : ButterflyInput
        Validated input with curve_family, short_tenor, belly_tenor,
        long_tenor, lookback_days, and field_name.

    Returns
    -------
    dict
        Serialized ``ButterflyOutput``.  On failure, returns a dict
        with an ``"error"`` key.
    """

    # ------------------------------------------------------------------
    # 1. Date window
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
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        field_name=params.field_name,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenors=['{params.short_tenor}', '{params.belly_tenor}', "
                f"'{params.long_tenor}'], field='{params.field_name}' "
                f"since {start_date.isoformat()}.  "
                "Please verify the curve family and tenors exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate all three tenors are present
    # ------------------------------------------------------------------
    available_tenors = set(raw_df["tenor"].unique())
    required = {params.short_tenor, params.belly_tenor, params.long_tenor}
    missing = required - available_tenors
    if missing:
        return {
            "error": (
                f"Missing tenor data for {missing} in curve_family='{params.curve_family}'.  "
                f"Available tenors in the query window: {sorted(available_tenors)}."
            )
        }

    # ------------------------------------------------------------------
    # 4. Pivot → wide format (date × tenor)
    # ------------------------------------------------------------------
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "tenor"], keep="last"
    )

    wide = raw_df.pivot(index="trade_date", columns="tenor", values="field_value")
    wide = wide.sort_index()

    # Forward-fill to bridge holiday gaps (max 5 business days).
    wide = wide.ffill(limit=5)
    wide = wide.dropna(subset=[params.short_tenor, params.belly_tenor, params.long_tenor])

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for '{params.short_tenor}', "
                f"'{params.belly_tenor}', and '{params.long_tenor}' on "
                f"'{params.curve_family}', no overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Calculate butterfly and component spreads
    # ------------------------------------------------------------------
    s = wide[params.short_tenor]
    b = wide[params.belly_tenor]
    l = wide[params.long_tenor]

    # Butterfly = 2 × belly − short − long (in bps)
    wide["butterfly_bps"] = ((2 * b - s - l) * 100).round(2)

    # Component 2-leg spreads for decomposition
    wide["wing_short_bps"] = ((b - s) * 100).round(2)  # belly − short
    wide["wing_long_bps"] = ((l - b) * 100).round(2)   # long − belly

    # Rolling z-score on the butterfly
    rolling_mean = wide["butterfly_bps"].rolling(window=Z_SCORE_WINDOW, min_periods=60).mean()
    rolling_std = wide["butterfly_bps"].rolling(window=Z_SCORE_WINDOW, min_periods=60).std()
    wide["z_score"] = ((wide["butterfly_bps"] - rolling_mean) / rolling_std).round(4)

    # ------------------------------------------------------------------
    # 6. Trim to requested lookback
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family}' {params.short_tenor}/{params.belly_tenor}/"
                f"{params.long_tenor} butterfly."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    bfly_series = display_df["butterfly_bps"]

    current_butterfly = _safe_float(latest["butterfly_bps"], 2)

    # Daily change
    daily_change = _bps_change(
        latest["butterfly_bps"],
        bfly_series.iloc[-2] if len(bfly_series) >= 2 else None,
    )

    # Trailing high / low / percentile
    trailing = bfly_series.iloc[-Z_SCORE_WINDOW:] if len(bfly_series) >= Z_SCORE_WINDOW else bfly_series
    high_252 = _safe_float(trailing.max(), 2)
    low_252 = _safe_float(trailing.min(), 2)

    if high_252 is not None and low_252 is not None and high_252 != low_252:
        percentile = round(
            (float(latest["butterfly_bps"]) - low_252) / (high_252 - low_252) * 100, 1
        )
    else:
        percentile = None

    # Build the human-readable label: "2s5s10s"
    butterfly_label = (
        f"{params.short_tenor.replace('Y', '')}s"
        f"{params.belly_tenor.replace('Y', '')}s"
        f"{params.long_tenor.replace('Y', '')}s"
    )

    metrics = ButterflyCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        butterfly_label=butterfly_label,
        current_butterfly_bps=current_butterfly,
        daily_change_bps=daily_change,
        current_z_score=_safe_float(latest.get("z_score")),
        rolling_window_days=Z_SCORE_WINDOW,
        high_252d_bps=high_252,
        low_252d_bps=low_252,
        percentile_252d=percentile,
        wing_short_bps=_safe_float(latest.get("wing_short_bps"), 2),
        wing_long_bps=_safe_float(latest.get("wing_long_bps"), 2),
        short_tenor_yield=_safe_float(latest.get(params.short_tenor)),
        belly_tenor_yield=_safe_float(latest.get(params.belly_tenor)),
        long_tenor_yield=_safe_float(latest.get(params.long_tenor)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series (withheld from LLM, frontend-only)
    # ------------------------------------------------------------------
    ts_rows = [
        ButterflyTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            butterfly_bps=round(row.butterfly_bps, 2),
            z_score=_safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]

    output = ButterflyOutput(current_metrics=metrics, time_series=ts_rows)
    return output.model_dump()

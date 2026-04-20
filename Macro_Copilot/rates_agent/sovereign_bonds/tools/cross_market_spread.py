"""
cross_market_spread.py — Deterministic Cross-Market Spread Calculator
======================================================================

Computes the yield differential between the *same* tenor on *two different*
sovereign yield curves (e.g. 10Y UST − 10Y Bund, 10Y BTP − 10Y Bund).

This closes the architectural gap where the LLM was manually subtracting
yields from two yield_levels calls — violating the "no LLM math" principle.

Data flow
---------
1.  **Fetch** — two-curve, single-tenor query against the enriched view.
2.  **Pivot** — each curve_family becomes a column with trade_date as index.
3.  **Fill** — forward-fill to bridge cross-market holiday gaps (max 5 days).
4.  **Math** — spread = curve_family_1 − curve_family_2 (bps), 252-day
    rolling z-score, daily/weekly/monthly changes, trailing high/low/percentile.
5.  **Return** — ``CrossMarketSpreadOutput`` with ``current_metrics`` only
    (time_series withheld from LLM, same pattern as curve_spread).
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    CrossMarketSpreadInput,
    CrossMarketSpreadCurrentMetrics,
    CrossMarketSpreadOutput,
    CrossMarketSpreadTimeSeriesRow,
)


# ============================================================================
# PRIVATE HELPERS
# ============================================================================

_FETCH_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE curve_family  IN (:curve_family_1, :curve_family_2)
      AND tenor         = :tenor
      AND field_name    = :field_name
      AND trade_date   >= :start_date
    ORDER BY trade_date
""")


def _fetch_raw(
    engine: Engine,
    curve_family_1: str,
    curve_family_2: str,
    tenor: str,
    field_name: str,
    start_date: date,
) -> pd.DataFrame:
    """Run the parameterized query and return a long-format DataFrame."""
    with engine.connect() as conn:
        result = conn.execute(
            _FETCH_SQL,
            {
                "curve_family_1": curve_family_1,
                "curve_family_2": curve_family_2,
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

def calculate_cross_market_spread(
    engine: Engine,
    params: CrossMarketSpreadInput,
) -> Dict[str, Any]:
    """
    Calculate the cross-market yield differential between two sovereign
    curves at the same tenor point.

    The spread is defined as: curve_family_1 − curve_family_2 (in bps).
    Convention: for BTP-Bund, curve_family_1='IT_BTP', curve_family_2='DE_BUND'.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : CrossMarketSpreadInput
        Validated input with curve_family_1, curve_family_2, tenor,
        lookback_days, and field_name.

    Returns
    -------
    dict
        Serialized ``CrossMarketSpreadOutput``.  On failure, returns a dict
        with an ``"error"`` key.
    """

    # ------------------------------------------------------------------
    # 1. Date window — same buffering as curve_spread.py
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
        curve_family_1=params.curve_family_1,
        curve_family_2=params.curve_family_2,
        tenor=params.tenor,
        field_name=params.field_name,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curves ['{params.curve_family_1}', "
                f"'{params.curve_family_2}'], tenor='{params.tenor}', "
                f"field='{params.field_name}' since {start_date.isoformat()}.  "
                "Please verify the curve families and tenor exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate both curves are present
    # ------------------------------------------------------------------
    available_curves = set(raw_df["curve_family"].unique())
    missing = {params.curve_family_1, params.curve_family_2} - available_curves
    if missing:
        return {
            "error": (
                f"Missing curve data for {missing} at tenor='{params.tenor}'.  "
                f"Available curve families in the query window: {sorted(available_curves)}."
            )
        }

    # ------------------------------------------------------------------
    # 4. Pivot → wide format (date × curve_family)
    # ------------------------------------------------------------------
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "curve_family"], keep="last"
    )

    wide = raw_df.pivot(index="trade_date", columns="curve_family", values="field_value")
    wide = wide.sort_index()

    # Forward-fill to bridge cross-market holiday gaps (max 5 business days).
    wide = wide.ffill(limit=5)
    wide = wide.dropna(subset=[params.curve_family_1, params.curve_family_2])

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for '{params.curve_family_1}' and "
                f"'{params.curve_family_2}' at {params.tenor}, no "
                "overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Calculate spread (bps) and rolling z-score
    # ------------------------------------------------------------------
    wide["spread_bps"] = (
        (wide[params.curve_family_1] - wide[params.curve_family_2]) * 100
    ).round(2)

    rolling_mean = wide["spread_bps"].rolling(window=Z_SCORE_WINDOW, min_periods=60).mean()
    rolling_std = wide["spread_bps"].rolling(window=Z_SCORE_WINDOW, min_periods=60).std()
    wide["z_score"] = ((wide["spread_bps"] - rolling_mean) / rolling_std).round(4)

    # ------------------------------------------------------------------
    # 6. Trim to requested lookback
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family_1}' vs '{params.curve_family_2}' "
                f"at {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    spreads = display_df["spread_bps"]

    current_spread = _safe_float(latest["spread_bps"], 2)

    # Daily / weekly / monthly change in spread (bps)
    daily_change = _bps_change(
        latest["spread_bps"],
        spreads.iloc[-2] if len(spreads) >= 2 else None,
    )
    weekly_change = _bps_change(
        latest["spread_bps"],
        spreads.iloc[-6] if len(spreads) >= 6 else None,
    )
    monthly_change = _bps_change(
        latest["spread_bps"],
        spreads.iloc[-22] if len(spreads) >= 22 else None,
    )

    # Trailing high / low / percentile (252 trading days)
    trailing = spreads.iloc[-Z_SCORE_WINDOW:] if len(spreads) >= Z_SCORE_WINDOW else spreads
    high_252 = _safe_float(trailing.max(), 2)
    low_252 = _safe_float(trailing.min(), 2)

    if high_252 is not None and low_252 is not None and high_252 != low_252:
        percentile = round(
            (float(latest["spread_bps"]) - low_252) / (high_252 - low_252) * 100, 1
        )
    else:
        percentile = None

    spread_label = f"{params.curve_family_1}-{params.curve_family_2} {params.tenor}"

    metrics = CrossMarketSpreadCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        curve_family_1=params.curve_family_1,
        curve_family_2=params.curve_family_2,
        tenor=params.tenor,
        spread_label=spread_label,
        current_spread_bps=current_spread,
        daily_change_bps=daily_change,
        weekly_change_bps=weekly_change,
        monthly_change_bps=monthly_change,
        current_z_score=_safe_float(latest.get("z_score")),
        rolling_window_days=Z_SCORE_WINDOW,
        high_252d_bps=high_252,
        low_252d_bps=low_252,
        percentile_252d=percentile,
        curve_family_1_yield=_safe_float(latest.get(params.curve_family_1)),
        curve_family_2_yield=_safe_float(latest.get(params.curve_family_2)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series (withheld from LLM, used for frontend charting)
    # ------------------------------------------------------------------
    ts_rows = [
        CrossMarketSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, 2),
            z_score=_safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]

    output = CrossMarketSpreadOutput(current_metrics=metrics, time_series=ts_rows)
    return output.model_dump()

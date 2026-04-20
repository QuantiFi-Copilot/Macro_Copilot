"""
curve_spread.py — Deterministic Curve-Spread Math Tool
=======================================================

This module is the canonical implementation of the "calculate any two-point
curve spread" use-case for the Rates Agent.  It is fully parameterized — no
tenor pair is hard-coded.  The LLM extracts (curve_family, short_tenor,
long_tenor, lookback_days) from the user, validates them via
``CurveSpreadInput``, and this function does the rest.

Data flow
---------
1.  **Fetch** — SQLAlchemy text query against the flattened enriched view
    (``macro_data.v_market_data_daily_enriched``).  Parameters are bound, never
    interpolated, to prevent injection.
2.  **Pivot** — Long-format rows are pivoted so each tenor becomes a column
    with trade_date as the index.
3.  **Fill** — Forward-fill to handle public-holiday gaps (bonds trade on
    slightly different calendars across countries).
4.  **Math** — Spread = long − short (in basis points).  Fixed 252-trading-day
    rolling z-score (always 1 year, independent of the display lookback).
5.  **Return** — Structured dict matching ``CurveSpreadOutput``.

"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    CurveSpreadCurrentMetrics,
    CurveSpreadInput,
    CurveSpreadOutput,
    CurveSpreadTimeSeriesRow,
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
      AND tenor         IN (:short_tenor, :long_tenor)
      AND field_name    = :field_name
      AND trade_date   >= :start_date
    ORDER BY trade_date
""")


def _fetch_raw(
    engine: Engine,
    curve_family: str,
    short_tenor: str,
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
                "long_tenor": long_tenor,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
        rows = result.fetchall()
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


def _safe_float(value: Any) -> Optional[float]:
    """Convert a value to a Python float, returning None for NaN / None."""
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, 4)
    except (TypeError, ValueError):
        return None


# ============================================================================
# PUBLIC API
# ============================================================================

def calculate_curve_spread(
    engine: Engine,
    params: CurveSpreadInput,
) -> Dict[str, Any]:
    """
    Calculate the two-point curve spread, daily change, rolling z-score, and
    full time-series for a given curve family and tenor pair.

    Parameters
    ----------
    engine : sqlalchemy.engine.Engine
        A live SQLAlchemy engine connected to the macro TimescaleDB instance.
    params : CurveSpreadInput
        Validated Pydantic input with curve_family, short_tenor, long_tenor,
        lookback_days, and field_name.

    Returns
    -------
    dict
        Serialized ``CurveSpreadOutput`` with ``current_metrics`` and
        ``time_series`` keys.  If the data is missing or insufficient, the
        return dict contains an ``"error"`` key with a human-readable string
        the LLM can relay to the user.
    """

    # ------------------------------------------------------------------
    # 1. Determine the date window
    # ------------------------------------------------------------------
    # Two independent concepts:
    #   - lookback_days:   how much *displayed* history the user wants
    #   - Z_SCORE_WINDOW:  fixed 252 trading-day (~1 year) rolling window
    #                      for mean/std, as specified in the requirements
    #
    # We fetch extra history (the warm-up buffer) so the z-score is
    # already populated from the first displayed row.  The buffer is
    # 1.5× the z-score window in calendar days to account for weekends
    # and holidays.
    Z_SCORE_WINDOW: int = 252  # fixed 1-year rolling window (trading days)
    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)  # ~378 calendar days
    start_date = date.today() - timedelta(days=params.lookback_days + buffer_calendar_days)

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = _fetch_raw(
        engine=engine,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        long_tenor=params.long_tenor,
        field_name=params.field_name,
        start_date=start_date,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No data found for curve_family='{params.curve_family}', "
                f"tenors=['{params.short_tenor}', '{params.long_tenor}'], "
                f"field='{params.field_name}' since {start_date.isoformat()}.  "
                "Please verify the curve family and tenors exist in the database."
            )
        }

    # ------------------------------------------------------------------
    # 3. Validate that both tenors are present
    # ------------------------------------------------------------------
    available_tenors = set(raw_df["tenor"].unique())
    missing = {params.short_tenor, params.long_tenor} - available_tenors
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

    # De-duplicate: if somehow there are two rows for the same date+tenor,
    # keep the last (most recently loaded).
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "tenor"], keep="last"
    )

    wide = raw_df.pivot(index="trade_date", columns="tenor", values="field_value")
    wide = wide.sort_index()

    # Forward-fill to bridge holiday mismatches (max 5 business days).
    wide = wide.ffill(limit=5)

    # Drop rows where either leg is still NaN after the fill.
    wide = wide.dropna(subset=[params.short_tenor, params.long_tenor])

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for '{params.short_tenor}' and "
                f"'{params.long_tenor}' on '{params.curve_family}', no "
                "overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Calculate spread (bps) and rolling z-score
    # ------------------------------------------------------------------
    # Yields are stored as percentages (e.g. 4.25 = 4.25%).
    # Spread in bps = (long − short) × 100.
    wide["spread_bps"] = (
        (wide[params.long_tenor] - wide[params.short_tenor]) * 100
    ).round(2)

    # Rolling z-score: (current − rolling_mean) / rolling_std
    # Always uses a fixed 252-trading-day window regardless of lookback_days.
    rolling_mean = wide["spread_bps"].rolling(window=Z_SCORE_WINDOW, min_periods=60).mean()
    rolling_std = wide["spread_bps"].rolling(window=Z_SCORE_WINDOW, min_periods=60).std()
    wide["z_score"] = ((wide["spread_bps"] - rolling_mean) / rolling_std).round(4)

    # ------------------------------------------------------------------
    # 6. Trim to the requested lookback (discard warm-up rows)
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for '{params.curve_family}' {params.short_tenor}/{params.long_tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    previous = display_df.iloc[-2] if len(display_df) >= 2 else None

    current_spread = _safe_float(latest["spread_bps"])
    daily_change = (
        _safe_float(round(latest["spread_bps"] - previous["spread_bps"], 2))
        if previous is not None
        else None
    )

    spread_label = (
        f"{params.short_tenor.replace('Y', '')}s"
        f"{params.long_tenor.replace('Y', '')}s"
    )

    metrics = CurveSpreadCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        spread_label=spread_label,
        current_spread_bps=current_spread,
        daily_change_bps=daily_change,
        current_z_score=_safe_float(latest.get("z_score")),
        rolling_window_days=Z_SCORE_WINDOW,
        short_tenor_yield=_safe_float(latest.get(params.short_tenor)),
        long_tenor_yield=_safe_float(latest.get(params.long_tenor)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series
    # ------------------------------------------------------------------
    ts_rows = [
        CurveSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, 2),
            z_score=_safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]

    output = CurveSpreadOutput(current_metrics=metrics, time_series=ts_rows)
    return output.model_dump()

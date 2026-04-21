"""
cross_market_spread.py — Deterministic Cross-Market Spread Calculator
======================================================================

Computes the yield differential between the *same* tenor on *two different*
sovereign yield curves (e.g. 10Y UST − 10Y Bund, 10Y BTP − 10Y Bund).

Thin orchestration layer over ``shared/analytics/`` primitives:

- ``fetch_cross_market_pair``        — DB query
- ``pivot_and_align_tenors``         — pivot by curve_family + ffill
- ``compute_spread_bps``             — (cf1 - cf2) * 100
- ``rolling_zscore``                 — 252-day z-score
- ``period_changes(already_bps=True)`` — daily/weekly/monthly on spread
- ``trailing_high_low_percentile``   — 252d range stats
- ``safe_float``                     — None/NaN-safe numeric coercion

Domain-specific responsibilities that stay in this module: input
validation, 2-curve error messages, ``"BTP-Bund 10Y"`` label, and
output-schema assembly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    CrossMarketSpreadInput,
    CrossMarketSpreadCurrentMetrics,
    CrossMarketSpreadOutput,
    CrossMarketSpreadTimeSeriesRow,
)
from shared.analytics.levels import (
    period_changes,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_cross_market_pair
from shared.analytics.spreads import (
    Z_SCORE_WINDOW,
    compute_spread_bps,
    pivot_and_align_tenors,
    rolling_zscore,
    safe_float,
)


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
    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = fetch_cross_market_pair(
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
    # 4. Pivot → wide format (date × curve_family) and align holiday gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.curve_family_1, params.curve_family_2),
        key_col="curve_family",
    )

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
    wide["spread_bps"] = compute_spread_bps(
        wide,
        minuend_col=params.curve_family_1,
        subtrahend_col=params.curve_family_2,
    )
    wide["z_score"] = rolling_zscore(wide["spread_bps"])

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

    current_spread = safe_float(latest["spread_bps"], decimals=2)

    # Daily / weekly / monthly change in spread (bps).  The spread series
    # is ALREADY in bps, so we use already_bps=True to get a plain
    # subtraction instead of a *100 multiplication.
    changes = period_changes(spreads, already_bps=True)

    # Trailing high / low / percentile on the spread series (already in bps,
    # so round to 2 decimals to match the scale).
    high_252, low_252, percentile = trailing_high_low_percentile(
        spreads, window=Z_SCORE_WINDOW, decimals=2,
    )

    spread_label = f"{params.curve_family_1}-{params.curve_family_2} {params.tenor}"

    metrics = CrossMarketSpreadCurrentMetrics(
        as_of_date=latest.name.strftime("%Y-%m-%d"),
        curve_family_1=params.curve_family_1,
        curve_family_2=params.curve_family_2,
        tenor=params.tenor,
        spread_label=spread_label,
        current_spread_bps=current_spread,
        daily_change_bps=changes["daily"],
        weekly_change_bps=changes["weekly"],
        monthly_change_bps=changes["monthly"],
        current_z_score=safe_float(latest.get("z_score")),
        rolling_window_days=Z_SCORE_WINDOW,
        high_252d_bps=high_252,
        low_252d_bps=low_252,
        percentile_252d=percentile,
        curve_family_1_yield=safe_float(latest.get(params.curve_family_1)),
        curve_family_2_yield=safe_float(latest.get(params.curve_family_2)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series (withheld from LLM, used for frontend charting)
    # ------------------------------------------------------------------
    ts_rows = [
        CrossMarketSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, 2),
            z_score=safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]

    output = CrossMarketSpreadOutput(current_metrics=metrics, time_series=ts_rows)
    return output.model_dump()

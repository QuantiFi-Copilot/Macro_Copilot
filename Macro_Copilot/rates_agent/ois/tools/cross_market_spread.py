"""
cross_market_spread.py — Deterministic OIS Cross-Market Spread Tool
=====================================================================

Computes the rate differential between the SAME tenor on TWO DIFFERENT
OIS curves (e.g. 2Y USD_SOFR_OIS − 2Y EUR_ESTR_OIS — the G4 policy
differential; 5Y SOFR − SONIA; 10Y ESTR − BOE).

The cleanest measure of relative central-bank policy stance across
currencies: strips out every cross-border distortion except the
reaction function itself.

Thin orchestration layer over ``shared/analytics/`` primitives:

- ``fetch_cross_market_pair``        — DB query
- ``pivot_and_align_tenors``         — pivot by curve_family + ffill
- ``compute_spread_bps``             — (cf1 - cf2) * 100
- ``rolling_zscore``                 — 252-day z-score
- ``period_changes(already_bps=True)`` — daily/weekly/monthly on spread
- ``trailing_high_low_percentile``   — 252d range stats
- ``safe_float``                     — None/NaN-safe numeric coercion

Domain-specific responsibilities that stay in this module: input
validation, OIS-aware error messages, ``"USD_SOFR_OIS-EUR_ESTR_OIS 2Y"``
label, and output-schema assembly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.schemas import (
    OISCrossMarketSpreadCurrentMetrics,
    OISCrossMarketSpreadInput,
    OISCrossMarketSpreadOutput,
    OISCrossMarketSpreadTimeSeriesRow,
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

def calculate_ois_cross_market_spread(
    engine: Engine,
    params: OISCrossMarketSpreadInput,
) -> Dict[str, Any]:
    """
    Calculate the cross-market rate differential between two OIS curves
    at the same tenor.

    Spread definition: ``curve_family_1 − curve_family_2`` in bps.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : OISCrossMarketSpreadInput
        Validated input with curve_family_1, curve_family_2, tenor,
        lookback_days, and field_name (defaults to 'PX_LAST').

    Returns
    -------
    dict
        Serialized ``OISCrossMarketSpreadOutput``.  On failure, returns
        a dict with an ``"error"`` key.
    """

    # ------------------------------------------------------------------
    # 1. Date window
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
                f"No OIS data found for curves ['{params.curve_family_1}', "
                f"'{params.curve_family_2}'], tenor='{params.tenor}', "
                f"field='{params.field_name}' since {start_date.isoformat()}.  "
                "Please verify both OIS curve families and the tenor exist "
                "in the database."
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
                f"Missing OIS curve data for {missing} at tenor='{params.tenor}'.  "
                f"Available curve families in the query window: "
                f"{sorted(available_curves)}."
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
                f"After aligning dates for OIS '{params.curve_family_1}' and "
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
    # Anchor the cutoff to the data's latest observation date, NOT
    # date.today() — the DB can be 1-3 days stale over weekends /
    # holidays and wall-clock anchoring produces inconsistent history.
    as_of_date = wide.index[-1].date()
    cutoff = pd.Timestamp(as_of_date - timedelta(days=params.lookback_days))
    display_df = wide.loc[wide.index >= cutoff].copy()

    if display_df.empty:
        return {
            "error": (
                f"No observations within the last {params.lookback_days} days "
                f"for OIS '{params.curve_family_1}' vs '{params.curve_family_2}' "
                f"at {params.tenor}."
            )
        }

    # ------------------------------------------------------------------
    # 7. Build current_metrics
    # ------------------------------------------------------------------
    latest = display_df.iloc[-1]
    spreads = display_df["spread_bps"]

    current_spread = safe_float(latest["spread_bps"], decimals=2)

    # Spread series is already in bps → already_bps=True for plain subtraction.
    changes = period_changes(spreads, already_bps=True)

    # Trailing high/low/percentile on the spread (bps scale → decimals=2).
    high_252, low_252, percentile = trailing_high_low_percentile(
        spreads, window=Z_SCORE_WINDOW, decimals=2,
    )

    spread_label = (
        f"{params.curve_family_1}-{params.curve_family_2} {params.tenor}"
    )

    metrics = OISCrossMarketSpreadCurrentMetrics(
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
        curve_family_1_rate=safe_float(latest.get(params.curve_family_1)),
        curve_family_2_rate=safe_float(latest.get(params.curve_family_2)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series (withheld from LLM, frontend-only)
    # ------------------------------------------------------------------
    ts_rows = [
        OISCrossMarketSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, 2),
            z_score=safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]

    output = OISCrossMarketSpreadOutput(
        current_metrics=metrics, time_series=ts_rows,
    )
    return output.model_dump()

"""
yield_levels.py — Deterministic Yield Level Monitor
=====================================================

Returns the current yield, period changes (daily/weekly/monthly in bps),
a 252-day rolling z-score, and deterministic context (1-year high, low,
percentile) for a single point on a sovereign yield curve.

Thin orchestration layer over ``shared/analytics/`` primitives:

- ``fetch_single_tenor``            — DB query
- ``clean_single_series``           — sort + dedup + ffill
- ``period_changes``                — daily/weekly/monthly bps deltas
- ``rolling_zscore``                — 252-day z-score
- ``trailing_high_low_percentile``  — trailing stats
- ``safe_float``                    — None/NaN-safe numeric coercion

Domain-specific responsibilities that stay in this module: input
validation, domain-aware error messages, and output-schema assembly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    YieldLevelInput,
    YieldLevelMetrics,
    YieldLevelOutput,
)
from shared.analytics.levels import (
    clean_single_series,
    period_changes,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_single_tenor
from shared.analytics.spreads import (
    Z_SCORE_WINDOW,
    rolling_zscore,
    safe_float,
)


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
    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = fetch_single_tenor(
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
    # 3. Clean (sort + dedup + ffill)
    # ------------------------------------------------------------------
    clean_df = clean_single_series(raw_df)

    if clean_df.empty:
        return {
            "error": (
                f"All values were null after cleaning for "
                f"'{params.curve_family}' {params.tenor}."
            )
        }

    yields = clean_df["field_value"]
    current_yield = float(yields.iloc[-1])

    # ------------------------------------------------------------------
    # 4. Math — period changes, z-score, trailing range
    # ------------------------------------------------------------------
    changes = period_changes(yields)
    daily_change = changes["daily"]
    weekly_change = changes["weekly"]
    monthly_change = changes["monthly"]

    z_series = rolling_zscore(yields)
    current_z = safe_float(z_series.iloc[-1])

    high_252, low_252, percentile = trailing_high_low_percentile(
        yields, window=Z_SCORE_WINDOW, decimals=4,
    )

    # ------------------------------------------------------------------
    # 5. Observation count (in the displayed window only)
    # ------------------------------------------------------------------
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
    # 6. Build output
    # ------------------------------------------------------------------
    metrics = YieldLevelMetrics(
        as_of_date=yields.index[-1].strftime("%Y-%m-%d"),
        curve_family=params.curve_family,
        tenor=params.tenor,
        current_yield_pct=safe_float(current_yield),
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

"""
butterfly.py — Deterministic Butterfly / Curvature Calculator
==============================================================

Computes the 3-point curvature (butterfly) on a single sovereign yield
curve:

    butterfly_bps = (2 × belly − short − long) × 100

A *positive* butterfly means the belly is cheap; a *negative* butterfly
means the belly is rich.  Also returns the component wing spreads
(belly−short, long−belly) for decomposition.

Thin orchestration layer over ``shared/analytics/`` primitives:

- ``fetch_tenor_group``              — DB query (3 tenors)
- ``pivot_and_align_tenors``         — pivot by tenor + ffill
- ``compute_spread_bps``             — wing spreads
- ``rolling_zscore``                 — 252-day z-score
- ``trailing_high_low_percentile``   — 252d range stats
- ``safe_float``                     — None/NaN-safe numeric coercion

The butterfly formula itself stays inline — it's a single expression,
and extracting it would be over-engineering.

Domain-specific responsibilities that stay in this module: input
validation, domain-aware error messages, ``"2s5s10s"`` label, and
output-schema assembly (including wing_short_bps, wing_long_bps).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    ButterflyInput,
    ButterflyCurrentMetrics,
    ButterflyOutput,
    ButterflyTimeSeriesRow,
)
from shared.analytics.levels import (
    delta_bps,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_tenor_group
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
    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # ------------------------------------------------------------------
    # 2. Fetch (3 tenors on one curve)
    # ------------------------------------------------------------------
    raw_df = fetch_tenor_group(
        engine=engine,
        curve_family=params.curve_family,
        tenors=[params.short_tenor, params.belly_tenor, params.long_tenor],
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
    # 4. Pivot → wide format (date × tenor) and align holiday gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.short_tenor, params.belly_tenor, params.long_tenor),
    )

    if wide.empty:
        return {
            "error": (
                f"After aligning dates for '{params.short_tenor}', "
                f"'{params.belly_tenor}', and '{params.long_tenor}' on "
                f"'{params.curve_family}', no overlapping observations remain."
            )
        }

    # ------------------------------------------------------------------
    # 5. Compute butterfly + wing spreads + z-score
    # ------------------------------------------------------------------
    s = wide[params.short_tenor]
    b = wide[params.belly_tenor]
    l = wide[params.long_tenor]

    # Butterfly = 2 × belly − short − long (in bps) — kept inline;
    # too trivial to warrant a shared helper.
    wide["butterfly_bps"] = ((2 * b - s - l) * 100).round(2)

    # Wing spreads reuse the shared 2-leg spread helper.
    wide["wing_short_bps"] = compute_spread_bps(
        wide, minuend_col=params.belly_tenor, subtrahend_col=params.short_tenor,
    )
    wide["wing_long_bps"] = compute_spread_bps(
        wide, minuend_col=params.long_tenor, subtrahend_col=params.belly_tenor,
    )

    # Rolling z-score on the butterfly series (already in bps).
    wide["z_score"] = rolling_zscore(wide["butterfly_bps"])

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

    current_butterfly = safe_float(latest["butterfly_bps"], decimals=2)

    # Daily change on the butterfly series (already bps → plain subtract).
    daily_change = delta_bps(
        latest["butterfly_bps"],
        bfly_series.iloc[-2] if len(bfly_series) >= 2 else None,
    )

    # Trailing 252d high/low/percentile of the butterfly (bps scale).
    high_252, low_252, percentile = trailing_high_low_percentile(
        bfly_series, window=Z_SCORE_WINDOW, decimals=2,
    )

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
        current_z_score=safe_float(latest.get("z_score")),
        rolling_window_days=Z_SCORE_WINDOW,
        high_252d_bps=high_252,
        low_252d_bps=low_252,
        percentile_252d=percentile,
        wing_short_bps=safe_float(latest.get("wing_short_bps"), decimals=2),
        wing_long_bps=safe_float(latest.get("wing_long_bps"), decimals=2),
        short_tenor_yield=safe_float(latest.get(params.short_tenor)),
        belly_tenor_yield=safe_float(latest.get(params.belly_tenor)),
        long_tenor_yield=safe_float(latest.get(params.long_tenor)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series (withheld from LLM, frontend-only)
    # ------------------------------------------------------------------
    ts_rows = [
        ButterflyTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            butterfly_bps=round(row.butterfly_bps, 2),
            z_score=safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]

    output = ButterflyOutput(current_metrics=metrics, time_series=ts_rows)
    return output.model_dump()

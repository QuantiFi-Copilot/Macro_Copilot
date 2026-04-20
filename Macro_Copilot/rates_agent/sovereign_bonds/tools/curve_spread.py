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
1.  **Fetch** — ``shared.analytics.rates_fetch.fetch_tenor_pair`` runs a
    parameterized SELECT against the enriched view.  Parameters are bound,
    never interpolated, to prevent injection.
2.  **Pivot + align** — ``shared.analytics.spreads.pivot_and_align_tenors``
    pivots long-format rows to wide and forward-fills small holiday gaps.
3.  **Math** — ``compute_spread_bps`` gives ``(long − short) × 100``;
    ``rolling_zscore`` gives the fixed 252-trading-day z-score.
4.  **Return** — Structured dict matching ``CurveSpreadOutput``.

Domain-specific responsibilities that stay in this module: input
validation, domain-aware error messages, window trimming, spread label
formatting, and output-schema assembly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.schemas import (
    CurveSpreadCurrentMetrics,
    CurveSpreadInput,
    CurveSpreadOutput,
    CurveSpreadTimeSeriesRow,
)
from shared.analytics.rates_fetch import fetch_tenor_pair
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
    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)  # ~378 calendar days
    start_date = date.today() - timedelta(days=params.lookback_days + buffer_calendar_days)

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    raw_df = fetch_tenor_pair(
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
    # 4. Pivot → wide format (date × tenor) and align across holiday gaps
    # ------------------------------------------------------------------
    wide = pivot_and_align_tenors(
        raw_df,
        required_tenors=(params.short_tenor, params.long_tenor),
    )

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
    wide["spread_bps"] = compute_spread_bps(
        wide,
        minuend_col=params.long_tenor,
        subtrahend_col=params.short_tenor,
    )

    # Rolling z-score: always 252 trading days regardless of lookback_days.
    wide["z_score"] = rolling_zscore(wide["spread_bps"])

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

    current_spread = safe_float(latest["spread_bps"])
    daily_change = (
        safe_float(round(latest["spread_bps"] - previous["spread_bps"], 2))
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
        current_z_score=safe_float(latest.get("z_score")),
        rolling_window_days=Z_SCORE_WINDOW,
        short_tenor_yield=safe_float(latest.get(params.short_tenor)),
        long_tenor_yield=safe_float(latest.get(params.long_tenor)),
    )

    # ------------------------------------------------------------------
    # 8. Build time_series
    # ------------------------------------------------------------------
    ts_rows = [
        CurveSpreadTimeSeriesRow(
            date=row.Index.strftime("%Y-%m-%d"),
            spread_bps=round(row.spread_bps, 2),
            z_score=safe_float(row.z_score),
        )
        for row in display_df.itertuples()
    ]

    output = CurveSpreadOutput(current_metrics=metrics, time_series=ts_rows)
    return output.model_dump()

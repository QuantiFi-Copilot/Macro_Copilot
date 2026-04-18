"""
scanner.py — Deterministic Extreme-Move / Z-Score Scanner
==========================================================

Scans across ALL sovereign yield curve instruments in the database and
returns the most statistically extreme observations — ranked by absolute
z-score.

This is an exploration tool, not a point query.  It answers questions
the PM doesn't know to ask: "What's the most stretched relationship
across all markets today?"  It also enables daily morning-briefing
workflows: "Flag anything with a z-score above 2."

Design
------
Unlike the other tools (which query a specific curve/tenor), this tool
queries the entire universe and computes z-scores for every instrument
in a single pass.  The LLM receives only the ranked results — no
raw time-series.

Data flow
---------
1.  **Fetch** — broad query across all sovereign benchmark instruments
    (filter by instrument_type = 'sovereign_benchmark' and field_name).
2.  **Group** — group by (curve_family, tenor).
3.  **Math** — for each group: current yield, 252-day rolling z-score,
    daily change, trailing high/low, percentile.
4.  **Rank** — sort by abs(z_score) descending.
5.  **Return** — top N results as a ranked list.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rates_agent.tools.schemas import (
    ScannerInput,
    ScannerResultRow,
    ScannerOutput,
)


# ============================================================================
# CONSTANTS
# ============================================================================

Z_SCORE_WINDOW = 252


# ============================================================================
# PRIVATE HELPERS
# ============================================================================

_FETCH_ALL_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        tenor,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = 'sovereign_benchmark'
      AND field_name      = :field_name
      AND trade_date     >= :start_date
      AND tenor IS NOT NULL
    ORDER BY curve_family, tenor, trade_date
""")

_FETCH_FILTERED_SQL = text("""
    SELECT
        trade_date,
        curve_family,
        tenor,
        field_value
    FROM macro_data.v_market_data_daily_enriched
    WHERE instrument_type = 'sovereign_benchmark'
      AND field_name      = :field_name
      AND trade_date     >= :start_date
      AND tenor IS NOT NULL
      AND curve_family    = ANY(:curve_families)
    ORDER BY curve_family, tenor, trade_date
""")


def _safe_float(value: Any, decimals: int = 4) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        return None if math.isnan(f) else round(f, decimals)
    except (TypeError, ValueError):
        return None


def _compute_group_metrics(group_df: pd.DataFrame) -> Optional[Dict[str, Any]]:
    """Compute z-score and context metrics for a single (curve_family, tenor) group."""
    if len(group_df) < 60:
        return None

    yields = group_df["field_value"]
    current_yield = float(yields.iloc[-1])

    # Daily change
    daily_change = None
    if len(yields) >= 2:
        daily_change = round((current_yield - float(yields.iloc[-2])) * 100, 2)

    # Rolling z-score
    rolling_mean = yields.rolling(window=Z_SCORE_WINDOW, min_periods=60).mean()
    rolling_std = yields.rolling(window=Z_SCORE_WINDOW, min_periods=60).std()
    z_series = (yields - rolling_mean) / rolling_std
    current_z = _safe_float(z_series.iloc[-1])

    if current_z is None:
        return None

    # Trailing high / low / percentile
    trailing = yields.iloc[-Z_SCORE_WINDOW:] if len(yields) >= Z_SCORE_WINDOW else yields
    high_252 = _safe_float(trailing.max())
    low_252 = _safe_float(trailing.min())

    percentile = None
    if high_252 is not None and low_252 is not None and high_252 != low_252:
        percentile = round(
            (current_yield - low_252) / (high_252 - low_252) * 100, 1
        )

    return {
        "current_yield": _safe_float(current_yield),
        "daily_change_bps": daily_change,
        "z_score": current_z,
        "high_252d": high_252,
        "low_252d": low_252,
        "percentile_252d": percentile,
        "as_of_date": group_df.index[-1].strftime("%Y-%m-%d"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def scan_extremes(
    engine: Engine,
    params: ScannerInput,
) -> Dict[str, Any]:
    """
    Scan all sovereign benchmark instruments for z-score extremes.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : ScannerInput
        Validated input with optional curve_families filter, top_n,
        min_abs_z_score, and field_name.

    Returns
    -------
    dict
        Serialized ``ScannerOutput``.  On failure, returns a dict
        with an ``"error"`` key.
    """

    # ------------------------------------------------------------------
    # 1. Date window — enough for z-score warm-up
    # ------------------------------------------------------------------
    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)
    start_date = date.today() - timedelta(days=365 + buffer_calendar_days)

    # ------------------------------------------------------------------
    # 2. Fetch
    # ------------------------------------------------------------------
    with engine.connect() as conn:
        if params.curve_families:
            result = conn.execute(
                _FETCH_FILTERED_SQL,
                {
                    "field_name": params.field_name,
                    "start_date": start_date.isoformat(),
                    "curve_families": list(params.curve_families),
                },
            )
        else:
            result = conn.execute(
                _FETCH_ALL_SQL,
                {
                    "field_name": params.field_name,
                    "start_date": start_date.isoformat(),
                },
            )
        rows = result.fetchall()
        columns = list(result.keys())

    raw_df = pd.DataFrame(rows, columns=columns)

    if raw_df.empty:
        return {
            "error": (
                "No sovereign benchmark data found for "
                f"field='{params.field_name}' since {start_date.isoformat()}.  "
                "Please verify the database contains sovereign_benchmark instruments."
            )
        }

    # ------------------------------------------------------------------
    # 3. Clean and group
    # ------------------------------------------------------------------
    raw_df["trade_date"] = pd.to_datetime(raw_df["trade_date"])
    raw_df["field_value"] = pd.to_numeric(raw_df["field_value"], errors="coerce")
    raw_df = raw_df.dropna(subset=["field_value"])
    raw_df = raw_df.drop_duplicates(
        subset=["trade_date", "curve_family", "tenor"], keep="last"
    )

    # ------------------------------------------------------------------
    # 4. Compute metrics per (curve_family, tenor)
    # ------------------------------------------------------------------
    all_results: List[Dict[str, Any]] = []

    for (curve_family, tenor), group in raw_df.groupby(["curve_family", "tenor"]):
        group = group.set_index("trade_date").sort_index()
        group = group.ffill(limit=5)

        metrics = _compute_group_metrics(group)
        if metrics is None:
            continue

        # Apply z-score threshold filter
        if abs(metrics["z_score"]) < params.min_abs_z_score:
            continue

        all_results.append({
            "curve_family": curve_family,
            "tenor": tenor,
            **metrics,
        })

    if not all_results:
        filter_desc = ""
        if params.curve_families:
            filter_desc = f" for curves {params.curve_families}"
        return {
            "error": (
                f"No instruments found with |z-score| >= {params.min_abs_z_score}"
                f"{filter_desc}.  Try lowering min_abs_z_score."
            )
        }

    # ------------------------------------------------------------------
    # 5. Rank by absolute z-score and take top N
    # ------------------------------------------------------------------
    all_results.sort(key=lambda r: abs(r["z_score"]), reverse=True)
    top_results = all_results[: params.top_n]

    # ------------------------------------------------------------------
    # 6. Build output
    # ------------------------------------------------------------------
    result_rows = [
        ScannerResultRow(
            rank=i + 1,
            curve_family=r["curve_family"],
            tenor=r["tenor"],
            as_of_date=r["as_of_date"],
            current_yield_pct=r["current_yield"],
            daily_change_bps=r["daily_change_bps"],
            z_score=r["z_score"],
            high_252d_pct=r["high_252d"],
            low_252d_pct=r["low_252d"],
            percentile_252d=r["percentile_252d"],
            signal="EXTREME_HIGH" if r["z_score"] > 0 else "EXTREME_LOW",
        )
        for i, r in enumerate(top_results)
    ]

    output = ScannerOutput(
        scan_summary=(
            f"Scanned {len(raw_df.groupby(['curve_family', 'tenor']))} instruments.  "
            f"Found {len(all_results)} with |z-score| >= {params.min_abs_z_score}.  "
            f"Showing top {len(top_results)} by absolute z-score."
        ),
        results=result_rows,
    )
    return output.model_dump()

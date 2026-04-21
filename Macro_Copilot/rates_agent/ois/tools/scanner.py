"""
scanner.py — Deterministic OIS Extreme-Move / Z-Score Scanner
===============================================================

OIS equivalent of the sovereign scanner.  Sweeps every OIS curve in the
database and returns the most statistically stretched (curve_family,
tenor) observations by absolute 252-day z-score.

The morning-sweep tool for the OIS side: "anything unusual in swaps
space?"  Symmetric with the sovereign scanner — near-trivial port
thanks to ``fetch_scan_universe(instrument_type='ois_swap')``, which
was generalized in the sovereign analytics refactor specifically to
make this tool a one-line change.

Thin orchestration layer over ``shared/analytics/`` primitives:

- ``fetch_scan_universe(instrument_type='ois_swap', ...)``
                                     — DB query across the whole OIS universe
- ``rolling_zscore``                 — 252-day z-score per instrument
- ``bps_change``                     — daily bps delta per instrument
- ``trailing_high_low_percentile``   — 252d range stats per instrument
- ``safe_float``                     — None/NaN-safe numeric coercion

Domain-specific responsibilities that stay in this module: input
validation, the groupby / threshold / ranking workflow, and
output-schema assembly.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.schemas import (
    OISScannerInput,
    OISScannerOutput,
    OISScannerResultRow,
)
from shared.analytics.levels import (
    bps_change,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_scan_universe
from shared.analytics.spreads import (
    Z_SCORE_WINDOW,
    Z_SCORE_MIN_PERIODS,
    rolling_zscore,
    safe_float,
)


# ============================================================================
# INSTRUMENT TYPE — filter passed to fetch_scan_universe
# ============================================================================

_INSTRUMENT_TYPE = "ois_swap"


# ============================================================================
# PER-GROUP METRICS
# ============================================================================

def _compute_group_metrics(rates: pd.Series) -> Optional[Dict[str, Any]]:
    """Compute z-score + context metrics for a single (curve_family, tenor)
    group's clean rate series.  Same shape as the sovereign scanner — we
    reuse the shared helpers to guarantee byte-identical per-group math.
    """
    if len(rates) < Z_SCORE_MIN_PERIODS:
        return None

    current_rate = float(rates.iloc[-1])

    daily_change = None
    if len(rates) >= 2:
        daily_change = bps_change(current_rate, rates.iloc[-2])

    z_series = rolling_zscore(rates)
    current_z = safe_float(z_series.iloc[-1])
    if current_z is None:
        return None

    high_252, low_252, percentile = trailing_high_low_percentile(
        rates, window=Z_SCORE_WINDOW, decimals=4,
    )

    return {
        "current_rate": safe_float(current_rate),
        "daily_change_bps": daily_change,
        "z_score": current_z,
        "high_252d": high_252,
        "low_252d": low_252,
        "percentile_252d": percentile,
        "as_of_date": rates.index[-1].strftime("%Y-%m-%d"),
    }


# ============================================================================
# PUBLIC API
# ============================================================================

def scan_ois_extremes(
    engine: Engine,
    params: OISScannerInput,
) -> Dict[str, Any]:
    """Scan all OIS swap instruments for z-score extremes.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : OISScannerInput
        Validated input with optional curve_families filter, top_n,
        min_abs_z_score, and field_name.

    Returns
    -------
    dict
        Serialized ``OISScannerOutput``.  On failure, returns a dict
        with an ``"error"`` key.
    """

    # ------------------------------------------------------------------
    # 1. Date window — enough for z-score warm-up plus ~1 year display
    # ------------------------------------------------------------------
    buffer_calendar_days = int(Z_SCORE_WINDOW * 1.5)
    start_date = date.today() - timedelta(days=365 + buffer_calendar_days)

    # ------------------------------------------------------------------
    # 2. Fetch (every OIS swap series in the universe)
    # ------------------------------------------------------------------
    raw_df = fetch_scan_universe(
        engine=engine,
        instrument_type=_INSTRUMENT_TYPE,
        field_name=params.field_name,
        start_date=start_date,
        curve_families=params.curve_families,
    )

    if raw_df.empty:
        return {
            "error": (
                "No OIS swap data found for "
                f"field='{params.field_name}' since {start_date.isoformat()}.  "
                "Please verify the database contains ois_swap instruments."
            )
        }

    # ------------------------------------------------------------------
    # 3. Clean universe-wide (before splitting into groups)
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
    group_count = 0

    for (curve_family, tenor), group in raw_df.groupby(["curve_family", "tenor"]):
        group_count += 1
        group = group.set_index("trade_date").sort_index()
        group = group.ffill(limit=5)

        metrics = _compute_group_metrics(group["field_value"])
        if metrics is None:
            continue

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
                f"No OIS instruments found with |z-score| >= {params.min_abs_z_score}"
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
        OISScannerResultRow(
            rank=i + 1,
            curve_family=r["curve_family"],
            tenor=r["tenor"],
            as_of_date=r["as_of_date"],
            current_rate_pct=r["current_rate"],
            daily_change_bps=r["daily_change_bps"],
            z_score=r["z_score"],
            high_252d_pct=r["high_252d"],
            low_252d_pct=r["low_252d"],
            percentile_252d=r["percentile_252d"],
            signal="EXTREME_HIGH" if r["z_score"] > 0 else "EXTREME_LOW",
        )
        for i, r in enumerate(top_results)
    ]

    output = OISScannerOutput(
        scan_summary=(
            f"Scanned {group_count} OIS instruments.  "
            f"Found {len(all_results)} with |z-score| >= {params.min_abs_z_score}.  "
            f"Showing top {len(top_results)} by absolute z-score."
        ),
        results=result_rows,
    )
    return output.model_dump()

"""
compute.py — Deterministic OIS z-score scanner (config-driven).

Migrated 2026-06-10 from ``rates_agent/ois/tools/scanner.py`` (flat
file) to the canonical per-tool-folder layout.  Closes PR-10G gap #4
(OIS half).  The sovereign sibling at
``rates_agent/sovereign_bonds/tools/scan_extremes/`` ships in the
same commit.

The math is unchanged from the legacy file — convention defaults in
``config.yaml`` reproduce the legacy Python-module constants
bit-for-bit.  The only observable change for existing callers is that
``scan_ois_extremes`` now accepts an optional ``config: ToolConfig``
kwarg that defaults to None (auto-load from bundled YAML).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.scan_ois_extremes.schemas import (
    OISScannerInput,
    OISScannerOutput,
    OISScannerResultRow,
)
from shared.analytics.levels import (
    bps_change,
    trailing_high_low_percentile,
)
from shared.analytics.rates_fetch import fetch_scan_universe, latest_trade_date
from shared.analytics.spreads import (
    rolling_zscore,
    safe_float,
)
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH = Path(__file__).parent / "config.yaml"


# ============================================================================
# PER-GROUP METRICS  (config-driven)
# ============================================================================

def _compute_group_metrics(
    rates: pd.Series,
    *,
    z_window: int,
    z_min_periods: int,
    trailing_range_decimals: int,
) -> Optional[Dict[str, Any]]:
    """Compute z-score + context metrics for a single (curve_family,
    tenor) group's clean rate series.  Same shape as the sovereign
    scanner — we reuse the shared helpers to guarantee byte-identical
    per-group math."""
    if len(rates) < z_min_periods:
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
        rates, window=z_window, decimals=trailing_range_decimals,
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
    *,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Scan all OIS swap instruments for z-score extremes.

    Parameters
    ----------
    engine : Engine
        Live SQLAlchemy engine connected to TimescaleDB.
    params : OISScannerInput
        Validated input with optional curve_families filter, top_n,
        min_abs_z_score, and field_name.
    config : Optional[ToolConfig]
        Tool config carrying conventions.  Defaults to None (auto-load
        from bundled config.yaml).

    Returns
    -------
    dict
        Serialized ``OISScannerOutput``.  On failure, returns a dict
        with an ``"error"`` key.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    instrument_type = config.convention_value("ois_scan_instrument_type")
    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_buffer_multiplier = float(config.convention_value("z_score_buffer_multiplier"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    trailing_range_decimals = int(
        config.convention_value("trailing_range_round_decimals")
    )

    # ------------------------------------------------------------------
    # 1. Date window — enough for z-score warm-up plus ~1 year display
    # ------------------------------------------------------------------
    buffer_calendar_days = int(z_window * z_buffer_multiplier)
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags (weekend / holiday /
    # stale snapshot).  Probe the same (instrument_type, field_name) the
    # universe scan fetches; falls back to today only when the universe is
    # empty.  Mirrors the sovereign scan_extremes anchor.
    anchor = (
        params.as_of_date
        or latest_trade_date(
            engine, instrument_type=instrument_type, field_name=params.field_name
        )
        or date.today()
    )
    start_date = anchor - timedelta(days=365 + buffer_calendar_days)

    # ------------------------------------------------------------------
    # 2. Fetch (every OIS swap series in the universe)
    # ------------------------------------------------------------------
    raw_df = fetch_scan_universe(
        engine=engine,
        instrument_type=instrument_type,
        field_name=params.field_name,
        start_date=start_date,
        end_date=anchor,
        curve_families=params.curve_families,
    )

    if raw_df.empty:
        return {
            "error": (
                f"No {instrument_type} data found for "
                f"field='{params.field_name}' since {start_date.isoformat()}.  "
                f"Please verify the database contains {instrument_type} instruments."
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
        group = group.ffill(limit=ffill_limit)

        metrics = _compute_group_metrics(
            group["field_value"],
            z_window=z_window,
            z_min_periods=z_min_periods,
            trailing_range_decimals=trailing_range_decimals,
        )
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

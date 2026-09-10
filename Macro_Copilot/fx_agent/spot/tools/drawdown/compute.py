"""compute.py — Single-pair FX drawdown tool.

Phase B follow-up (2026-05-25).

Computes the running drawdown DD_t = (P_t / running_max(P_0..P_t)) - 1
on an FX spot series, plus a snapshot summary (max drawdown, peak /
trough dates, recovery date / time-to-recovery).

Mirrors yield_levels' 4-file pattern + the substrate discipline of
calculate_fx_panel (fail-loud on empty fetch / cleaning, no SQL ad-hoc).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from fx_agent.spot.tools.drawdown.schemas import (
    FXDrawdownInput,
    FXDrawdownMetrics,
    FXDrawdownOutput,
)
from shared.analytics.fx_fetch import fetch_fx_spot_series
from shared.analytics.levels import clean_single_series
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


def calculate_fx_drawdown(
    engine: Engine,
    params: FXDrawdownInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Compute the running drawdown of one FX spot pair over a window.

    Returns the serialised ``FXDrawdownOutput`` as a dict — typed
    snapshot + canonical TimeSeries. Raises ``ValueError`` on
    unknown pair / empty cleaning / zero observations in window.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field_name = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    dd_round_decimals = int(config.convention_value("drawdown_round_decimals"))
    px_round_decimals = int(config.convention_value("price_round_decimals"))

    field_name_resolved = params.field_name or default_field_name

    # 1. Date window — fetch a slightly larger window (we'll slice to
    #    display window after cleaning). Buffer protects against
    #    leading NaN rows that get dropped during cleaning.
    start_date = date.today() - timedelta(days=params.lookback_days + 30)

    # 2. Fetch single-pair via shared loader
    raw_df = fetch_fx_spot_series(
        engine=engine,
        pair=params.pair,
        start_date=start_date,
        field_name=field_name_resolved,
    )
    if raw_df.empty:
        raise ValueError(
            f"calculate_fx_drawdown: no observations for pair={params.pair!r}, "
            f"field={field_name_resolved!r}, since {start_date.isoformat()}. "
            f"Verify the pair exists in instrument_master and has "
            f"ingested PX_LAST history."
        )

    # 3. Clean (ffill holiday gaps)
    clean_df = clean_single_series(raw_df, ffill_limit=ffill_limit)
    if clean_df.empty:
        raise ValueError(
            f"calculate_fx_drawdown: all values null after cleaning for "
            f"pair={params.pair!r}."
        )
    spot = clean_df["field_value"]

    # 4. Slice to the display window
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display_spot = spot.loc[spot.index >= cutoff]
    if display_spot.empty:
        raise ValueError(
            f"calculate_fx_drawdown: no observations within the last "
            f"{params.lookback_days} days for pair={params.pair!r}."
        )

    obs_count = int(len(display_spot))

    # 5. Running max + drawdown ratio (RATIO, always ≤ 0)
    running_max = display_spot.cummax()
    drawdown = (display_spot / running_max) - 1.0

    # 6. Snapshot: identify trough + the running peak associated with it
    max_dd_ts = drawdown.idxmin()
    max_dd_value = float(drawdown.loc[max_dd_ts])
    trough_value = float(display_spot.loc[max_dd_ts])
    # The peak that this trough is measured against is the running max
    # AT the trough date — which equals the spot value at the first date
    # the running max reached this level.
    peak_value_at_trough = float(running_max.loc[max_dd_ts])
    # Find the date when running_max first achieved peak_value_at_trough
    # (within the window, on or before the trough). Use idxmax of
    # (display_spot == peak_value_at_trough), but safer: walk the
    # cummax — the peak_date is the first date where spot == cummax
    # AND cummax >= peak_value_at_trough.
    pre_trough_slice = display_spot.loc[:max_dd_ts]
    is_running_max_match = pre_trough_slice >= peak_value_at_trough
    peak_candidates = pre_trough_slice[is_running_max_match]
    if peak_candidates.empty:
        # Shouldn't happen — running_max at trough is by construction
        # some past value. Defensive fallback: use the first row.
        peak_ts = pre_trough_slice.index[0]
    else:
        peak_ts = peak_candidates.index[0]

    # 7. Recovery: first date AFTER max_dd_ts where spot >= peak_value_at_trough
    post_trough_slice = display_spot.loc[display_spot.index > max_dd_ts]
    recovered = post_trough_slice[post_trough_slice >= peak_value_at_trough]
    if recovered.empty:
        recovery_ts: Optional[pd.Timestamp] = None
        time_to_recovery_days: Optional[int] = None
    else:
        recovery_ts = recovered.index[0]
        # Number of trading days between max_dd_ts (exclusive) and
        # recovery_ts (inclusive) = number of rows in display_spot
        # strictly after max_dd_ts and on/before recovery_ts.
        between = display_spot.loc[
            (display_spot.index > max_dd_ts) & (display_spot.index <= recovery_ts)
        ]
        time_to_recovery_days = int(len(between))

    # 8. Snapshot values, rounded
    current_dd_value = float(drawdown.iloc[-1])
    metrics = FXDrawdownMetrics(
        as_of_date=display_spot.index[-1].strftime("%Y-%m-%d"),
        pair=params.pair,
        current_drawdown=round(current_dd_value, dd_round_decimals),
        max_drawdown=round(max_dd_value, dd_round_decimals),
        max_drawdown_date=max_dd_ts.strftime("%Y-%m-%d"),
        peak_date=peak_ts.strftime("%Y-%m-%d"),
        peak_value=round(peak_value_at_trough, px_round_decimals),
        trough_value=round(trough_value, px_round_decimals),
        recovery_date=(
            recovery_ts.strftime("%Y-%m-%d") if recovery_ts is not None else None
        ),
        time_to_recovery_days=time_to_recovery_days,
        observation_count=obs_count,
    )

    # 9. Canonical TimeSeries
    canonical_series = _build_canonical_drawdown_time_series(
        drawdown,
        pair=params.pair,
        dd_round_decimals=dd_round_decimals,
    )

    output = FXDrawdownOutput(
        current_metrics=metrics,
        time_series=canonical_series,
    )
    return output.model_dump()


def _build_canonical_drawdown_time_series(
    drawdown: pd.Series,
    *,
    pair: str,
    dd_round_decimals: int,
) -> TimeSeries:
    """Wrap the running-drawdown series into the canonical TimeSeries
    shape with closed-enum RATIO units. Each row's value is rounded
    so the snapshot's current_drawdown equals the last row byte-for-byte.

    Naming convention: ``<pair_lower>_drawdown``.
    """
    series_name = f"{pair.lower()}_drawdown"
    rows = [
        TimeSeriesRow(
            date=ts.strftime("%Y-%m-%d"),
            value=(
                round(float(v), dd_round_decimals) if pd.notna(v) else None
            ),
        )
        for ts, v in drawdown.items()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.RATIO,
        description=(
            f"Running drawdown for {pair} over the display window. "
            f"Computed as (P_t / running_max(P_0..P_t)) - 1, always "
            f"≤ 0. Rounded to {dd_round_decimals} decimals to match "
            f"current_metrics.current_drawdown exactly at the latest "
            f"row."
        ),
        rows=rows,
    )


__all__ = [
    "CONFIG_PATH",
    "calculate_fx_drawdown",
]

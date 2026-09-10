"""compute.py — Single-pair FX rolling realized vol tool.

Phase B follow-up (2026-05-25).

Computes rolling realized vol from daily log-returns, annualized via
sqrt(252), expressed in PERCENT. Mirrors yield_levels' 4-file pattern
and the Phase B substrate discipline (no SQL ad-hoc, fetch via
shared.analytics.fx_fetch, fail-loud on data issues).
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from fx_agent.spot.tools.realized_vol.schemas import (
    FXRealizedVolInput,
    FXRealizedVolMetrics,
    FXRealizedVolOutput,
)
from shared.analytics.fx_fetch import fetch_fx_spot_series
from shared.analytics.levels import clean_single_series
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


def get_fx_realized_vol(
    engine: Engine,
    params: FXRealizedVolInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Compute the rolling annualized realized vol of one FX spot pair.

    Returns the serialised ``FXRealizedVolOutput`` as a dict — typed
    snapshot + canonical TimeSeries (PERCENT). Raises ``ValueError``
    on unknown pair / empty cleaning / zero non-NaN vol observations
    in the window.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field_name = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    annual_periods = int(config.convention_value("annualization_trading_days_per_year"))
    std_ddof = int(config.convention_value("std_ddof"))
    vol_round_decimals = int(config.convention_value("vol_round_decimals"))

    field_name_resolved = params.field_name or default_field_name

    # 1. Date window — pull enough history for the FIRST display row to
    #    have a defined rolling std. Display starts at today -
    #    lookback_days; the rolling std at that date needs window_days
    #    of prior returns; each return needs 1 prior spot row. Plus
    #    calendar-day buffer for weekends/holidays.
    buffer_calendar_days = params.window_days * 2 + 14
    start_date = date.today() - timedelta(
        days=params.lookback_days + buffer_calendar_days
    )

    # 2. Fetch via shared loader
    raw_df = fetch_fx_spot_series(
        engine=engine,
        pair=params.pair,
        start_date=start_date,
        field_name=field_name_resolved,
    )
    if raw_df.empty:
        raise ValueError(
            f"get_fx_realized_vol: no observations for pair={params.pair!r}, "
            f"field={field_name_resolved!r}, since {start_date.isoformat()}. "
            f"Verify the pair exists in instrument_master and has "
            f"ingested PX_LAST history."
        )

    # 3. Clean (ffill holiday gaps)
    clean_df = clean_single_series(raw_df, ffill_limit=ffill_limit)
    if clean_df.empty:
        raise ValueError(
            f"get_fx_realized_vol: all values null after cleaning for "
            f"pair={params.pair!r}."
        )
    spot = clean_df["field_value"]

    # 4. Daily log-returns
    log_returns = np.log(spot) - np.log(spot.shift(1))

    # 5. Rolling std with ddof from config (sample std)
    rolling_std = log_returns.rolling(
        window=params.window_days, min_periods=params.window_days
    ).std(ddof=std_ddof)

    # 6. Annualize and convert to PERCENT
    annualization_factor = math.sqrt(annual_periods)
    realized_vol_pct = rolling_std * annualization_factor * 100.0

    # 7. Slice to display window
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display = realized_vol_pct.loc[realized_vol_pct.index >= cutoff]
    if display.empty:
        raise ValueError(
            f"get_fx_realized_vol: no observations within the last "
            f"{params.lookback_days} days for pair={params.pair!r}."
        )

    # Non-NaN values within the display window — used for snapshot stats
    valid = display.dropna()
    obs_count = int(len(valid))
    if obs_count == 0:
        raise ValueError(
            f"get_fx_realized_vol: zero non-NaN realized-vol observations "
            f"in the {params.lookback_days}-day window for pair={params.pair!r}. "
            f"Possible cause: lookback_days <= window_days "
            f"({params.lookback_days} vs {params.window_days})."
        )

    # 8. Snapshot (rounded to vol_round_decimals so the snapshot equals
    #    time_series.rows[-1].value byte-for-byte)
    latest_ts = display.index[-1]
    latest_value = display.iloc[-1]
    current_vol = (
        round(float(latest_value), vol_round_decimals)
        if pd.notna(latest_value)
        else None
    )

    metrics = FXRealizedVolMetrics(
        as_of_date=latest_ts.strftime("%Y-%m-%d"),
        pair=params.pair,
        window_days=params.window_days,
        current_realized_vol_pct=current_vol,
        mean_realized_vol_pct=_safe_round(float(valid.mean()), vol_round_decimals),
        min_realized_vol_pct=_safe_round(float(valid.min()), vol_round_decimals),
        max_realized_vol_pct=_safe_round(float(valid.max()), vol_round_decimals),
        observation_count=obs_count,
    )

    # 9. Canonical TimeSeries
    canonical_series = _build_canonical_vol_time_series(
        display,
        pair=params.pair,
        window_days=params.window_days,
        vol_round_decimals=vol_round_decimals,
    )

    output = FXRealizedVolOutput(
        current_metrics=metrics,
        time_series=canonical_series,
    )
    return output.model_dump()


def _safe_round(x: float, decimals: int) -> Optional[float]:
    """Round x, mapping NaN to None for clean JSON serialisation."""
    if x is None or not np.isfinite(x):
        return None
    return round(float(x), decimals)


def _build_canonical_vol_time_series(
    display: pd.Series,
    *,
    pair: str,
    window_days: int,
    vol_round_decimals: int,
) -> TimeSeries:
    """Wrap the realized-vol series into the canonical TimeSeries shape
    with closed-enum PERCENT units. Warmup rows (where the rolling
    window isn't full yet) carry value=None.

    Naming convention: ``<pair_lower>_realized_vol_<window>d``.
    """
    series_name = f"{pair.lower()}_realized_vol_{window_days}d"
    rows = [
        TimeSeriesRow(
            date=ts.strftime("%Y-%m-%d"),
            value=(
                round(float(v), vol_round_decimals) if pd.notna(v) else None
            ),
        )
        for ts, v in display.items()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.PERCENT,
        description=(
            f"Rolling {window_days}-day annualized realized vol for "
            f"{pair} in PERCENT (8.0 = 8% / year). Computed from "
            f"daily log-returns: std * sqrt(252) * 100. Warmup rows "
            f"(first window-1 entries) carry value=None. Rounded to "
            f"{vol_round_decimals} decimals to match "
            f"current_metrics.current_realized_vol_pct exactly at the "
            f"latest row."
        ),
        rows=rows,
    )


__all__ = [
    "CONFIG_PATH",
    "get_fx_realized_vol",
]

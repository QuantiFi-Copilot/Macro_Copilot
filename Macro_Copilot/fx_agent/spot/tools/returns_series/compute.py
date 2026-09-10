"""compute.py — Single-pair FX log-returns series tool.

Phase B follow-up (2026-05-25).

Single-pair derived primitive that:
  1. Fetches the spot history via shared.analytics.fx_fetch.fetch_fx_spot_series
  2. Cleans it via shared.analytics.levels.clean_single_series
  3. Computes log-returns at the requested horizon (daily / weekly / monthly)
  4. Returns a canonical TimeSeries (RATIO units) + a snapshot summary

Mirrors ``rates_agent/sovereign_bonds/tools/yield_levels/compute.py``
in shape (config-driven, single-pair, canonical TimeSeries output).
Codex compliance discipline (2026-05-25):
  - all data loading goes through shared/analytics/fx_fetch.py
  - no SQL ad-hoc in this compute file
  - tool is a simple transformation on a single FX spot series

Test seam
---------
``fetch_fx_spot_series`` is imported at module level so unit tests can
monkeypatch it via ``patch("fx_agent.spot.tools.returns_series.compute
.fetch_fx_spot_series")``.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from fx_agent.spot.tools.returns_series.schemas import (
    FXReturnsSeriesInput,
    FXReturnsSeriesMetrics,
    FXReturnsSeriesOutput,
)
from shared.analytics.fx_fetch import fetch_fx_spot_series
from shared.analytics.levels import clean_single_series
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# ============================================================================
# PUBLIC API
# ============================================================================


def get_fx_returns_series(
    engine: Engine,
    params: FXReturnsSeriesInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Compute the log-returns time series for one FX pair at a chosen horizon.

    Returns the serialised ``FXReturnsSeriesOutput`` as a dict — typed
    snapshot + canonical TimeSeries. Raises ``ValueError`` on empty
    fetch / unknown pair / insufficient history (substrate-primitive
    fail-loud discipline, mirror calculate_fx_panel).
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    # ------------------------------------------------------------------
    # Pull conventions
    # ------------------------------------------------------------------
    default_field_name = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    return_round_decimals = int(config.convention_value("return_round_decimals"))
    horizon_periods = _horizon_to_periods(params.horizon, config)

    field_name_resolved = params.field_name or default_field_name

    # ------------------------------------------------------------------
    # 1. Date window — pull the lookback PLUS enough extra history for
    #    the horizon lag (so the first display row has a defined
    #    return). Calendar-day buffer is generous to absorb weekends
    #    and holidays.
    # ------------------------------------------------------------------
    horizon_calendar_buffer = horizon_periods * 2 + 10
    start_date = date.today() - timedelta(
        days=params.lookback_days + horizon_calendar_buffer
    )

    # ------------------------------------------------------------------
    # 2. Fetch — single-pair, via the shared FX loader
    # ------------------------------------------------------------------
    raw_df = fetch_fx_spot_series(
        engine=engine,
        pair=params.pair,
        start_date=start_date,
        field_name=field_name_resolved,
    )
    if raw_df.empty:
        raise ValueError(
            f"get_fx_returns_series: no observations for pair={params.pair!r}, "
            f"field={field_name_resolved!r}, since {start_date.isoformat()}. "
            f"Verify the pair exists in instrument_master "
            f"(attributes->>'pair') and has ingested PX_LAST history."
        )

    # ------------------------------------------------------------------
    # 3. Clean
    # ------------------------------------------------------------------
    clean_df = clean_single_series(raw_df, ffill_limit=ffill_limit)
    if clean_df.empty:
        raise ValueError(
            f"get_fx_returns_series: all values were null after cleaning "
            f"for pair={params.pair!r}."
        )
    spot = clean_df["field_value"]

    # ------------------------------------------------------------------
    # 4. Compute log-returns at the horizon
    # ------------------------------------------------------------------
    log_spot = np.log(spot)
    log_returns = log_spot - log_spot.shift(horizon_periods)
    # log_returns has the same index as spot; first horizon_periods rows
    # are NaN by construction. Keep them — they appear in the output
    # TimeSeries as value=None warmup rows.

    # ------------------------------------------------------------------
    # 5. Slice to the display window (lookback_days), anchored to today
    # ------------------------------------------------------------------
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    display = log_returns.loc[log_returns.index >= cutoff]
    if display.empty:
        raise ValueError(
            f"get_fx_returns_series: no observations within the last "
            f"{params.lookback_days} days for pair={params.pair!r}."
        )

    # Non-NaN returns within the display window — used for the snapshot
    # summary stats AND for the observation count.
    valid_returns = display.dropna()
    obs_count = int(len(valid_returns))
    if obs_count == 0:
        raise ValueError(
            f"get_fx_returns_series: zero non-NaN returns in the "
            f"{params.lookback_days}-day window for pair={params.pair!r}. "
            f"Possible cause: lookback_days <= horizon_periods "
            f"({params.lookback_days} vs ~{horizon_periods * 2})."
        )

    # ------------------------------------------------------------------
    # 6. Snapshot summary (rounded to return_round_decimals so the
    #    snapshot's current_return equals time_series.rows[-1].value
    #    byte-for-byte)
    # ------------------------------------------------------------------
    latest_ts = display.index[-1]
    latest_value = display.iloc[-1]
    current_return = (
        round(float(latest_value), return_round_decimals)
        if pd.notna(latest_value)
        else None
    )

    metrics = FXReturnsSeriesMetrics(
        as_of_date=latest_ts.strftime("%Y-%m-%d"),
        pair=params.pair,
        horizon=params.horizon,
        current_return=current_return,
        mean_return=_safe_round(float(valid_returns.mean()), return_round_decimals),
        std_return=_safe_round(float(valid_returns.std(ddof=1)) if obs_count >= 2 else math.nan, return_round_decimals),
        min_return=_safe_round(float(valid_returns.min()), return_round_decimals),
        max_return=_safe_round(float(valid_returns.max()), return_round_decimals),
        observation_count=obs_count,
    )

    # ------------------------------------------------------------------
    # 7. Canonical TimeSeries — same display slice, same rounding
    # ------------------------------------------------------------------
    canonical_series = _build_canonical_returns_time_series(
        display,
        pair=params.pair,
        horizon=params.horizon,
        return_round_decimals=return_round_decimals,
    )

    output = FXReturnsSeriesOutput(
        current_metrics=metrics,
        time_series=canonical_series,
    )
    return output.model_dump()


# ============================================================================
# HELPERS
# ============================================================================


def _horizon_to_periods(horizon: str, config: ToolConfig) -> int:
    """Resolve the closed-Literal horizon into trading-day periods.

    Fail-loud if the horizon string is somehow unknown (Pydantic
    Literal should have caught it upstream, but defensive).
    """
    key = f"horizon_{horizon}_periods"
    try:
        return int(config.convention_value(key))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"get_fx_returns_series: unknown horizon={horizon!r}; "
            f"could not resolve convention key {key!r} from config.yaml. "
            f"Allowed horizons (closed Literal): 'daily' / 'weekly' / 'monthly'."
        ) from exc


def _safe_round(x: float, decimals: int) -> Optional[float]:
    """Round x, mapping NaN to None for clean JSON serialisation."""
    if x is None or not np.isfinite(x):
        return None
    return round(float(x), decimals)


def _build_canonical_returns_time_series(
    display: pd.Series,
    *,
    pair: str,
    horizon: str,
    return_round_decimals: int,
) -> TimeSeries:
    """Wrap the in-window log-returns series into the canonical TimeSeries
    shape with closed-enum RATIO units. Warmup rows where the lag isn't
    defined yet carry value=None; the others are rounded to
    return_round_decimals so the snapshot's current_return matches the
    last row byte-for-byte.

    Naming convention: ``<pair_lower>_log_return_<horizon>``.
    """
    series_name = f"{pair.lower()}_log_return_{horizon}"
    rows = [
        TimeSeriesRow(
            date=ts.strftime("%Y-%m-%d"),
            value=(
                round(float(v), return_round_decimals)
                if pd.notna(v)
                else None
            ),
        )
        for ts, v in display.items()
    ]
    return TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.RATIO,
        description=(
            f"Historical {horizon} log-returns for {pair} over the display "
            f"window. Computed as log(P_t) - log(P_t-h) where h is the "
            f"trading-day periods for the horizon. Rounded to "
            f"{return_round_decimals} decimals to match "
            f"current_metrics.current_return exactly at the latest row. "
            f"Warmup rows (where the lag isn't yet defined) carry "
            f"value=None."
        ),
        rows=rows,
    )


__all__ = [
    "CONFIG_PATH",
    "get_fx_returns_series",
]

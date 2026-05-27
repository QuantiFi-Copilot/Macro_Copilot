"""FX vol smile aggregate snapshot compute path.

Returns the full 5-point smile (ATM + 25R + 25B + 10R + 10B) for one
(pair, tenor). Each point is computed independently from its own
series (same recipe as risk_reversal / butterfly / atm_vol_level),
then aggregated into a single response with a common as_of_date =
MIN of per-point latest dates.

Substrate routing:
  - ATM: instrument_type='fx_vol' (vol level)
  - 25R/25B/10R/10B: instrument_type='fx_vol_smile' (smile points)

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.vol._shared import smile_vendor_ticker, vol_vendor_ticker
from fx_agent.vol.tools.vol_smile.schemas import (
    FXSmilePointMetrics,
    FXVolSmileInput,
    FXVolSmileMetrics,
    FXVolSmileOutput,
)
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"
_FROZEN_TRAILING_WINDOW = 252


def _safe_abs_change(series: pd.Series, periods: int) -> Optional[float]:
    if len(series) <= periods:
        return None
    old = series.iloc[-periods - 1]
    new = series.iloc[-1]
    if old is None or pd.isna(old):
        return None
    return float(new - old)


def _round_optional(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), decimals)


def _atm_query() -> Any:
    return text(
        """
        SELECT
            d.trade_date,
            d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_vol'
          AND im.attributes ->> 'pair' = :pair
          AND im.tenor = :tenor
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )


def _smile_query() -> Any:
    return text(
        """
        SELECT
            d.trade_date,
            d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_vol_smile'
          AND im.attributes ->> 'pair' = :pair
          AND im.attributes ->> 'smile_point' = :smile_point
          AND im.tenor = :tenor
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )


def _compute_point_metrics(
    df: pd.DataFrame,
    *,
    smile_point: str,
    vendor_ticker: str,
    z_window: int,
    z_min_periods: int,
    z_ddof: int,
    trailing_window: int,
    daily_periods: int,
    weekly_periods: int,
    monthly_periods: int,
    vol_decimals: int,
    z_decimals: int,
    percentile_decimals: int,
) -> Tuple[FXSmilePointMetrics, pd.Timestamp]:
    """Compute a single FXSmilePointMetrics from a cleaned series df.

    Returns the metrics block and the series' latest trade_date timestamp
    (used by the caller to compute the aggregate as_of_date_min).
    """
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"]).sort_values("trade_date")
    if df.empty:
        raise ValueError(
            f"Smile point {smile_point} series all-NaN after cleaning."
        )

    values = df["field_value"]
    current = float(values.iloc[-1])
    latest_ts: pd.Timestamp = df["trade_date"].iloc[-1]

    trailing = values.tail(z_window)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=z_ddof)
    z_score = None
    if len(trailing) >= z_min_periods and std_ and not pd.isna(std_):
        z_score = float((current - mean_) / std_)

    range_values = values.tail(trailing_window)
    high_252d = float(range_values.max()) if not range_values.empty else None
    low_252d = float(range_values.min()) if not range_values.empty else None
    percentile_252d = None
    if high_252d is not None and low_252d is not None and high_252d != low_252d:
        percentile_252d = float((current - low_252d) / (high_252d - low_252d) * 100.0)

    metrics = FXSmilePointMetrics(
        smile_point=smile_point,
        vendor_ticker=vendor_ticker,
        as_of_date=latest_ts.strftime("%Y-%m-%d"),
        current_vol_pts=round(current, vol_decimals),
        daily_change_vol_pts=_round_optional(
            _safe_abs_change(values, daily_periods), vol_decimals
        ),
        weekly_change_vol_pts=_round_optional(
            _safe_abs_change(values, weekly_periods), vol_decimals
        ),
        monthly_change_vol_pts=_round_optional(
            _safe_abs_change(values, monthly_periods), vol_decimals
        ),
        z_score=_round_optional(z_score, z_decimals),
        high_252d=_round_optional(high_252d, vol_decimals),
        low_252d=_round_optional(low_252d, vol_decimals),
        percentile_252d=_round_optional(percentile_252d, percentile_decimals),
        observation_count=int(len(df)),
    )
    return metrics, latest_ts


def get_fx_vol_smile(
    engine: Engine,
    params: FXVolSmileInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.upper().replace("/", "").strip()
    tenor = params.tenor

    default_field = config.convention_value("default_vol_field")
    field_name = (params.field_name or default_field).upper().strip()

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    if trailing_window != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            "trailing_range_window_days is wire-frozen at 252 — schema "
            "fields high_252d / low_252d / percentile_252d embed the number (PR14)."
        )

    daily_periods = int(config.convention_value("daily_change_periods"))
    weekly_periods = int(config.convention_value("weekly_change_periods"))
    monthly_periods = int(config.convention_value("monthly_change_periods"))
    vol_decimals = int(config.convention_value("vol_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    percentile_decimals = int(config.convention_value("percentile_round_decimals"))

    # Fetch ATM + 4 smile points in a single connection scope
    with engine.connect() as conn:
        atm_df = pd.read_sql(
            _atm_query(),
            conn,
            params={
                "pair": pair,
                "tenor": tenor,
                "field_name": field_name,
                "lookback_days": params.lookback_days,
            },
        )
        smile_dfs: Dict[str, pd.DataFrame] = {}
        for smile_point in ("25R", "25B", "10R", "10B"):
            smile_dfs[smile_point] = pd.read_sql(
                _smile_query(),
                conn,
                params={
                    "pair": pair,
                    "smile_point": smile_point,
                    "tenor": tenor,
                    "field_name": field_name,
                    "lookback_days": params.lookback_days,
                },
            )

    # Fail-loud on any missing series (no partial smile)
    if atm_df.empty:
        raise ValueError(
            f"No FX ATM vol data found for pair={pair}, tenor={tenor}, "
            f"field={field_name}, lookback_days={params.lookback_days}"
        )
    for smile_point, df in smile_dfs.items():
        if df.empty:
            raise ValueError(
                f"No FX smile point data for pair={pair}, "
                f"smile_point={smile_point}, tenor={tenor}, field={field_name}, "
                f"lookback_days={params.lookback_days}"
            )

    common_kwargs = dict(
        z_window=z_window,
        z_min_periods=z_min_periods,
        z_ddof=z_ddof,
        trailing_window=trailing_window,
        daily_periods=daily_periods,
        weekly_periods=weekly_periods,
        monthly_periods=monthly_periods,
        vol_decimals=vol_decimals,
        z_decimals=z_decimals,
        percentile_decimals=percentile_decimals,
    )

    atm_metrics, atm_latest = _compute_point_metrics(
        atm_df,
        smile_point="ATM",
        vendor_ticker=vol_vendor_ticker(pair, tenor),
        **common_kwargs,
    )
    point_metrics: Dict[str, FXSmilePointMetrics] = {"ATM": atm_metrics}
    latest_dates = [atm_latest]
    for smile_point, df in smile_dfs.items():
        m, ts = _compute_point_metrics(
            df,
            smile_point=smile_point,
            vendor_ticker=smile_vendor_ticker(pair, smile_point, tenor),
            **common_kwargs,
        )
        point_metrics[smile_point] = m
        latest_dates.append(ts)

    common_as_of = min(latest_dates).strftime("%Y-%m-%d")

    metrics = FXVolSmileMetrics(
        as_of_date=common_as_of,
        pair=pair,
        tenor=tenor,
        atm=point_metrics["ATM"],
        rr_25=point_metrics["25R"],
        bf_25=point_metrics["25B"],
        rr_10=point_metrics["10R"],
        bf_10=point_metrics["10B"],
    )

    return FXVolSmileOutput(current_metrics=metrics).model_dump()

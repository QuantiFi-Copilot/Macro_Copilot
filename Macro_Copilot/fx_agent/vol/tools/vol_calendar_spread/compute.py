"""FX vol calendar-spread snapshot compute path.

Spread_t = long_vol_t - short_vol_t (signed per spread_direction).
Inner-joins two ATM implied histories by trade_date, then runs the
same snapshot + rolling 252d shape as the rest of the vol primitive
family.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR8, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.vol._shared import vol_vendor_ticker
from fx_agent.vol.tools.vol_calendar_spread.schemas import (
    FXVolCalendarSpreadInput,
    FXVolCalendarSpreadMetrics,
    FXVolCalendarSpreadOutput,
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


def _implied_query() -> Any:
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


def _fetch_leg(
    engine: Engine,
    *,
    pair: str,
    tenor: str,
    field_name: str,
    lookback_days: int,
) -> pd.Series:
    with engine.connect() as conn:
        df = pd.read_sql(
            _implied_query(),
            conn,
            params={
                "pair": pair,
                "tenor": tenor,
                "field_name": field_name,
                "lookback_days": lookback_days,
            },
        )
    if df.empty:
        raise ValueError(
            f"No FX implied vol data found for pair={pair}, tenor={tenor}, "
            f"field={field_name}, lookback_days={lookback_days}"
        )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"]).sort_values("trade_date")
    if df.empty:
        raise ValueError(
            f"FX implied vol for {pair} {tenor} all-NaN after cleaning."
        )
    return pd.Series(df["field_value"].values, index=df["trade_date"])


def get_fx_vol_calendar_spread(
    engine: Engine,
    params: FXVolCalendarSpreadInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.upper().replace("/", "").strip()
    short_tenor = params.short_tenor
    long_tenor = params.long_tenor
    vendor_ticker_short = vol_vendor_ticker(pair, short_tenor)
    vendor_ticker_long = vol_vendor_ticker(pair, long_tenor)

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

    short_series = _fetch_leg(
        engine, pair=pair, tenor=short_tenor,
        field_name=field_name, lookback_days=params.lookback_days,
    )
    long_series = _fetch_leg(
        engine, pair=pair, tenor=long_tenor,
        field_name=field_name, lookback_days=params.lookback_days,
    )

    joined = pd.concat(
        {"short": short_series, "long": long_series},
        axis=1, join="inner",
    ).dropna()
    if joined.empty:
        raise ValueError(
            f"No overlapping (short, long) implied-vol rows for pair={pair}, "
            f"short_tenor={short_tenor}, long_tenor={long_tenor}."
        )

    if params.spread_direction == "long_minus_short":
        joined["spread"] = joined["long"] - joined["short"]
    elif params.spread_direction == "short_minus_long":
        joined["spread"] = joined["short"] - joined["long"]
    else:
        raise ValueError(
            f"Unsupported spread_direction {params.spread_direction!r}."
        )

    spread_series = joined["spread"]
    current_spread = float(spread_series.iloc[-1])
    current_short = float(joined["short"].iloc[-1])
    current_long = float(joined["long"].iloc[-1])
    as_of_date = spread_series.index[-1].strftime("%Y-%m-%d")

    trailing = spread_series.tail(z_window)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=z_ddof)
    z_score = None
    if len(trailing) >= z_min_periods and std_ and not pd.isna(std_):
        z_score = float((current_spread - mean_) / std_)

    range_values = spread_series.tail(trailing_window)
    high_252d = float(range_values.max()) if not range_values.empty else None
    low_252d = float(range_values.min()) if not range_values.empty else None
    percentile_252d = None
    if high_252d is not None and low_252d is not None and high_252d != low_252d:
        percentile_252d = float(
            (current_spread - low_252d) / (high_252d - low_252d) * 100.0
        )

    metrics = FXVolCalendarSpreadMetrics(
        as_of_date=as_of_date,
        pair=pair,
        short_tenor=short_tenor,
        long_tenor=long_tenor,
        spread_direction=params.spread_direction,
        vendor_ticker_short=vendor_ticker_short,
        vendor_ticker_long=vendor_ticker_long,
        current_short_vol_pct=round(current_short, vol_decimals),
        current_long_vol_pct=round(current_long, vol_decimals),
        current_spread_vol_pts=round(current_spread, vol_decimals),
        daily_change_vol_pts=_round_optional(
            _safe_abs_change(spread_series, daily_periods), vol_decimals
        ),
        weekly_change_vol_pts=_round_optional(
            _safe_abs_change(spread_series, weekly_periods), vol_decimals
        ),
        monthly_change_vol_pts=_round_optional(
            _safe_abs_change(spread_series, monthly_periods), vol_decimals
        ),
        z_score=_round_optional(z_score, z_decimals),
        high_252d=_round_optional(high_252d, vol_decimals),
        low_252d=_round_optional(low_252d, vol_decimals),
        percentile_252d=_round_optional(percentile_252d, percentile_decimals),
        observation_count=int(len(spread_series)),
    )

    return FXVolCalendarSpreadOutput(current_metrics=metrics).model_dump()

"""FX ATM vol level snapshot compute path.

Mirror of fx_agent/spot/tools/spot_levels/compute.py but for the
ATM vol substrate. Key conceptual differences:

  - Vol values are in PERCENT (8.0 = 8% / year), not in price units.
  - Period changes are reported in ABSOLUTE vol points (not %), since
    that's the trader convention for vol moves.
  - Query joins on attributes.pair AND tenor (instead of just pair on
    fx_spot) — the fx_vol substrate has multiple tenors per pair.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.vol._shared import vol_vendor_ticker
from fx_agent.vol.tools.atm_vol_level.schemas import (
    FXAtmVolLevelInput,
    FXAtmVolLevelMetrics,
    FXAtmVolLevelOutput,
)
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"
_FROZEN_TRAILING_WINDOW = 252


def _safe_abs_change(series: pd.Series, periods: int) -> Optional[float]:
    """Absolute change (NOT percent) — vol-points convention."""
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


def _vol_query() -> Any:
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
          AND im.attributes ->> 'smile_point' = 'ATM'
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )


def get_fx_atm_vol_level(
    engine: Engine,
    params: FXAtmVolLevelInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.upper().replace("/", "").strip()
    tenor = params.tenor
    vendor_ticker = vol_vendor_ticker(pair, tenor)

    default_field = config.convention_value("default_vol_field")
    field_name = (params.field_name or default_field).upper().strip()

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    if trailing_window != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            "trailing_range_window_days is wire-frozen at 252 — see schema "
            "field names high_252d / low_252d / percentile_252d."
        )

    daily_periods = int(config.convention_value("daily_change_periods"))
    weekly_periods = int(config.convention_value("weekly_change_periods"))
    monthly_periods = int(config.convention_value("monthly_change_periods"))
    vol_decimals = int(config.convention_value("vol_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    percentile_decimals = int(config.convention_value("percentile_round_decimals"))

    with engine.connect() as conn:
        df = pd.read_sql(
            _vol_query(),
            conn,
            params={
                "pair": pair,
                "tenor": tenor,
                "field_name": field_name,
                "lookback_days": params.lookback_days,
            },
        )

    if df.empty:
        raise ValueError(
            f"No FX ATM vol data found for pair={pair}, tenor={tenor}, "
            f"field={field_name}, lookback_days={params.lookback_days}"
        )

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"]).sort_values("trade_date")
    if df.empty:
        raise ValueError(
            f"FX ATM vol data for {pair}/{tenor} all-NaN after cleaning."
        )

    values = df["field_value"]
    current = float(values.iloc[-1])
    as_of_date = df["trade_date"].iloc[-1].strftime("%Y-%m-%d")

    trailing = values.tail(z_window)
    mean_252 = trailing.mean()
    std_252 = trailing.std(ddof=z_ddof)
    z_score = None
    if len(trailing) >= z_min_periods and std_252 and not pd.isna(std_252):
        z_score = float((current - mean_252) / std_252)

    range_values = values.tail(trailing_window)
    high_252d = float(range_values.max()) if not range_values.empty else None
    low_252d = float(range_values.min()) if not range_values.empty else None
    percentile_252d = None
    if high_252d is not None and low_252d is not None and high_252d != low_252d:
        percentile_252d = float((current - low_252d) / (high_252d - low_252d) * 100.0)

    metrics = FXAtmVolLevelMetrics(
        as_of_date=as_of_date,
        pair=pair,
        tenor=tenor,
        vendor_ticker=vendor_ticker,
        current_atm_vol_pct=round(current, vol_decimals),
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

    return FXAtmVolLevelOutput(current_metrics=metrics).model_dump()

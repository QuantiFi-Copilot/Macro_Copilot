"""FX risk reversal snapshot compute path.

Mirror of fx_agent/vol/tools/atm_vol_level/compute.py for the smile
substrate. Key differences:

  - Resolves smile_point from delta_anchor (25 → '25R', 10 → '10R')
  - Queries instrument_type='fx_vol_smile' (not 'fx_vol')
  - Output unit is "vol points" not "vol pct" (RR is a differential,
    not a level — the absolute number is what trades)

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR8, PR12, PR13, PR16.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.vol._shared import smile_vendor_ticker
from fx_agent.vol.tools.risk_reversal.schemas import (
    FXRiskReversalInput,
    FXRiskReversalMetrics,
    FXRiskReversalOutput,
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


def get_fx_risk_reversal(
    engine: Engine,
    params: FXRiskReversalInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.upper().replace("/", "").strip()
    delta = int(params.delta_anchor)
    smile_point = f"{delta}R"  # delta_anchor=25 → "25R" ; delta_anchor=10 → "10R"
    tenor = params.tenor
    vendor_ticker = smile_vendor_ticker(pair, smile_point, tenor)

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

    with engine.connect() as conn:
        df = pd.read_sql(
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

    if df.empty:
        raise ValueError(
            f"No FX risk reversal data found for pair={pair}, "
            f"delta_anchor={delta}, smile_point={smile_point}, tenor={tenor}, "
            f"field={field_name}, lookback_days={params.lookback_days}"
        )

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"]).sort_values("trade_date")
    if df.empty:
        raise ValueError(
            f"FX risk reversal data for {pair}/{smile_point}/{tenor} all-NaN "
            "after cleaning."
        )

    values = df["field_value"]
    current = float(values.iloc[-1])
    as_of_date = df["trade_date"].iloc[-1].strftime("%Y-%m-%d")

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

    metrics = FXRiskReversalMetrics(
        as_of_date=as_of_date,
        pair=pair,
        delta_anchor=delta,
        smile_point=smile_point,
        tenor=tenor,
        vendor_ticker=vendor_ticker,
        current_risk_reversal_vol_pts=round(current, vol_decimals),
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

    return FXRiskReversalOutput(current_metrics=metrics).model_dump()

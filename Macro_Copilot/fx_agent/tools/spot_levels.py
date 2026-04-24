from __future__ import annotations

from typing import Optional

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.tools.schemas import FXSpotLevelInput, FXSpotLevelMetrics, FXSpotLevelOutput


def _safe_pct_change(series: pd.Series, periods: int) -> Optional[float]:
    if len(series) <= periods:
        return None
    old = series.iloc[-periods - 1]
    new = series.iloc[-1]
    if old is None or pd.isna(old) or old == 0:
        return None
    return float((new / old - 1.0) * 100.0)


def get_fx_spot_level(params: FXSpotLevelInput) -> FXSpotLevelOutput:
    pair = params.pair.upper().replace("/", "").strip()
    field_name = params.field_name.upper().strip()

    query = text("""
        SELECT
            d.trade_date,
            d.field_value::float AS field_value,
            im.vendor_ticker,
            im.attributes
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE d.field_name = :field_name
          AND im.instrument_type = 'fx_spot'
          AND (
                im.attributes ->> 'pair' = :pair
                OR replace(im.vendor_ticker, ' Curncy', '') = :pair
          )
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
    """)

    engine = get_db_engine()
    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params={
                "pair": pair,
                "field_name": field_name,
                "lookback_days": params.lookback_days,
            },
        )

    if df.empty:
        raise ValueError(f"No FX spot data found for pair={pair}, field={field_name}")

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"]).sort_values("trade_date")

    values = df["field_value"]
    current = float(values.iloc[-1])
    as_of_date = df["trade_date"].iloc[-1].strftime("%Y-%m-%d")

    trailing = values.tail(252)
    mean_252 = trailing.mean()
    std_252 = trailing.std(ddof=1)

    z_score = None
    if len(trailing) >= 20 and std_252 and not pd.isna(std_252):
        z_score = float((current - mean_252) / std_252)

    high_252d = float(trailing.max()) if not trailing.empty else None
    low_252d = float(trailing.min()) if not trailing.empty else None

    percentile_252d = None
    if high_252d is not None and low_252d is not None and high_252d != low_252d:
        percentile_252d = float((current - low_252d) / (high_252d - low_252d) * 100.0)

    metrics = FXSpotLevelMetrics(
        as_of_date=as_of_date,
        pair=pair,
        current_spot=current,
        daily_change_pct=_safe_pct_change(values, 1),
        weekly_change_pct=_safe_pct_change(values, 5),
        monthly_change_pct=_safe_pct_change(values, 21),
        z_score=z_score,
        high_252d=high_252d,
        low_252d=low_252d,
        percentile_252d=percentile_252d,
        observation_count=int(len(df)),
    )

    return FXSpotLevelOutput(current_metrics=metrics)
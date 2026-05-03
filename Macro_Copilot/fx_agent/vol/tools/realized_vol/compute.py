from __future__ import annotations

import numpy as np
import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.vol.tools.realized_vol.schemas import (
    FXRealizedVolInput,
    FXRealizedVolMetrics,
    FXRealizedVolOutput,
    FXRealizedVolTimeSeriesRow,
)


ANNUALIZATION_DAYS = 252


def _return_series(values: pd.Series, return_type: str) -> pd.Series:
    if return_type == "simple_return":
        return values.pct_change()
    return np.log(values / values.shift(1))


def get_fx_realized_vol(params: FXRealizedVolInput) -> FXRealizedVolOutput:
    pair = params.pair.upper().replace("/", "").strip()
    field_name = params.field_name.upper().strip()

    query = text(
        """
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
        """
    )

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

    if df.empty:
        raise ValueError(f"No numeric FX spot data found for pair={pair}, field={field_name}")

    values = df["field_value"]
    returns = _return_series(values, params.return_type)
    rolling_vol = (
        returns.rolling(params.window_observations, min_periods=params.window_observations)
        .std(ddof=1)
        * np.sqrt(ANNUALIZATION_DAYS)
        * 100.0
    )

    as_of_date = df["trade_date"].iloc[-1].strftime("%Y-%m-%d")
    current_spot = float(values.iloc[-1])
    current_vol = rolling_vol.iloc[-1]
    realized_vol = None if pd.isna(current_vol) else float(current_vol)

    vol_window = rolling_vol.dropna().tail(252)
    vol_z = None
    if realized_vol is not None and len(vol_window) >= 20:
        std = vol_window.std(ddof=1)
        if std and not pd.isna(std):
            vol_z = float((realized_vol - vol_window.mean()) / std)

    daily_return_pct = None
    latest_return = returns.iloc[-1]
    if not pd.isna(latest_return):
        daily_return_pct = float(latest_return * 100.0)

    rows = [
        FXRealizedVolTimeSeriesRow(
            date=pd.to_datetime(date).strftime("%Y-%m-%d"),
            realized_vol_annualized_pct=None if pd.isna(vol) else float(vol),
        )
        for date, vol in zip(df["trade_date"], rolling_vol)
    ]

    metrics = FXRealizedVolMetrics(
        as_of_date=as_of_date,
        pair=pair,
        spot=current_spot,
        window_observations=params.window_observations,
        realized_vol_annualized_pct=realized_vol,
        realized_vol_z_score=vol_z,
        daily_return_pct=daily_return_pct,
        observation_count=int(len(df)),
    )

    return FXRealizedVolOutput(current_metrics=metrics, time_series=rows)

"""FX vol rolling z-score time series compute path.

Fetches the ATM vol history for one (pair, tenor) and computes a
rolling 252-day z-score per day via pandas rolling stats. Emits only
rows where the trailing window has >= z_score_min_periods observations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.vol._shared import vol_vendor_ticker
from fx_agent.vol.tools.vol_z_score.schemas import (
    FXVolZScoreInput,
    FXVolZScoreOutput,
    FXVolZScoreRow,
    FXVolZScoreSnapshot,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


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


def get_fx_vol_z_score(
    engine: Engine,
    params: FXVolZScoreInput,
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
    vol_decimals = int(config.convention_value("vol_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))

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
    df = df.dropna(subset=["field_value"]).sort_values("trade_date").reset_index(drop=True)
    if df.empty:
        raise ValueError(
            f"FX ATM vol data for {pair}/{tenor} all-NaN after cleaning."
        )

    # Rolling 252-day z-score
    series = df["field_value"]
    rolling = series.rolling(window=z_window, min_periods=z_min_periods)
    rolling_mean = rolling.mean()
    rolling_std = rolling.std(ddof=z_ddof)
    z = (series - rolling_mean) / rolling_std
    df["z_score"] = z

    # Emit only rows with a valid z (drops rows where min_periods not reached
    # OR std == 0).
    emitted = df.dropna(subset=["z_score"]).reset_index(drop=True)

    # Legacy row shape (kept for backward compat)
    rows = [
        FXVolZScoreRow(
            trade_date=row["trade_date"].strftime("%Y-%m-%d"),
            z_score=round(float(row["z_score"]), z_decimals),
        )
        for _, row in emitted.iterrows()
    ]
    # Canonical TimeSeries shape (PR13 / event-study composable).
    ts_series_name = (
        f"{pair.lower()}_v{tenor.lower()}_atm_vol_zscore_252d"
    )
    ts_description = (
        f"Rolling 252-day z-score of {pair} {tenor} ATM implied vol "
        f"(unit: z-score; min_periods={z_min_periods}; ddof={z_ddof})."
    )
    time_series = TimeSeries(
        series_name=ts_series_name,
        units=TimeSeriesUnits.Z_SCORE,
        description=ts_description,
        rows=[
            TimeSeriesRow(
                date=row["trade_date"].strftime("%Y-%m-%d"),
                value=round(float(row["z_score"]), z_decimals),
            )
            for _, row in emitted.iterrows()
        ],
    )

    current_z = (
        round(float(emitted["z_score"].iloc[-1]), z_decimals)
        if not emitted.empty
        else None
    )
    series_min_z = (
        round(float(emitted["z_score"].min()), z_decimals) if not emitted.empty else None
    )
    series_max_z = (
        round(float(emitted["z_score"].max()), z_decimals) if not emitted.empty else None
    )
    series_mean_z = (
        round(float(emitted["z_score"].mean()), z_decimals) if not emitted.empty else None
    )

    snapshot = FXVolZScoreSnapshot(
        as_of_date=df["trade_date"].iloc[-1].strftime("%Y-%m-%d"),
        pair=pair,
        tenor=tenor,
        vendor_ticker=vendor_ticker,
        current_atm_vol_pct=round(float(series.iloc[-1]), vol_decimals),
        current_z_score=current_z,
        series_min_z=series_min_z,
        series_max_z=series_max_z,
        series_mean_z=series_mean_z,
        observation_count_full_series=int(len(df)),
        observation_count_emitted=int(len(emitted)),
    )

    return FXVolZScoreOutput(
        snapshot=snapshot,
        rows=rows,
        time_series=time_series,
        units="z_score",
    ).model_dump()

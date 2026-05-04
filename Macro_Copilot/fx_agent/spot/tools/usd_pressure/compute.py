from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.spot.tools.spot_levels import FXSpotLevelInput, get_fx_spot_level
from fx_agent.spot.tools.usd_pressure.schemas import (
    FXUSDPressureInput,
    FXUSDPressureOutput,
    FXUSDPressureRow,
)

USD_PAIRS = ["EURUSD", "GBPUSD", "AUDUSD", "USDJPY", "USDCAD", "USDCHF"]
USD_BASE = {"USDJPY", "USDCAD", "USDCHF"}


def _dxy_monthly_change(field_name: str, lookback_days: int) -> tuple[str | None, float | None]:
    query = text(
        """
        SELECT d.trade_date, d.field_value::float AS value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'risk_proxy'
          AND im.vendor_ticker = 'DXY Curncy'
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )
    engine = get_db_engine()
    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params={"field_name": field_name, "lookback_days": lookback_days},
        )
    if len(df) <= 21:
        return None, None
    values = pd.to_numeric(df["value"], errors="coerce").dropna()
    if len(values) <= 21 or values.iloc[-22] == 0:
        return None, None
    as_of = pd.to_datetime(df["trade_date"].iloc[-1]).strftime("%Y-%m-%d")
    return as_of, float((values.iloc[-1] / values.iloc[-22] - 1.0) * 100.0)


def scan_usd_pressure(params: FXUSDPressureInput) -> FXUSDPressureOutput:
    field_name = params.field_name.upper().strip()
    rows: list[FXUSDPressureRow] = []

    for pair in USD_PAIRS:
        try:
            spot = get_fx_spot_level(
                FXSpotLevelInput(
                    pair=pair,
                    lookback_days=params.lookback_days,
                    field_name=field_name,
                )
            ).current_metrics
        except Exception:
            continue

        monthly = spot.monthly_change_pct
        # Positive pressure = broad USD strength. For XXXUSD pairs, rising
        # spot means USD weakness, so invert the sign.
        pressure = None
        if monthly is not None:
            pressure = monthly if pair in USD_BASE else -monthly
        signal = "USD strength" if (pressure or 0) > 0 else "USD weakness"
        if pressure is None or abs(pressure) < 0.5:
            signal = "mixed"

        rows.append(
            FXUSDPressureRow(
                pair=pair,
                as_of_date=spot.as_of_date,
                spot=spot.current_spot,
                monthly_change_pct=monthly,
                usd_pressure_pct=pressure,
                z_score=spot.z_score,
                signal=signal,
            )
        )

    values = [row.usd_pressure_pct for row in rows if row.usd_pressure_pct is not None]
    score = float(sum(values) / len(values)) if values else 0.0
    if score > 0.5:
        regime = "broad USD strength"
    elif score < -0.5:
        regime = "broad USD weakness"
    else:
        regime = "mixed USD pressure"

    dxy_as_of, dxy_monthly = _dxy_monthly_change(field_name, params.lookback_days)
    as_of = rows[0].as_of_date if rows else dxy_as_of or ""

    return FXUSDPressureOutput(
        as_of_date=as_of,
        pressure_regime=regime,
        usd_pressure_score=round(score, 2),
        dxy_monthly_change_pct=dxy_monthly,
        rows=rows,
    )

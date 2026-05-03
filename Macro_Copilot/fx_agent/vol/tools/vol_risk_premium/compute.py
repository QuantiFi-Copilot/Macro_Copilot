from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.vol.tools.realized_vol import FXRealizedVolInput, get_fx_realized_vol
from fx_agent.vol.tools.vol_risk_premium.schemas import (
    FXVolRiskPremiumInput,
    FXVolRiskPremiumMetrics,
    FXVolRiskPremiumOutput,
    FXVolRiskPremiumTimeSeriesRow,
)


def _z_score(values: pd.Series, current: float | None = None) -> float | None:
    clean = values.dropna().tail(252)
    if len(clean) < 20:
        return None
    std = clean.std(ddof=1)
    if not std or pd.isna(std):
        return None
    level = clean.iloc[-1] if current is None else current
    return float((level - clean.mean()) / std)


def _signal(premium: float | None, premium_z: float | None) -> tuple[str, str]:
    if premium is None:
        return "fair", "neutral"
    if premium >= 2.0 or (premium_z is not None and premium_z >= 1.0):
        return "vol rich", "prefer selling vol"
    if premium <= -1.0 or (premium_z is not None and premium_z <= -1.0):
        return "vol cheap", "prefer owning vol"
    return "fair", "neutral"


def get_fx_vol_risk_premium(
    params: FXVolRiskPremiumInput,
) -> FXVolRiskPremiumOutput:
    pair = params.pair.upper().replace("/", "").strip()
    tenor = params.tenor.upper().strip()
    field_name = params.field_name.upper().strip()

    query = text(
        """
        SELECT
            d.trade_date,
            d.field_value::float AS implied_vol_pct
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
          ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_vol'
          AND d.field_name = :field_name
          AND im.attributes ->> 'pair' = :pair
          AND im.tenor = :tenor
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY d.trade_date ASC
        """
    )

    engine = get_db_engine()
    with engine.connect() as conn:
        implied_df = pd.read_sql(
            query,
            conn,
            params={
                "pair": pair,
                "tenor": tenor,
                "field_name": field_name,
                "lookback_days": params.lookback_days,
            },
        )

    if implied_df.empty:
        raise ValueError(f"No FX implied vol data found for pair={pair}, tenor={tenor}")

    implied_df["trade_date"] = pd.to_datetime(implied_df["trade_date"])
    implied_df["implied_vol_pct"] = pd.to_numeric(
        implied_df["implied_vol_pct"],
        errors="coerce",
    )
    implied = (
        implied_df.dropna(subset=["implied_vol_pct"])
        .set_index("trade_date")["implied_vol_pct"]
        .sort_index()
    )
    if implied.empty:
        raise ValueError(f"No numeric FX implied vol data found for pair={pair}, tenor={tenor}")

    realized = get_fx_realized_vol(
        FXRealizedVolInput(
            pair=pair,
            window_observations=params.realized_window_observations,
            lookback_days=params.lookback_days,
            return_type="log_return",
            field_name=field_name,
        )
    )
    realized_series = pd.Series(
        {
            pd.to_datetime(row.date): row.realized_vol_annualized_pct
            for row in realized.time_series
        },
        dtype="float64",
    ).sort_index()

    joined = pd.concat(
        [
            implied.rename("implied_vol_pct"),
            realized_series.rename("realized_vol_annualized_pct"),
        ],
        axis=1,
    ).sort_index()
    joined["vol_risk_premium_pct"] = (
        joined["implied_vol_pct"] - joined["realized_vol_annualized_pct"]
    )

    current = joined.dropna(subset=["implied_vol_pct"]).iloc[-1]
    as_of_date = current.name.strftime("%Y-%m-%d")
    implied_vol = float(current["implied_vol_pct"])
    realized_vol = (
        None
        if pd.isna(current.get("realized_vol_annualized_pct"))
        else float(current["realized_vol_annualized_pct"])
    )
    premium = (
        None
        if pd.isna(current.get("vol_risk_premium_pct"))
        else float(current["vol_risk_premium_pct"])
    )
    implied_z = _z_score(joined["implied_vol_pct"], implied_vol)
    premium_z = _z_score(joined["vol_risk_premium_pct"], premium)
    signal, expression = _signal(premium, premium_z)

    rows = [
        FXVolRiskPremiumTimeSeriesRow(
            date=pd.to_datetime(idx).strftime("%Y-%m-%d"),
            implied_vol_pct=None if pd.isna(row["implied_vol_pct"]) else float(row["implied_vol_pct"]),
            realized_vol_annualized_pct=(
                None
                if pd.isna(row["realized_vol_annualized_pct"])
                else float(row["realized_vol_annualized_pct"])
            ),
            vol_risk_premium_pct=(
                None
                if pd.isna(row["vol_risk_premium_pct"])
                else float(row["vol_risk_premium_pct"])
            ),
        )
        for idx, row in joined.iterrows()
    ]

    metrics = FXVolRiskPremiumMetrics(
        as_of_date=as_of_date,
        pair=pair,
        tenor=tenor,
        implied_vol_pct=implied_vol,
        realized_vol_annualized_pct=realized_vol,
        vol_risk_premium_pct=premium,
        implied_vol_z_score=implied_z,
        premium_z_score=premium_z,
        signal=signal,
        suggested_expression=expression,
        observation_count=int(joined["implied_vol_pct"].count()),
    )

    return FXVolRiskPremiumOutput(current_metrics=metrics, time_series=rows)

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from database.database import get_db_engine
from fx_agent.macro.tools.rates_overlay.schemas import (
    FXRatesDifferentialInput,
    FXRatesDifferentialOutput,
    FXRatesDifferentialSnapshot,
)
from fx_agent.macro.tools.trade_setup import FXTradeSetupInput, get_fx_trade_setup
from fx_agent.reference.conventions import normalize_pair, normalize_tenor


def _pct_to_bps(value: float) -> float:
    return float(value * 100.0)


def _change_bps(series: pd.Series, periods: int) -> float | None:
    if len(series) <= periods:
        return None
    old = series.iloc[-periods - 1]
    new = series.iloc[-1]
    if pd.isna(old) or pd.isna(new):
        return None
    return _pct_to_bps(float(new - old))


def _fetch_rates_differential(
    params: FXRatesDifferentialInput,
) -> tuple[FXRatesDifferentialSnapshot | None, list[str]]:
    query = text(
        """
        SELECT
            im.curve_family,
            im.tenor,
            im.instrument_type,
            d.trade_date,
            d.field_value::float AS field_value
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.curve_family IN (:curve_family_1, :curve_family_2)
          AND im.tenor = :tenor
          AND d.field_name = :field_name
          AND d.trade_date >= CURRENT_DATE - (:lookback_days || ' days')::interval
        ORDER BY im.curve_family, d.trade_date ASC
        """
    )
    engine = get_db_engine()
    with engine.connect() as conn:
        df = pd.read_sql(
            query,
            conn,
            params={
                "curve_family_1": params.rate_curve_family_1,
                "curve_family_2": params.rate_curve_family_2,
                "tenor": params.rate_tenor,
                "field_name": params.rates_field_name,
                "lookback_days": params.lookback_days,
            },
        )

    if df.empty:
        return None, [
            (
                "No rates data found for "
                f"{params.rate_curve_family_1}/{params.rate_curve_family_2} "
                f"{params.rate_tenor} field={params.rates_field_name}."
            )
        ]

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"])
    if df.empty:
        return None, ["Rates rows were present but all values were null/non-numeric."]

    available = set(df["curve_family"].unique())
    required = {params.rate_curve_family_1, params.rate_curve_family_2}
    missing = sorted(required - available)
    if missing:
        return None, [
            f"Missing rates curve(s): {', '.join(missing)} for tenor {params.rate_tenor}."
        ]

    wide = (
        df.pivot_table(
            index="trade_date",
            columns="curve_family",
            values="field_value",
            aggfunc="last",
        )
        .sort_index()
        .ffill(limit=5)
        .dropna(subset=[params.rate_curve_family_1, params.rate_curve_family_2])
    )
    if wide.empty:
        return None, ["No overlapping dates between the selected rates curves."]

    differential = wide[params.rate_curve_family_1] - wide[params.rate_curve_family_2]
    latest = wide.iloc[-1]
    label = (
        f"{params.rate_curve_family_1}-{params.rate_curve_family_2} "
        f"{params.rate_tenor}"
    )
    return (
        FXRatesDifferentialSnapshot(
            label=label,
            as_of_date=wide.index[-1].strftime("%Y-%m-%d"),
            curve_family_1_rate_pct=float(latest[params.rate_curve_family_1]),
            curve_family_2_rate_pct=float(latest[params.rate_curve_family_2]),
            differential_bps=_pct_to_bps(float(differential.iloc[-1])),
            daily_change_bps=_change_bps(differential, 1),
            monthly_change_bps=_change_bps(differential, 21),
            observation_count=int(len(wide)),
        ),
        [],
    )


def _rates_direction(pair: str, monthly_change_bps: float | None) -> str:
    if monthly_change_bps is None or abs(monthly_change_bps) < 2.0:
        return "neutral"
    # Default convention is curve 1 minus curve 2. For common XXXUSD pairs,
    # higher US-vs-foreign rates support USD and weigh on spot.
    if pair.endswith("USD"):
        return "bearish" if monthly_change_bps > 0 else "bullish"
    if pair.startswith("USD"):
        return "bullish" if monthly_change_bps > 0 else "bearish"
    return "neutral"


def _consistency(fx_direction: str, rates_direction: str) -> str:
    if rates_direction == "neutral" or fx_direction == "neutral":
        return "mixed"
    if rates_direction == fx_direction:
        return "rates confirm FX setup"
    return "rates challenge FX setup"


def get_fx_rates_differential_overlay(
    params: FXRatesDifferentialInput,
) -> FXRatesDifferentialOutput:
    pair = normalize_pair(params.pair)
    fx_tenor = normalize_tenor(params.fx_tenor)
    params = params.model_copy(
        update={
            "pair": pair,
            "fx_tenor": fx_tenor,
            "rate_curve_family_1": params.rate_curve_family_1.upper().strip(),
            "rate_curve_family_2": params.rate_curve_family_2.upper().strip(),
            "rate_tenor": params.rate_tenor.upper().strip(),
            "rates_field_name": params.rates_field_name.upper().strip(),
        }
    )

    fx_setup = get_fx_trade_setup(
        FXTradeSetupInput(
            pair=pair,
            tenor=fx_tenor,
            vol_window_observations=params.fx_vol_window_observations,
            lookback_days=params.lookback_days,
        )
    )
    rates_snapshot, missing = _fetch_rates_differential(params)

    if rates_snapshot is None:
        conclusion = (
            f"{fx_setup.summary} Rates overlay is unavailable because the "
            "selected rates curves are not populated in the database."
        )
        return FXRatesDifferentialOutput(
            pair=pair,
            fx_summary=fx_setup.summary,
            fx_direction=fx_setup.direction,
            fx_score=fx_setup.total_score,
            rates_status="missing data",
            rates_snapshot=None,
            consistency_label="rates unavailable",
            conclusion=conclusion,
            missing_data=missing,
            drivers=fx_setup.key_drivers,
            risks=[*fx_setup.risks, *missing],
            follow_ups=[
                "Run FX data health.",
                "Verify Rates/OIS ingestion in instrument_master.",
                f"Re-run {pair} vs rates differential after Rates data is loaded.",
            ],
        )

    rates_direction = _rates_direction(pair, rates_snapshot.monthly_change_bps)
    consistency = _consistency(fx_setup.direction, rates_direction)
    conclusion = (
        f"{fx_setup.summary} {rates_snapshot.label} moved "
        f"{rates_snapshot.monthly_change_bps if rates_snapshot.monthly_change_bps is not None else 0:+.1f}bp "
        f"over 1M, which is {rates_direction} for {pair}; {consistency}."
    )

    risks = list(fx_setup.risks)
    if consistency == "rates challenge FX setup":
        risks.append("Rates differential points against the FX setup.")

    return FXRatesDifferentialOutput(
        pair=pair,
        fx_summary=fx_setup.summary,
        fx_direction=fx_setup.direction,
        fx_score=fx_setup.total_score,
        rates_status="available",
        rates_snapshot=rates_snapshot,
        consistency_label=consistency,
        conclusion=conclusion,
        missing_data=[],
        drivers=[
            *fx_setup.key_drivers,
            (
                f"{rates_snapshot.label} is {rates_snapshot.differential_bps:.1f}bp "
                f"as of {rates_snapshot.as_of_date}."
            ),
        ],
        risks=risks,
        follow_ups=[
            f"Show me the {pair} trade setup.",
            f"Classify the FX regime anchored on {pair}.",
            "Compare the same FX setup with OIS pricing.",
        ],
    )


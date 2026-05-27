"""FX vol risk premium snapshot compute path.

VRP_t = implied_t - realized_t, where implied is the ATM vol at the
chosen tenor and realized is the rolling-window annualized vol on the
underlying spot at a horizon controlled by realized_window_basis.

Joins fx_vol implied with fx_spot-derived realized by trade_date,
then runs the same snapshot + rolling 252d shape as the rest of the
vol primitive family.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR8, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.vol._shared import (
    tenor_trading_days,
    vol_vendor_ticker,
)
from fx_agent.vol.tools.vol_risk_premium.schemas import (
    FXVolRiskPremiumInput,
    FXVolRiskPremiumMetrics,
    FXVolRiskPremiumOutput,
)
from shared.analytics.fx_fetch import fetch_fx_spot_series
from shared.analytics.levels import clean_single_series
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
          AND d.trade_date >= :start_date
        ORDER BY d.trade_date ASC
        """
    )


def _resolve_realized_window(
    basis: str,
    tenor: str,
    fixed_window_days: int,
) -> int:
    if basis == "tenor_matched":
        return tenor_trading_days(tenor)
    if basis == "fixed_30d":
        return fixed_window_days
    raise ValueError(
        f"Unsupported realized_window_basis {basis!r}. "
        "Expected 'tenor_matched' or 'fixed_30d'."
    )


def _compute_realized_vol_series(
    spot_df: pd.DataFrame,
    *,
    window_days: int,
    annual_periods: int,
    std_ddof: int,
    ffill_limit: int,
) -> pd.Series:
    """Return a date-indexed annualized realized-vol Series in PERCENT.

    Mirrors the fx_agent.spot.tools.realized_vol compute path — kept
    inline (not a cross-tool call) so this primitive is self-contained
    and its parity fixtures don't depend on another tool's wire shape.
    """
    df = clean_single_series(spot_df, ffill_limit=ffill_limit)
    if df.empty:
        return pd.Series(dtype="float64")
    # clean_single_series sets trade_date as the index — use df.index
    spot = df["field_value"]
    log_returns = np.log(spot) - np.log(spot.shift(1))
    rolling_std = log_returns.rolling(
        window=window_days, min_periods=window_days
    ).std(ddof=std_ddof)
    annualization = math.sqrt(annual_periods)
    realized_pct = rolling_std * annualization * 100.0
    return pd.Series(realized_pct.values, index=pd.to_datetime(df.index))


def get_fx_vol_risk_premium(
    engine: Engine,
    params: FXVolRiskPremiumInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.upper().replace("/", "").strip()
    tenor = params.tenor
    vendor_ticker_implied = vol_vendor_ticker(pair, tenor)

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
    annual_periods = int(
        config.convention_value("annualization_trading_days_per_year")
    )
    std_ddof = int(config.convention_value("std_ddof"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    fixed_window_days = int(config.convention_value("fixed_realized_window_days"))
    vol_decimals = int(config.convention_value("vol_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    percentile_decimals = int(config.convention_value("percentile_round_decimals"))

    realized_window_days = _resolve_realized_window(
        params.realized_window_basis, tenor, fixed_window_days
    )

    # Spot fetch needs enough lookback to cover the FIRST premium row's
    # realized-vol window. Budget: caller's lookback + realized window +
    # weekend/holiday buffer.
    spot_start = date.today() - timedelta(
        days=params.lookback_days + realized_window_days * 2 + 14
    )

    # Implied vol fetch — match the same start date so the joined
    # window is consistent.
    with engine.connect() as conn:
        implied_df = pd.read_sql(
            _implied_query(),
            conn,
            params={
                "pair": pair,
                "tenor": tenor,
                "field_name": field_name,
                "start_date": spot_start.isoformat(),
            },
        )

    if implied_df.empty:
        raise ValueError(
            f"No FX implied vol data found for pair={pair}, tenor={tenor}, "
            f"field={field_name} since {spot_start.isoformat()}."
        )

    implied_df["trade_date"] = pd.to_datetime(implied_df["trade_date"])
    implied_df["field_value"] = pd.to_numeric(
        implied_df["field_value"], errors="coerce"
    )
    implied_df = implied_df.dropna(subset=["field_value"]).sort_values("trade_date")
    implied_series = pd.Series(
        implied_df["field_value"].values, index=implied_df["trade_date"]
    )

    # Spot fetch via the shared loader
    spot_df = fetch_fx_spot_series(
        engine=engine,
        pair=pair,
        start_date=spot_start,
        field_name=field_name,
    )
    if spot_df.empty:
        raise ValueError(
            f"No FX spot data found for pair={pair}, field={field_name} "
            f"since {spot_start.isoformat()} — required for realized vol leg."
        )

    realized_series = _compute_realized_vol_series(
        spot_df,
        window_days=realized_window_days,
        annual_periods=annual_periods,
        std_ddof=std_ddof,
        ffill_limit=ffill_limit,
    )

    # Inner-join implied + realized by trade_date — only dates where
    # BOTH have a non-null value contribute to the VRP series.
    joined = pd.concat(
        {"implied": implied_series, "realized": realized_series},
        axis=1, join="inner",
    ).dropna()
    if joined.empty:
        raise ValueError(
            f"No overlap between implied and realized series for pair={pair}, "
            f"tenor={tenor}, realized_window={realized_window_days}d. "
            "Possible cause: lookback_days too short for the realized window."
        )

    # Slice display window to lookback_days
    cutoff = pd.Timestamp(date.today() - timedelta(days=params.lookback_days))
    joined = joined.loc[joined.index >= cutoff]
    if joined.empty:
        raise ValueError(
            f"No overlapping (implied, realized) rows within the last "
            f"{params.lookback_days} days for pair={pair}, tenor={tenor}."
        )

    joined["vrp"] = joined["implied"] - joined["realized"]
    vrp_series = joined["vrp"]
    current_vrp = float(vrp_series.iloc[-1])
    current_implied = float(joined["implied"].iloc[-1])
    current_realized = float(joined["realized"].iloc[-1])
    as_of_date = vrp_series.index[-1].strftime("%Y-%m-%d")

    trailing = vrp_series.tail(z_window)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=z_ddof)
    z_score = None
    if len(trailing) >= z_min_periods and std_ and not pd.isna(std_):
        z_score = float((current_vrp - mean_) / std_)

    range_values = vrp_series.tail(trailing_window)
    high_252d = float(range_values.max()) if not range_values.empty else None
    low_252d = float(range_values.min()) if not range_values.empty else None
    percentile_252d = None
    if high_252d is not None and low_252d is not None and high_252d != low_252d:
        percentile_252d = float(
            (current_vrp - low_252d) / (high_252d - low_252d) * 100.0
        )

    metrics = FXVolRiskPremiumMetrics(
        as_of_date=as_of_date,
        pair=pair,
        tenor=tenor,
        realized_window_basis=params.realized_window_basis,
        realized_window_days=realized_window_days,
        vendor_ticker_implied=vendor_ticker_implied,
        current_implied_vol_pct=round(current_implied, vol_decimals),
        current_realized_vol_pct=round(current_realized, vol_decimals),
        current_vol_risk_premium_vol_pts=round(current_vrp, vol_decimals),
        daily_change_vol_pts=_round_optional(
            _safe_abs_change(vrp_series, daily_periods), vol_decimals
        ),
        weekly_change_vol_pts=_round_optional(
            _safe_abs_change(vrp_series, weekly_periods), vol_decimals
        ),
        monthly_change_vol_pts=_round_optional(
            _safe_abs_change(vrp_series, monthly_periods), vol_decimals
        ),
        z_score=_round_optional(z_score, z_decimals),
        high_252d=_round_optional(high_252d, vol_decimals),
        low_252d=_round_optional(low_252d, vol_decimals),
        percentile_252d=_round_optional(percentile_252d, percentile_decimals),
        observation_count=int(len(vrp_series)),
    )

    return FXVolRiskPremiumOutput(current_metrics=metrics).model_dump()

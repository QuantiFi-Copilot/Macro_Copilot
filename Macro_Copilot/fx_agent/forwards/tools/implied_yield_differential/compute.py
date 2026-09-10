"""FX implied yield differential compute path.

Single-(pair, tenor) primitive returning the LOCAL-minus-USD rate
differential implied from forward points via CIP. Reuses the same
JPY-aware divisor + 252-day annualization as fx_carry / forward_curve
so a cross-tool identity check holds: |this.differential| ==
|fx_carry.carry_annualized_pct| for the same (pair, tenor).

Sign convention HARD-LOCKED to local_minus_usd. usd_leg_position
('base' vs 'quote') determines whether to flip the raw CIP result.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.forwards._shared import (
    points_to_spot_units,
    tenor_days_from_config,
)
from fx_agent.forwards.tools.implied_yield_differential.schemas import (
    FXImpliedYieldDifferentialInput,
    FXImpliedYieldDifferentialMetrics,
    FXImpliedYieldDifferentialOutput,
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


def _resolve_usd_leg(pair: str) -> Tuple[str, str, float]:
    """Return (usd_leg_position, local_currency, sign_for_local_minus_usd).

    sign_for_local_minus_usd is the multiplier to apply to the raw
    CIP differential (which is rate_quote - rate_base) to obtain
    local-minus-USD:
      - USDxxx: USD=base, local=quote → raw = local - USD → sign = +1
      - xxxUSD: USD=quote, local=base → raw = USD - local → sign = -1

    Raises ValueError for non-USD G10 crosses (V1 limitation).
    """
    pair = pair.upper().replace("/", "").strip()
    if len(pair) != 6:
        raise ValueError(f"pair {pair!r} must be 6 letters (ISO3 + ISO3).")
    base, quote = pair[:3], pair[3:]
    if base == "USD" and quote == "USD":
        raise ValueError(f"pair {pair!r}: cannot have USD on both legs.")
    if base == "USD":
        return ("base", quote, +1.0)
    if quote == "USD":
        return ("quote", base, -1.0)
    raise ValueError(
        f"pair {pair!r} has no USD leg — implied_yield_differential V1 "
        "requires a USD leg ('local minus USD' is undefined for non-USD "
        "crosses). Use a USDxxx or xxxUSD pair, or wait for the future "
        "quote-aware cross variant."
    )


def _history_query() -> Any:
    return text(
        """
        WITH spot_history AS (
            SELECT
                d.trade_date,
                d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND im.attributes ->> 'pair' = :pair
              AND d.field_name = :spot_field
              AND d.trade_date >= :start_date
        ),
        fwd_history AS (
            SELECT
                d.trade_date,
                d.field_value::float AS forward_points
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_forward'
              AND im.attributes ->> 'pair' = :pair
              AND im.tenor = :tenor
              AND d.field_name = :forward_field
              AND d.trade_date >= :start_date
        )
        SELECT
            s.trade_date,
            s.spot,
            f.forward_points
        FROM spot_history s
        INNER JOIN fwd_history f
            ON s.trade_date = f.trade_date
        ORDER BY s.trade_date ASC
        """
    )


def get_fx_implied_yield_differential(
    engine: Engine,
    params: FXImpliedYieldDifferentialInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.upper().replace("/", "").strip()
    usd_leg_position, local_currency, sign = _resolve_usd_leg(pair)

    tenor = params.tenor
    tenor_days_by_tenor = tenor_days_from_config(config)
    if tenor not in tenor_days_by_tenor:
        raise ValueError(
            f"Unsupported FX forward tenor: {tenor!r}. "
            f"Supported tenors are: {sorted(tenor_days_by_tenor)}."
        )
    tenor_days = tenor_days_by_tenor[tenor]
    annualization_days = int(config.convention_value("annualization_days"))
    jpy_divisor = float(config.convention_value("jpy_forward_points_divisor"))
    default_divisor = float(config.convention_value("default_forward_points_divisor"))
    spot_field = (
        params.field_name or str(config.convention_value("default_fx_spot_field"))
    )
    forward_field = (
        params.field_name or str(config.convention_value("default_fx_forward_field"))
    )

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
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    spot_decimals = int(config.convention_value("spot_round_decimals"))
    fwd_decimals = int(config.convention_value("forward_points_round_decimals"))
    yield_decimals = int(config.convention_value("yield_pct_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    percentile_decimals = int(config.convention_value("percentile_round_decimals"))

    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=int(params.lookback_days))
    ).date()

    with engine.connect() as conn:
        df = pd.read_sql(
            _history_query(),
            conn,
            params={
                "pair": pair,
                "tenor": tenor,
                "spot_field": spot_field,
                "forward_field": forward_field,
                "start_date": start_date,
            },
        )

    if df.empty:
        raise ValueError(
            f"No joined (spot, forward) history for pair={pair}, tenor={tenor}, "
            f"start_date={start_date}. Verify both legs exist in macro_data."
        )

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
    df["forward_points"] = pd.to_numeric(df["forward_points"], errors="coerce")
    df = df.dropna(subset=["spot", "forward_points"])
    df = df.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
    df = df.set_index("trade_date")
    df = df.ffill(limit=ffill_limit)
    df = df.dropna()
    if df.empty:
        raise ValueError(
            f"All-NaN after cleaning for pair={pair}, tenor={tenor}."
        )

    df["fp_spot_units"] = df["forward_points"].apply(
        lambda fp: points_to_spot_units(
            pair, fp, jpy_divisor=jpy_divisor, default_divisor=default_divisor
        )
    )
    df["outright"] = df["spot"] + df["fp_spot_units"]
    # Raw CIP differential (rate_quote - rate_base):
    df["raw_diff_pct"] = (
        (df["outright"] / df["spot"] - 1.0)
        * (annualization_days / tenor_days)
        * 100.0
    )
    # Sign-adjusted local-minus-USD:
    df["local_minus_usd_pct"] = sign * df["raw_diff_pct"]

    diff_series = df["local_minus_usd_pct"]
    current_diff = float(diff_series.iloc[-1])
    current_spot = float(df["spot"].iloc[-1])
    current_fp_raw = float(df["forward_points"].iloc[-1])
    current_fp_spot_units = float(df["fp_spot_units"].iloc[-1])
    current_outright = float(df["outright"].iloc[-1])
    as_of_date = df.index[-1].strftime("%Y-%m-%d")

    trailing = diff_series.tail(z_window)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=z_ddof)
    z_score = None
    if len(trailing) >= z_min_periods and std_ and not pd.isna(std_):
        z_score = float((current_diff - mean_) / std_)

    range_values = diff_series.tail(trailing_window)
    high_252d = float(range_values.max()) if not range_values.empty else None
    low_252d = float(range_values.min()) if not range_values.empty else None
    percentile_252d = None
    if high_252d is not None and low_252d is not None and high_252d != low_252d:
        percentile_252d = float(
            (current_diff - low_252d) / (high_252d - low_252d) * 100.0
        )

    metrics = FXImpliedYieldDifferentialMetrics(
        as_of_date=as_of_date,
        pair=pair,
        tenor=tenor,
        tenor_days=tenor_days,
        usd_leg_position=usd_leg_position,
        local_currency=local_currency,
        current_spot=round(current_spot, spot_decimals),
        current_forward_points=round(current_fp_raw, fwd_decimals),
        current_forward_points_spot_units=round(current_fp_spot_units, spot_decimals),
        current_outright_forward=round(current_outright, spot_decimals),
        current_implied_yield_differential_pct=round(current_diff, yield_decimals),
        daily_change_pct=_round_optional(
            _safe_abs_change(diff_series, daily_periods), yield_decimals
        ),
        weekly_change_pct=_round_optional(
            _safe_abs_change(diff_series, weekly_periods), yield_decimals
        ),
        monthly_change_pct=_round_optional(
            _safe_abs_change(diff_series, monthly_periods), yield_decimals
        ),
        z_score=_round_optional(z_score, z_decimals),
        high_252d=_round_optional(high_252d, yield_decimals),
        low_252d=_round_optional(low_252d, yield_decimals),
        percentile_252d=_round_optional(percentile_252d, percentile_decimals),
        observation_count=int(len(diff_series)),
    )

    return FXImpliedYieldDifferentialOutput(current_metrics=metrics).model_dump()

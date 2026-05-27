"""FX carry basket compute path — strategy index primitive.

This is a STRATEGY INDEX, NOT an executable backtest. V1 conventions
HARD-LOCKED in config:
  - rebalance_frequency_days = 21 (monthly)
  - signal_lag_days = 1 (no lookahead)
  - weighting_scheme = equal_weight within each leg
  - transaction_cost_basis = none
  - basket_construction = central methodology knob (PR8)

Compute steps (see config methodology block for the full spec):
  1. Fetch (spot, forward_points) panel for the market_scope universe.
  2. Compute per-pair carry_annualized_pct and daily log spot return
     series (the same math as fx_carry, applied cross-sectionally).
  3. Determine rebalance dates and select long / short legs at each
     rebalance by carry ranking at (rebalance_date - signal_lag).
  4. Compute daily basket return (long leg - short leg, with daily
     carry accrual) per trading day in each rebalance period.
  5. Compound into cumulative excess return index.
  6. Snapshot annualized return / vol / Sharpe / max drawdown.

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR8, PR10, PR12, PR13, PR16.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.forwards._shared import (
    points_to_spot_units,
    tenor_days_from_config,
)
from fx_agent.forwards.tools.carry_basket.schemas import (
    FXCarryBasketInput,
    FXCarryBasketOutput,
    FXCarryBasketSnapshotMetrics,
)
from shared.config import ToolConfig, load_tool_config
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# Closed map of market_scope → fx_family list. Mirror of the Pydantic
# Literal in schemas.FXCarryBasketMarketScope. NDFs deliberately
# excluded — outright unit, separate substrate.
_MARKET_SCOPE_TO_FX_FAMILIES = {
    "G10": ["G10_FORWARDS"],
    "EM": ["EM_FORWARDS"],
    "ALL": ["G10_FORWARDS", "EM_FORWARDS"],
}


def _round_optional(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), decimals)


def _history_query() -> Any:
    """Per-day join of spot and forward_points for every pair in
    market_scope at the requested tenor, over the lookback window."""
    return text(
        """
        WITH spot_history AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                d.trade_date,
                d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND d.field_name = :spot_field
              AND d.trade_date >= :start_date
        ),
        fwd_history AS (
            SELECT
                im.attributes ->> 'pair' AS pair,
                d.trade_date,
                d.field_value::float AS forward_points
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_forward'
              AND im.attributes ->> 'fx_family' = ANY(:fx_families)
              AND im.tenor = :tenor
              AND d.field_name = :forward_field
              AND d.trade_date >= :start_date
        )
        SELECT
            s.pair,
            s.trade_date,
            s.spot,
            f.forward_points
        FROM spot_history s
        INNER JOIN fwd_history f
            ON s.pair = f.pair
           AND s.trade_date = f.trade_date
        ORDER BY s.pair, s.trade_date
        """
    )


def _compute_wide_signals(
    df: pd.DataFrame,
    *,
    jpy_divisor: float,
    default_divisor: float,
    annualization_days: int,
    tenor_days: int,
    ffill_limit: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """From the long-format joined history, build two wide DataFrames:
      - carry_wide: dates × pairs, carry_annualized_pct
      - return_wide: dates × pairs, daily log spot returns
    """
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
    df["forward_points"] = pd.to_numeric(df["forward_points"], errors="coerce")
    df = df.dropna(subset=["spot", "forward_points"])

    df["fp_spot_units"] = df.apply(
        lambda r: points_to_spot_units(
            r["pair"], r["forward_points"],
            jpy_divisor=jpy_divisor, default_divisor=default_divisor,
        ),
        axis=1,
    )
    # forward_implied_rate_diff = (F/S - 1) * (annual/tenor) * 100
    # ≈ r_quote - r_base per CIP. This is the SAME number fx_carry's
    # carry_annualized_pct emits.
    df["forward_implied_rate_diff_pct"] = (
        (df["fp_spot_units"] / df["spot"])
        * (annualization_days / tenor_days)
        * 100.0
    )
    # CRITICAL SIGN CONVENTION for the strategy index:
    #
    # "long-pair carry" = carry of going LONG the pair (long base,
    # short quote) = r_base - r_quote = -forward_implied_rate_diff.
    #
    # Canonical FX carry strategy goes LONG high-yielders. In pair
    # terms: LONG USDxxx (when r_USD > r_xxx) OR LONG xxxUSD (when
    # r_xxx > r_USD). Both reduce to "long the pair when r_base >
    # r_quote", i.e. long_pair_carry > 0.
    #
    # If we ranked by forward_implied_rate_diff descending and longed
    # the top, we would systematically long LOW-yielders against
    # HIGH-yielders — the INVERTED strategy. So we negate.
    df["carry_signal_pct"] = -df["forward_implied_rate_diff_pct"]

    carry_wide = (
        df.pivot_table(
            index="trade_date", columns="pair",
            values="carry_signal_pct", aggfunc="last",
        )
        .sort_index()
        .ffill(limit=ffill_limit)
    )

    # Daily log spot returns per pair
    spot_wide = (
        df.pivot_table(
            index="trade_date", columns="pair",
            values="spot", aggfunc="last",
        )
        .sort_index()
        .ffill(limit=ffill_limit)
    )
    log_returns = np.log(spot_wide) - np.log(spot_wide.shift(1))
    return carry_wide, log_returns


def _build_rebalance_schedule(
    trading_dates: List[pd.Timestamp],
    carry_wide: pd.DataFrame,
    *,
    rebalance_period: int,
    signal_lag: int,
    top_n: int,
    basket_construction: str,
) -> List[Dict[str, Any]]:
    """Return one entry per valid rebalance period covering the trading
    window. Each entry has the signal date, period start/end indices,
    selected long/short pairs, and their average carry signal.
    """
    rebalances: List[Dict[str, Any]] = []
    # Warmup: signal_lag days needed before the first rebalance can fire.
    start_idx = signal_lag
    n_dates = len(trading_dates)
    period_idx = 0
    cursor = start_idx
    while cursor < n_dates:
        signal_idx = cursor - signal_lag
        if signal_idx < 0:
            cursor += rebalance_period
            period_idx += 1
            continue
        signal_date = trading_dates[signal_idx]
        period_start_idx = cursor
        period_end_idx = min(cursor + rebalance_period - 1, n_dates - 1)
        # Rank carry at signal_date (drop missing)
        if signal_date not in carry_wide.index:
            cursor += rebalance_period
            period_idx += 1
            continue
        signal_row = carry_wide.loc[signal_date].dropna()
        if basket_construction == "long_short_top_n":
            if len(signal_row) < 2 * top_n:
                cursor += rebalance_period
                period_idx += 1
                continue
            sorted_pairs = signal_row.sort_values(ascending=False)
            long_pairs = sorted_pairs.head(top_n).index.tolist()
            short_pairs = sorted_pairs.tail(top_n).index.tolist()
            long_signal_avg = float(signal_row[long_pairs].mean())
            short_signal_avg = float(signal_row[short_pairs].mean())
        elif basket_construction == "long_only_top_n":
            if len(signal_row) < top_n:
                cursor += rebalance_period
                period_idx += 1
                continue
            sorted_pairs = signal_row.sort_values(ascending=False)
            long_pairs = sorted_pairs.head(top_n).index.tolist()
            short_pairs = []
            long_signal_avg = float(signal_row[long_pairs].mean())
            short_signal_avg = 0.0
        else:
            raise ValueError(f"Unsupported basket_construction={basket_construction!r}")

        rebalances.append({
            "period_idx": period_idx,
            "signal_date": signal_date,
            "period_start_idx": period_start_idx,
            "period_end_idx": period_end_idx,
            "long_pairs": long_pairs,
            "short_pairs": short_pairs,
            "long_signal_avg_pct": long_signal_avg,
            "short_signal_avg_pct": short_signal_avg,
        })
        cursor += rebalance_period
        period_idx += 1
    return rebalances


def _compute_basket_returns(
    rebalances: List[Dict[str, Any]],
    trading_dates: List[pd.Timestamp],
    log_returns: pd.DataFrame,
    *,
    annualization_days: int,
    basket_construction: str,
) -> pd.Series:
    """For each rebalance period, compute the daily basket excess
    return. Concatenate into a single date-indexed Series.
    """
    pieces: List[pd.Series] = []
    for rb in rebalances:
        day_indices = range(rb["period_start_idx"], rb["period_end_idx"] + 1)
        dates = [trading_dates[i] for i in day_indices]
        if not dates:
            continue
        # Daily carry accrual is constant within the period (signal
        # value at rebalance, applied per held day).
        long_carry_daily = rb["long_signal_avg_pct"] / annualization_days / 100.0
        short_carry_daily = rb["short_signal_avg_pct"] / annualization_days / 100.0

        # Daily spot return on long leg = mean log return across long_pairs
        long_returns = log_returns.loc[dates, rb["long_pairs"]].mean(axis=1)
        if basket_construction == "long_short_top_n":
            short_returns = log_returns.loc[dates, rb["short_pairs"]].mean(axis=1)
            daily_basket = (
                (long_returns + long_carry_daily)
                - (short_returns + short_carry_daily)
            )
        else:  # long_only_top_n
            daily_basket = long_returns + long_carry_daily
        pieces.append(daily_basket)
    if not pieces:
        return pd.Series(dtype="float64")
    return pd.concat(pieces).sort_index()


def _max_drawdown_pct(cum_index: pd.Series) -> Optional[float]:
    """Largest peak-to-trough drawdown on a cumulative index series.

    cum_index is the cumulative return ratio (0.05 = +5%). Returns
    the max drawdown as a NEGATIVE percentage (e.g. -12.5 for a 12.5%
    drawdown), or None if the series is empty.
    """
    if cum_index.empty:
        return None
    # Level = 1 + cum_return (so drawdown is level / running_max - 1)
    level = 1.0 + cum_index
    running_max = level.cummax()
    drawdown_series = level / running_max - 1.0
    return float(drawdown_series.min()) * 100.0


def get_fx_carry_basket(
    engine: Engine,
    params: FXCarryBasketInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    market_scope = params.market_scope
    tenor = params.tenor
    top_n = int(params.top_n)
    basket_construction = params.basket_construction

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
    rebalance_period = int(config.convention_value("rebalance_frequency_days"))
    signal_lag = int(config.convention_value("signal_lag_days"))
    std_ddof = int(config.convention_value("std_ddof"))
    annualization_min_obs = int(config.convention_value("annualization_min_obs_days"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    return_decimals = int(config.convention_value("return_pct_round_decimals"))
    index_decimals = int(config.convention_value("index_round_decimals"))

    weighting_scheme = str(config.convention_value("weighting_scheme"))
    transaction_cost_basis = str(config.convention_value("transaction_cost_basis"))

    fx_families = _MARKET_SCOPE_TO_FX_FAMILIES[market_scope]
    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=int(params.lookback_days))
    ).date()

    with engine.connect() as conn:
        df = pd.read_sql(
            _history_query(),
            conn,
            params={
                "tenor": tenor,
                "spot_field": spot_field,
                "forward_field": forward_field,
                "start_date": start_date,
                "fx_families": fx_families,
            },
        )

    if df.empty:
        raise ValueError(
            f"No (spot, forward) history found for market_scope={market_scope}, "
            f"tenor={tenor}, since {start_date}. Verify the substrate has "
            "deliverable forward points for the requested universe."
        )

    carry_wide, log_returns = _compute_wide_signals(
        df,
        jpy_divisor=jpy_divisor,
        default_divisor=default_divisor,
        annualization_days=annualization_days,
        tenor_days=tenor_days,
        ffill_limit=ffill_limit,
    )

    trading_dates = list(carry_wide.index)
    if len(trading_dates) < signal_lag + rebalance_period:
        raise ValueError(
            f"Not enough trading dates ({len(trading_dates)}) for at least one "
            f"full rebalance period (signal_lag={signal_lag} + rebalance_period="
            f"{rebalance_period}). Increase lookback_days."
        )

    rebalances = _build_rebalance_schedule(
        trading_dates,
        carry_wide,
        rebalance_period=rebalance_period,
        signal_lag=signal_lag,
        top_n=top_n,
        basket_construction=basket_construction,
    )
    if not rebalances:
        raise ValueError(
            f"No valid rebalance periods constructed for market_scope={market_scope}, "
            f"tenor={tenor}, top_n={top_n}, basket_construction={basket_construction}. "
            "Possible cause: too few pairs in scope OR carry signal series too sparse."
        )

    daily_basket = _compute_basket_returns(
        rebalances,
        trading_dates,
        log_returns,
        annualization_days=annualization_days,
        basket_construction=basket_construction,
    ).dropna()

    if daily_basket.empty:
        raise ValueError(
            "Daily basket return series empty after rebalance computation."
        )

    # Cumulative compound index (as a return ratio: 0.05 = +5%)
    cum_index = (1.0 + daily_basket).cumprod() - 1.0

    # Snapshot stats
    obs_count = int(len(daily_basket))
    current_cum_pct = float(cum_index.iloc[-1]) * 100.0
    as_of_date = cum_index.index[-1].strftime("%Y-%m-%d")

    annualized_return_pct = None
    annualized_vol_pct = None
    sharpe_ratio = None
    if obs_count >= annualization_min_obs:
        # Geometric annualized return
        years = obs_count / annualization_days
        annualized_return_pct = float(
            (1.0 + cum_index.iloc[-1]) ** (1.0 / years) - 1.0
        ) * 100.0
        # Annualized vol
        std_daily = float(daily_basket.std(ddof=std_ddof))
        annualized_vol_pct = std_daily * math.sqrt(annualization_days) * 100.0
        if annualized_vol_pct > 0:
            sharpe_ratio = annualized_return_pct / annualized_vol_pct

    max_dd_pct = _max_drawdown_pct(cum_index)

    # Constituent pairs from the most recent rebalance
    last_rb = rebalances[-1]
    constituent_pairs: List[str] = []
    for pair in last_rb["long_pairs"]:
        constituent_pairs.append(f"{pair} (long)")
    for pair in last_rb["short_pairs"]:
        constituent_pairs.append(f"{pair} (short)")

    snapshot = FXCarryBasketSnapshotMetrics(
        as_of_date=as_of_date,
        market_scope=market_scope,
        tenor=tenor,
        top_n=top_n,
        basket_construction=basket_construction,
        rebalance_frequency_days=rebalance_period,
        signal_lag_days=signal_lag,
        weighting_scheme=weighting_scheme,
        transaction_cost_basis=transaction_cost_basis,
        current_cumulative_excess_return_pct=round(current_cum_pct, return_decimals),
        annualized_return_pct=_round_optional(annualized_return_pct, return_decimals),
        annualized_volatility_pct=_round_optional(annualized_vol_pct, return_decimals),
        sharpe_ratio=_round_optional(sharpe_ratio, return_decimals),
        max_drawdown_pct=_round_optional(max_dd_pct, return_decimals),
        observation_count=obs_count,
    )

    # Canonical TimeSeries for the cumulative index
    series_name = (
        f"fx_carry_basket_{market_scope.lower()}_{tenor.lower()}_"
        f"{basket_construction}_top{top_n}_cumulative_excess_return"
    )
    rows = [
        TimeSeriesRow(
            date=ts.strftime("%Y-%m-%d"),
            value=round(float(v), index_decimals) if pd.notna(v) else None,
        )
        for ts, v in cum_index.items()
    ]
    cum_series = TimeSeries(
        series_name=series_name,
        units=TimeSeriesUnits.RATIO,
        description=(
            f"FX carry basket cumulative excess return RATIO (0.05 = +5%) "
            f"for market_scope={market_scope} tenor={tenor} top_n={top_n} "
            f"basket_construction={basket_construction}. Strategy index — "
            "NO transaction costs / bid-ask / slippage / forward roll. "
            "NOT an executable backtest; use for relative-value / regime "
            "analysis only."
        ),
        rows=rows,
    )

    return FXCarryBasketOutput(
        snapshot=snapshot,
        cumulative_excess_return_series=cum_series,
        constituent_pairs=constituent_pairs,
    ).model_dump()

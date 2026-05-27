"""FX cross-currency basis snapshot compute path.

basis_bps = (ois_diff_pct - fx_iyd_pct) * 100
          = -(fx_iyd_pct - ois_diff_pct) * 100
Sign convention HARD-LOCKED Bloomberg BCRX-style: NEGATIVE = USD scarcity.

Composes:
  - FX leg via inline implied_yield_differential math (joined spot+forward)
  - OIS leg via shared.analytics.rates_fetch.fetch_cross_market_pair

V1 cross-domain dependency: this is the FIRST fx_agent primitive that
consumes rates_agent OIS substrate. Cross-domain pattern uses the
EXISTING shared.analytics.rates_fetch helper — no new shared infra,
no rates_agent modification.

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
from fx_agent.forwards.tools.cross_currency_basis.schemas import (
    FXCrossCurrencyBasisInput,
    FXCrossCurrencyBasisMetrics,
    FXCrossCurrencyBasisOutput,
)
from shared.analytics.rates_fetch import fetch_cross_market_pair
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"
_FROZEN_TRAILING_WINDOW = 252


# HARD-LOCKED V1 mapping (per autonomous Q3 decision): each supported
# FX pair's LOCAL currency → matching rates_agent OIS curve_family.
# USD leg is always USD_SOFR_OIS (per config convention). Phase C++
# may promote to shared.conventions if EM/extended coverage lands.
_LOCAL_CURRENCY_TO_OIS_CURVE: Dict[str, str] = {
    "EUR": "EUR_ESTR_OIS",
    "GBP": "GBP_SONIA_OIS",
    "JPY": "JPY_OIS",
    "AUD": "AUD_OIS",
    "CAD": "CAD_OIS",
}

# FX tenor → OIS tenor mapping (FX 12M aliased to OIS 1Y; rest identical).
_FX_TO_OIS_TENOR: Dict[str, str] = {
    "1W": "1W",
    "1M": "1M",
    "3M": "3M",
    "6M": "6M",
    "12M": "1Y",
}


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

    Same logic as implied_yield_differential: sign flip for xxxUSD
    pairs so the output is always LOCAL minus USD.
    """
    pair = pair.upper().replace("/", "").strip()
    base, quote = pair[:3], pair[3:]
    if base == "USD":
        return ("base", quote, +1.0)
    if quote == "USD":
        return ("quote", base, -1.0)
    raise ValueError(
        f"pair {pair!r} has no USD leg (Pydantic should have caught this earlier)."
    )


def _fx_history_query() -> Any:
    return text(
        """
        WITH spot_history AS (
            SELECT d.trade_date, d.field_value::float AS spot
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_spot'
              AND im.attributes ->> 'pair' = :pair
              AND d.field_name = :field_name
              AND d.trade_date >= :start_date
        ),
        fwd_history AS (
            SELECT d.trade_date, d.field_value::float AS forward_points
            FROM macro_data.market_data_daily d
            JOIN macro_data.instrument_master im
                ON d.instrument_id = im.instrument_id
            WHERE im.instrument_type = 'fx_forward'
              AND im.attributes ->> 'pair' = :pair
              AND im.tenor = :tenor
              AND d.field_name = :field_name
              AND d.trade_date >= :start_date
        )
        SELECT s.trade_date, s.spot, f.forward_points
        FROM spot_history s
        INNER JOIN fwd_history f ON s.trade_date = f.trade_date
        ORDER BY s.trade_date ASC
        """
    )


def _compute_fx_iyd_series(
    engine: Engine, *, pair: str, fx_tenor: str, field_name: str,
    start_date: pd.Timestamp, sign: float, jpy_divisor: float,
    default_divisor: float, annualization_days: int, tenor_days: int,
    ffill_limit: int,
) -> pd.Series:
    """Return the FX implied yield differential (LOCAL-USD) series in PERCENT,
    date-indexed. Same math as get_fx_implied_yield_differential — kept inline
    so this primitive is self-contained for parity fixtures."""
    with engine.connect() as conn:
        df = pd.read_sql(
            _fx_history_query(), conn,
            params={
                "pair": pair, "tenor": fx_tenor,
                "field_name": field_name,
                "start_date": start_date.date().isoformat(),
            },
        )
    if df.empty:
        raise ValueError(
            f"No joined FX (spot, forward) history for pair={pair}, "
            f"tenor={fx_tenor} since {start_date.date()}."
        )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
    df["forward_points"] = pd.to_numeric(df["forward_points"], errors="coerce")
    df = df.dropna(subset=["spot", "forward_points"])
    df = df.drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
    df = df.set_index("trade_date").ffill(limit=ffill_limit).dropna()
    if df.empty:
        raise ValueError(f"FX (spot, forward) all-NaN after cleaning for {pair} {fx_tenor}.")

    df["fp_spot_units"] = df["forward_points"].apply(
        lambda fp: points_to_spot_units(
            pair, fp, jpy_divisor=jpy_divisor, default_divisor=default_divisor,
        )
    )
    df["outright"] = df["spot"] + df["fp_spot_units"]
    raw_diff_pct = (
        (df["outright"] / df["spot"] - 1.0)
        * (annualization_days / tenor_days)
        * 100.0
    )
    return sign * raw_diff_pct


def _compute_ois_diff_series(
    engine: Engine, *, local_ois_curve: str, usd_ois_curve: str,
    ois_tenor: str, field_name: str, start_date: pd.Timestamp,
    ffill_limit: int,
) -> pd.Series:
    """Return the (local_ois - usd_ois) series in PERCENT, date-indexed.
    Consumes rates_agent OIS via fetch_cross_market_pair (cross-domain
    read-only; no rates_agent modification)."""
    df = fetch_cross_market_pair(
        engine=engine,
        curve_family_1=local_ois_curve,
        curve_family_2=usd_ois_curve,
        tenor=ois_tenor,
        field_name=field_name,
        start_date=start_date.date(),
    )
    if df.empty:
        raise ValueError(
            f"No OIS data for ({local_ois_curve}, {usd_ois_curve}) tenor={ois_tenor} "
            f"since {start_date.date()}."
        )
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["field_value"] = pd.to_numeric(df["field_value"], errors="coerce")
    df = df.dropna(subset=["field_value"])
    wide = df.pivot_table(
        index="trade_date", columns="curve_family",
        values="field_value", aggfunc="last",
    ).sort_index().ffill(limit=ffill_limit)
    if local_ois_curve not in wide.columns or usd_ois_curve not in wide.columns:
        raise ValueError(
            f"OIS pivot missing one of the curves: have {list(wide.columns)}, "
            f"need [{local_ois_curve}, {usd_ois_curve}]."
        )
    return wide[local_ois_curve] - wide[usd_ois_curve]


def get_fx_cross_currency_basis(
    engine: Engine,
    params: FXCrossCurrencyBasisInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.upper().replace("/", "").strip()
    usd_leg_position, local_currency, sign = _resolve_usd_leg(pair)
    if local_currency not in _LOCAL_CURRENCY_TO_OIS_CURVE:
        raise ValueError(
            f"local_currency={local_currency} has no OIS curve in V1 mapping. "
            f"Supported: {sorted(_LOCAL_CURRENCY_TO_OIS_CURVE)}"
        )
    local_ois_curve = _LOCAL_CURRENCY_TO_OIS_CURVE[local_currency]
    usd_ois_curve = str(config.convention_value("usd_ois_curve"))

    fx_tenor = params.tenor
    if fx_tenor not in _FX_TO_OIS_TENOR:
        raise ValueError(
            f"Unsupported FX tenor {fx_tenor!r}. Supported: {sorted(_FX_TO_OIS_TENOR)}"
        )
    ois_tenor = _FX_TO_OIS_TENOR[fx_tenor]

    tenor_days_by_tenor = tenor_days_from_config(config)
    tenor_days = tenor_days_by_tenor[fx_tenor]
    annualization_days = int(config.convention_value("annualization_days"))
    jpy_divisor = float(config.convention_value("jpy_forward_points_divisor"))
    default_divisor = float(config.convention_value("default_forward_points_divisor"))

    field_name = (
        params.field_name or str(config.convention_value("default_fx_spot_field"))
    )
    ois_field = (
        params.field_name or str(config.convention_value("default_ois_field"))
    )

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    if trailing_window != _FROZEN_TRAILING_WINDOW:
        raise NotImplementedError(
            "trailing_range_window_days is wire-frozen at 252 — schema "
            "fields high_252d_bps / low_252d_bps / percentile_252d embed the number (PR14)."
        )
    daily_periods = int(config.convention_value("daily_change_periods"))
    weekly_periods = int(config.convention_value("weekly_change_periods"))
    monthly_periods = int(config.convention_value("monthly_change_periods"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    basis_decimals = int(config.convention_value("basis_bps_round_decimals"))
    pct_decimals = int(config.convention_value("pct_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    percentile_decimals = int(config.convention_value("percentile_round_decimals"))

    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=int(params.lookback_days))
    )

    fx_iyd_series = _compute_fx_iyd_series(
        engine,
        pair=pair, fx_tenor=fx_tenor, field_name=field_name,
        start_date=start_date, sign=sign,
        jpy_divisor=jpy_divisor, default_divisor=default_divisor,
        annualization_days=annualization_days, tenor_days=tenor_days,
        ffill_limit=ffill_limit,
    )
    ois_diff_series = _compute_ois_diff_series(
        engine,
        local_ois_curve=local_ois_curve, usd_ois_curve=usd_ois_curve,
        ois_tenor=ois_tenor, field_name=ois_field,
        start_date=start_date, ffill_limit=ffill_limit,
    )

    # Inner-join FX × OIS by trade_date
    joined = pd.concat(
        {"fx_iyd": fx_iyd_series, "ois_diff": ois_diff_series},
        axis=1, join="inner",
    ).dropna()
    if joined.empty:
        raise ValueError(
            f"No overlap between FX iyd and OIS diff for pair={pair} tenor={fx_tenor} "
            f"({local_ois_curve} vs {usd_ois_curve}). Verify both substrates have "
            "data in the requested window."
        )

    # HARD-LOCKED Bloomberg BCRX-style sign convention:
    # basis_bps = (fx_iyd - ois_diff) * 100
    # NEGATIVE = USD scarcity
    #
    # Convention confirmed 2026-05-27 by three independent sources:
    #   1. Sreeram (rates substrate owner) via Bloomberg convention ref
    #   2. Codex algebraic derivation
    #   3. Claude concrete re-derivation (EURUSD/USD scarcity = -30bp)
    #
    # Concrete check kept inline for future-dev sanity:
    #   EURUSD, USD scarce: FX implies r_USD=5.3%, OIS r_USD=5.0%.
    #   r_EUR_OIS=3%. (F/S-1)*ann/tenor = 5.3-3 = 2.3% (USD premium via swap)
    #   fx_iyd = r_local_FX - r_USD_FX = -2.3% (EUR 2.3% below USD per FX)
    #   ois_diff = r_local_OIS - r_USD_OIS = 3-5 = -2.0% (EUR 2.0% below USD per OIS)
    #   basis = (-2.3 - (-2.0)) * 100 = -30 bps ✓ (negative = USD scarce)
    #
    # The OPPOSITE formula ((ois_diff - fx_iyd) * 100) gives +30bp which
    # contradicts Bloomberg convention — easy bug to introduce if writing
    # the formula without re-deriving from first principles. If desk
    # feedback later requests inverse sign (positive=USD-scarcity), flip
    # is isolated to this single line + parity fixture recapture.
    joined["basis_bps"] = (joined["fx_iyd"] - joined["ois_diff"]) * 100.0
    basis_series = joined["basis_bps"]

    current_basis = float(basis_series.iloc[-1])
    current_fx_iyd = float(joined["fx_iyd"].iloc[-1])
    current_ois_diff = float(joined["ois_diff"].iloc[-1])
    as_of_date = basis_series.index[-1].strftime("%Y-%m-%d")

    trailing = basis_series.tail(z_window)
    mean_ = trailing.mean()
    std_ = trailing.std(ddof=z_ddof)
    z_score = None
    if len(trailing) >= z_min_periods and std_ and not pd.isna(std_):
        z_score = float((current_basis - mean_) / std_)

    range_values = basis_series.tail(trailing_window)
    high_252d = float(range_values.max()) if not range_values.empty else None
    low_252d = float(range_values.min()) if not range_values.empty else None
    percentile_252d = None
    if high_252d is not None and low_252d is not None and high_252d != low_252d:
        percentile_252d = float(
            (current_basis - low_252d) / (high_252d - low_252d) * 100.0
        )

    metrics = FXCrossCurrencyBasisMetrics(
        as_of_date=as_of_date,
        pair=pair,
        tenor=fx_tenor,
        fx_tenor=fx_tenor,
        ois_tenor=ois_tenor,
        local_currency=local_currency,
        local_ois_curve=local_ois_curve,
        usd_ois_curve=usd_ois_curve,
        sign_convention="bloomberg_bcrx_usd_scarcity_negative",
        current_fx_implied_yield_diff_pct=round(current_fx_iyd, pct_decimals),
        current_ois_diff_pct=round(current_ois_diff, pct_decimals),
        current_basis_bps=round(current_basis, basis_decimals),
        daily_change_bps=_round_optional(
            _safe_abs_change(basis_series, daily_periods), basis_decimals
        ),
        weekly_change_bps=_round_optional(
            _safe_abs_change(basis_series, weekly_periods), basis_decimals
        ),
        monthly_change_bps=_round_optional(
            _safe_abs_change(basis_series, monthly_periods), basis_decimals
        ),
        z_score=_round_optional(z_score, z_decimals),
        high_252d_bps=_round_optional(high_252d, basis_decimals),
        low_252d_bps=_round_optional(low_252d, basis_decimals),
        percentile_252d=_round_optional(percentile_252d, percentile_decimals),
        observation_count=int(len(basis_series)),
    )

    return FXCrossCurrencyBasisOutput(current_metrics=metrics).model_dump()

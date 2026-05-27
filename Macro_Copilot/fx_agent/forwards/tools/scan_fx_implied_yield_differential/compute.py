"""FX implied yield differential scanner compute path.

Phase F1 (2026-05-27).

Cross-sectional carry leader-board. For each pair in the
universe defined by (instrument_type='fx_spot' ∩ instrument_type=
'fx_forward') at the requested tenor, computes the FX-implied
(local-USD) rate spread series and ranks the cross-section.

Math inlined from implied_yield_differential primitive (PR #237):

    iyd_pct(t) = sign * (outright(t)/spot(t) - 1) * (annual/tenor_days) * 100
    outright(t) = spot(t) + forward_points(t) * pip_divisor
    sign = +1 for USDxxx pairs (USD base), -1 for xxxUSD pairs (USD quote)

This keeps the scanner self-contained — adding a sibling helper to
_shared.py would force changes through the (frozen)
implied_yield_differential PR which we want to avoid.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.forwards._shared import (
    points_to_spot_units,
    tenor_days_from_config,
)
from fx_agent.forwards.tools.scan_fx_implied_yield_differential.schemas import (
    FXIYDScannerInput,
    FXIYDScannerOutput,
    FXIYDScannerRow,
)
from shared.analytics.fx_fetch import (
    _SCOPE_TO_FX_FORWARDS_FAMILIES,
)
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    try:
        return round(float(value), decimals)
    except (TypeError, ValueError):
        return None


def _sort_key(row: dict, rank_by: str) -> tuple:
    if rank_by == "iyd_signed":
        return (1, float(row["current_iyd_pct"]))
    if rank_by == "abs_iyd":
        return (1, abs(float(row["current_iyd_pct"])))
    if rank_by == "abs_z_score":
        z = row.get("z_score")
        return (0 if z is None else 1, abs(z) if z is not None else 0)
    raise ValueError(f"Unsupported rank_by={rank_by!r}")


def _resolve_usd_leg(pair: str) -> Tuple[str, float]:
    """Return (local_currency, sign) for LOCAL-USD output.

    Mirrors _resolve_usd_leg in cross_currency_basis but returns
    just (local_currency, sign) since we don't need the usd_leg label.
    """
    pair_clean = pair.upper().replace("/", "").strip()
    base, quote = pair_clean[:3], pair_clean[3:]
    if base == "USD":
        return (quote, +1.0)
    if quote == "USD":
        return (base, -1.0)
    raise ValueError(
        f"pair {pair!r} has no USD leg — non-USD-leg pairs not supported."
    )


def _iyd_universe_query():
    """Pull every pair with BOTH fx_spot and fx_forward instruments
    in the requested market_scope at the requested tenor."""
    return text(
        """
        SELECT DISTINCT
            spot_im.attributes ->> 'pair' AS pair
        FROM macro_data.instrument_master spot_im
        JOIN macro_data.instrument_master fwd_im
            ON spot_im.attributes ->> 'pair' = fwd_im.attributes ->> 'pair'
        WHERE spot_im.instrument_type = 'fx_spot'
          AND fwd_im.instrument_type  = 'fx_forward'
          AND fwd_im.tenor = :tenor
          AND (
            :all_scope
            OR fwd_im.attributes ->> 'fx_family' = ANY(:fx_families)
          )
        ORDER BY 1
        """
    )


def _iyd_history_query():
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


def _compute_pair_iyd_series(
    engine: Engine, *, pair: str, tenor: str, field_name: str,
    start_date: date, sign: float, jpy_divisor: float, default_divisor: float,
    annualization_days: int, tenor_days: int, ffill_limit: int,
) -> pd.Series:
    """Return the iyd series in PERCENT, date-indexed. Empty Series on no data."""
    with engine.connect() as conn:
        df = pd.read_sql(
            _iyd_history_query(), conn,
            params={
                "pair": pair, "tenor": tenor,
                "field_name": field_name,
                "start_date": start_date.isoformat(),
            },
        )
    if df.empty:
        return pd.Series(dtype=float, name=pair)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["spot"] = pd.to_numeric(df["spot"], errors="coerce")
    df["forward_points"] = pd.to_numeric(df["forward_points"], errors="coerce")
    df = (
        df.dropna(subset=["spot", "forward_points"])
        .drop_duplicates(subset=["trade_date"], keep="last")
        .sort_values("trade_date")
        .set_index("trade_date")
        .ffill(limit=ffill_limit)
        .dropna()
    )
    if df.empty:
        return pd.Series(dtype=float, name=pair)

    df["fp_spot_units"] = df["forward_points"].apply(
        lambda fp: points_to_spot_units(
            pair, fp,
            jpy_divisor=jpy_divisor, default_divisor=default_divisor,
        )
    )
    df["outright"] = df["spot"] + df["fp_spot_units"]
    raw_diff_pct = (
        (df["outright"] / df["spot"] - 1.0)
        * (annualization_days / tenor_days)
        * 100.0
    )
    series = sign * raw_diff_pct
    series.name = pair
    return series


def run_fx_implied_yield_differential_scanner(
    engine: Engine,
    params: FXIYDScannerInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    iyd_decimals = int(config.convention_value("iyd_pct_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    pct_decimals = int(config.convention_value("percentile_round_decimals"))
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    field_name = str(config.convention_value("default_fx_spot_field"))
    jpy_divisor = float(config.convention_value("jpy_forward_points_divisor"))
    default_divisor = float(config.convention_value("default_forward_points_divisor"))
    annualization_days = int(config.convention_value("annualization_days"))
    tenor_days_by_tenor = tenor_days_from_config(config)
    tenor_days = tenor_days_by_tenor[params.tenor]

    start_date = date.today() - timedelta(days=int(params.lookback_days))

    # Resolve pair universe.
    fx_families = _SCOPE_TO_FX_FORWARDS_FAMILIES[params.market_scope]
    all_scope = fx_families is None
    with engine.connect() as conn:
        rows = conn.execute(
            _iyd_universe_query(),
            {
                "tenor": params.tenor,
                "all_scope": all_scope,
                "fx_families": list(fx_families) if fx_families is not None else ["__"],
            },
        ).fetchall()
    pairs: List[str] = [str(r[0]) for r in rows if r[0]]

    snapshots: List[dict] = []
    for pair in pairs:
        try:
            local_currency, sign = _resolve_usd_leg(pair)
        except ValueError:
            continue  # crosses / non-USD pairs silently skipped
        series = _compute_pair_iyd_series(
            engine,
            pair=pair, tenor=params.tenor, field_name=field_name,
            start_date=start_date, sign=sign,
            jpy_divisor=jpy_divisor, default_divisor=default_divisor,
            annualization_days=annualization_days, tenor_days=tenor_days,
            ffill_limit=ffill_limit,
        )
        if series.empty:
            continue
        current = float(series.iloc[-1])
        as_of_date_iso = series.index[-1].strftime("%Y-%m-%d")

        trailing = series.tail(z_window)
        mean_w = trailing.mean()
        std_w = trailing.std(ddof=z_ddof)
        z_score: Optional[float] = None
        if len(trailing) >= z_min_periods and std_w and not pd.isna(std_w):
            z_score = float((current - mean_w) / std_w)

        rng = series.tail(trailing_window)
        hi = float(rng.max()) if not rng.empty else None
        lo = float(rng.min()) if not rng.empty else None
        percentile: Optional[float] = None
        if hi is not None and lo is not None and hi != lo:
            percentile = float((current - lo) / (hi - lo) * 100.0)

        snapshots.append({
            "pair": pair,
            "tenor": params.tenor,
            "as_of_date": as_of_date_iso,
            "current_iyd_pct": round(current, iyd_decimals),
            "z_score": _round(z_score, z_decimals),
            "percentile_252d": _round(percentile, pct_decimals),
            "high_252d_pct": _round(hi, iyd_decimals),
            "low_252d_pct": _round(lo, iyd_decimals),
            "observation_count": int(len(series)),
        })

    snapshots.sort(key=lambda r: _sort_key(r, params.rank_by), reverse=True)
    if params.top_n is not None:
        snapshots = snapshots[: int(params.top_n)]
    for idx, snap in enumerate(snapshots, start=1):
        snap["rank"] = idx

    rows_out = [FXIYDScannerRow(**snap) for snap in snapshots]
    return FXIYDScannerOutput(
        tenor=params.tenor,
        market_scope=params.market_scope,
        rows=rows_out,
    ).model_dump()


__all__ = [
    "CONFIG_PATH",
    "run_fx_implied_yield_differential_scanner",
]

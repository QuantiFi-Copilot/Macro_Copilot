"""FX vol cross-sectional scanner compute path.

For one tenor + market_scope, fetches every pair's ATM vol history,
computes the rolling 252-day z-score / percentile / range per pair,
ranks the cross-section, and returns the top-N rows.

Mirror of fx_agent/spot/tools/scanner.py shape (scan_fx_spot) but for
the fx_vol substrate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Engine

from fx_agent.vol._shared import fx_families_for_scope, vol_vendor_ticker
from fx_agent.vol.tools.vol_scanner.schemas import (
    FXVolScannerInput,
    FXVolScannerOutput,
    FXVolScannerRow,
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
    if rank_by == "vol_signed":
        return (1, row["current_atm_vol_pct"])
    if rank_by == "abs_vol":
        return (1, abs(row["current_atm_vol_pct"]))
    if rank_by == "abs_z_score":
        z = row.get("z_score")
        return (0 if z is None else 1, abs(z) if z is not None else 0)
    raise ValueError(f"Unsupported rank_by={rank_by!r}")


def _vol_universe_query() -> Any:
    """Pull every (pair, trade_date, vol) tuple for the requested tenor +
    fx_family list over the lookback window.
    """
    return text(
        """
        SELECT
            im.attributes ->> 'pair' AS pair,
            d.trade_date,
            d.field_value::float AS vol
        FROM macro_data.market_data_daily d
        JOIN macro_data.instrument_master im
            ON d.instrument_id = im.instrument_id
        WHERE im.instrument_type = 'fx_vol'
          AND im.attributes ->> 'fx_family' = ANY(:fx_families)
          AND im.tenor = :tenor
          AND im.attributes ->> 'smile_point' = 'ATM'
          AND d.field_name = :field_name
          AND d.trade_date >= :start_date
        ORDER BY im.attributes ->> 'pair', d.trade_date
        """
    )


def run_fx_vol_scanner(
    engine: Engine,
    params: FXVolScannerInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    tenor = params.tenor
    fx_families = fx_families_for_scope(params.market_scope)

    default_field = str(config.convention_value("default_vol_field"))
    field_name = (params.field_name or default_field).upper().strip()

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    vol_decimals = int(config.convention_value("vol_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    percentile_decimals = int(config.convention_value("percentile_round_decimals"))

    lookback_days = int(params.lookback_days)
    start_date = (
        pd.Timestamp.today().normalize() - pd.Timedelta(days=lookback_days)
    ).date()

    with engine.connect() as conn:
        df = pd.read_sql(
            _vol_universe_query(),
            conn,
            params={
                "fx_families": fx_families,
                "tenor": tenor,
                "field_name": field_name,
                "start_date": start_date,
            },
        )

    if df.empty:
        return FXVolScannerOutput(
            tenor=tenor, market_scope=params.market_scope, rows=[]
        ).model_dump()

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df["vol"] = pd.to_numeric(df["vol"], errors="coerce")
    df = df.dropna(subset=["vol"])

    snapshots: list[dict] = []
    for pair, group in df.groupby("pair", sort=False):
        group_sorted = group.sort_values("trade_date")
        if group_sorted.empty:
            continue
        values = group_sorted["vol"]
        current = float(values.iloc[-1])
        as_of_date = group_sorted["trade_date"].iloc[-1].strftime("%Y-%m-%d")

        trailing = values.tail(z_window)
        mean_w = trailing.mean()
        std_w = trailing.std(ddof=z_ddof)
        z_score = None
        if len(trailing) >= z_min_periods and std_w and not pd.isna(std_w):
            z_score = float((current - mean_w) / std_w)

        rng = values.tail(trailing_window)
        hi = float(rng.max()) if not rng.empty else None
        lo = float(rng.min()) if not rng.empty else None
        pct = None
        if hi is not None and lo is not None and hi != lo:
            pct = float((current - lo) / (hi - lo) * 100.0)

        snapshots.append({
            "pair": str(pair),
            "tenor": tenor,
            "vendor_ticker": vol_vendor_ticker(str(pair), tenor),
            "as_of_date": as_of_date,
            "current_atm_vol_pct": round(current, vol_decimals),
            "z_score": _round(z_score, z_decimals),
            "percentile_252d": _round(pct, percentile_decimals),
            "high_252d": _round(hi, vol_decimals),
            "low_252d": _round(lo, vol_decimals),
            "observation_count": int(len(group_sorted)),
        })

    snapshots.sort(key=lambda r: _sort_key(r, params.rank_by), reverse=True)
    if params.top_n is not None:
        snapshots = snapshots[: int(params.top_n)]
    for idx, snap in enumerate(snapshots, start=1):
        snap["rank"] = idx

    rows = [FXVolScannerRow(**snap) for snap in snapshots]
    return FXVolScannerOutput(
        tenor=tenor, market_scope=params.market_scope, rows=rows
    ).model_dump()

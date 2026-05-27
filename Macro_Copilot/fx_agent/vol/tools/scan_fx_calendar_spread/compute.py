"""FX vol calendar spread scanner compute path.

Phase F1 (2026-05-27).

Composes fx_vol_panel twice (front, back) at ATM, inner-joins on
(date, pair), computes spread = front - back, ranks the cross-
section. Pure composition primitive — no vol math duplication.

Output spread is signed: positive = front-elevated (vol-curve
peak at front), negative = back-elevated (vol-curve inverted).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from fx_agent.vol.tools.fx_vol_panel import (
    FXVolPanelInput,
    FXVolPanelOutput,
    calculate_fx_vol_panel,
)
from fx_agent.vol.tools.scan_fx_calendar_spread.schemas import (
    FXCalendarSpreadScannerInput,
    FXCalendarSpreadScannerOutput,
    FXCalendarSpreadScannerRow,
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
    if rank_by == "spread_signed":
        return (1, float(row["current_spread_vol_pct"]))
    if rank_by == "abs_spread":
        return (1, abs(float(row["current_spread_vol_pct"])))
    if rank_by == "abs_z_score":
        z = row.get("z_score")
        return (0 if z is None else 1, abs(z) if z is not None else 0)
    raise ValueError(f"Unsupported rank_by={rank_by!r}")


def run_fx_calendar_spread_scanner(
    engine: Engine,
    params: FXCalendarSpreadScannerInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    vol_decimals = int(config.convention_value("vol_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    pct_decimals = int(config.convention_value("percentile_round_decimals"))

    start_date = date.today() - timedelta(days=int(params.lookback_days))

    # Compose two panels (front + back). If either is empty, return empty.
    try:
        front_out = calculate_fx_vol_panel(
            engine,
            FXVolPanelInput(
                market_scope=params.market_scope,
                tenor=params.front_tenor,
                smile_point="ATM",
                start_date=start_date,
            ),
        )
        back_out = calculate_fx_vol_panel(
            engine,
            FXVolPanelInput(
                market_scope=params.market_scope,
                tenor=params.back_tenor,
                smile_point="ATM",
                start_date=start_date,
            ),
        )
    except ValueError:
        return FXCalendarSpreadScannerOutput(
            front_tenor=params.front_tenor,
            back_tenor=params.back_tenor,
            market_scope=params.market_scope,
            rows=[],
        ).model_dump()

    front_panel = FXVolPanelOutput.model_validate(front_out).panel
    back_panel = FXVolPanelOutput.model_validate(back_out).panel
    if front_panel is None or back_panel is None:
        return FXCalendarSpreadScannerOutput(
            front_tenor=params.front_tenor,
            back_tenor=params.back_tenor,
            market_scope=params.market_scope,
            rows=[],
        ).model_dump()

    front_df = front_panel.payload
    back_df = back_panel.payload

    # Inner-join: pairs present in BOTH tenors.
    common_pairs = sorted(set(front_df.columns) & set(back_df.columns))

    snapshots: List[dict] = []
    for pair in common_pairs:
        # Align dates per-pair via inner-join.
        joined = pd.concat(
            {"front": front_df[pair], "back": back_df[pair]},
            axis=1, join="inner",
        ).dropna()
        if joined.empty:
            continue
        spread_series = joined["front"] - joined["back"]
        current_spread = float(spread_series.iloc[-1])
        current_front = float(joined["front"].iloc[-1])
        current_back = float(joined["back"].iloc[-1])
        as_of_date_iso = spread_series.index[-1].strftime("%Y-%m-%d")

        trailing = spread_series.tail(z_window)
        mean_w = trailing.mean()
        std_w = trailing.std(ddof=z_ddof)
        z_score: Optional[float] = None
        if len(trailing) >= z_min_periods and std_w and not pd.isna(std_w):
            z_score = float((current_spread - mean_w) / std_w)

        rng = spread_series.tail(trailing_window)
        hi = float(rng.max()) if not rng.empty else None
        lo = float(rng.min()) if not rng.empty else None
        percentile: Optional[float] = None
        if hi is not None and lo is not None and hi != lo:
            percentile = float((current_spread - lo) / (hi - lo) * 100.0)

        snapshots.append({
            "pair": pair,
            "front_tenor": params.front_tenor,
            "back_tenor": params.back_tenor,
            "as_of_date": as_of_date_iso,
            "current_front_vol_pct": round(current_front, vol_decimals),
            "current_back_vol_pct": round(current_back, vol_decimals),
            "current_spread_vol_pct": round(current_spread, vol_decimals),
            "z_score": _round(z_score, z_decimals),
            "percentile_252d": _round(percentile, pct_decimals),
            "high_252d_pct": _round(hi, vol_decimals),
            "low_252d_pct": _round(lo, vol_decimals),
            "observation_count": int(len(spread_series)),
        })

    snapshots.sort(key=lambda r: _sort_key(r, params.rank_by), reverse=True)
    if params.top_n is not None:
        snapshots = snapshots[: int(params.top_n)]
    for idx, snap in enumerate(snapshots, start=1):
        snap["rank"] = idx

    rows_out = [FXCalendarSpreadScannerRow(**snap) for snap in snapshots]
    return FXCalendarSpreadScannerOutput(
        front_tenor=params.front_tenor,
        back_tenor=params.back_tenor,
        market_scope=params.market_scope,
        rows=rows_out,
    ).model_dump()


__all__ = [
    "CONFIG_PATH",
    "run_fx_calendar_spread_scanner",
]

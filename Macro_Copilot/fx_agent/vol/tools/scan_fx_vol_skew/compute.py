"""FX vol skew scanner compute path.

Phase F1 (2026-05-27).

Consumes fx_vol_panel at a smile_point and ranks the cross-
section by signed skew / |skew| / |z-score|. Pure composition
primitive — no vol math duplication.

Sign convention is inherited from the underlying smile_point
substrate:
  - 25R / 10R (risk reversal): POSITIVE = calls priced above
    puts; NEGATIVE = puts priced above calls.
  - 25B / 10B (butterfly): POSITIVE = smile wings rich vs ATM.
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
from fx_agent.vol.tools.scan_fx_vol_skew.schemas import (
    FXVolSkewScannerInput,
    FXVolSkewScannerOutput,
    FXVolSkewScannerRow,
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
    if rank_by == "skew_signed":
        return (1, float(row["current_skew_vol_pct"]))
    if rank_by == "abs_skew":
        return (1, abs(float(row["current_skew_vol_pct"])))
    if rank_by == "abs_z_score":
        z = row.get("z_score")
        return (0 if z is None else 1, abs(z) if z is not None else 0)
    raise ValueError(f"Unsupported rank_by={rank_by!r}")


def run_fx_vol_skew_scanner(
    engine: Engine,
    params: FXVolSkewScannerInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    skew_decimals = int(config.convention_value("skew_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    pct_decimals = int(config.convention_value("percentile_round_decimals"))

    start_date = date.today() - timedelta(days=int(params.lookback_days))

    try:
        panel_output = calculate_fx_vol_panel(
            engine,
            FXVolPanelInput(
                market_scope=params.market_scope,
                tenor=params.tenor,
                smile_point=params.smile_point,
                start_date=start_date,
            ),
        )
    except ValueError:
        return FXVolSkewScannerOutput(
            tenor=params.tenor,
            smile_point=params.smile_point,
            market_scope=params.market_scope,
            rows=[],
        ).model_dump()

    panel = FXVolPanelOutput.model_validate(panel_output).panel
    if panel is None:
        return FXVolSkewScannerOutput(
            tenor=params.tenor,
            smile_point=params.smile_point,
            market_scope=params.market_scope,
            rows=[],
        ).model_dump()

    payload = panel.payload

    snapshots: List[dict] = []
    for pair in payload.columns:
        series = payload[pair].dropna()
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
            "pair": str(pair),
            "tenor": params.tenor,
            "smile_point": params.smile_point,
            "as_of_date": as_of_date_iso,
            "current_skew_vol_pct": round(current, skew_decimals),
            "z_score": _round(z_score, z_decimals),
            "percentile_252d": _round(percentile, pct_decimals),
            "high_252d_pct": _round(hi, skew_decimals),
            "low_252d_pct": _round(lo, skew_decimals),
            "observation_count": int(len(series)),
        })

    snapshots.sort(key=lambda r: _sort_key(r, params.rank_by), reverse=True)
    if params.top_n is not None:
        snapshots = snapshots[: int(params.top_n)]
    for idx, snap in enumerate(snapshots, start=1):
        snap["rank"] = idx

    rows_out = [FXVolSkewScannerRow(**snap) for snap in snapshots]
    return FXVolSkewScannerOutput(
        tenor=params.tenor,
        smile_point=params.smile_point,
        market_scope=params.market_scope,
        rows=rows_out,
    ).model_dump()


__all__ = [
    "CONFIG_PATH",
    "run_fx_vol_skew_scanner",
]

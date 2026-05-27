"""FX cross-currency basis scanner compute path.

Phase F1 (2026-05-27).

Consumes the fx_basis_panel artifact and ranks the V1 pair set
by current basis level / |basis| / |z-score|. Pure composition
primitive — does NOT compute basis math itself.

Sign convention inherited (NEGATIVE basis = USD scarcity).
For rank_by='basis_signed', the most-negative basis ranks #1
(greatest USD scarcity first).

Operationalises: P3, P5, P11; PR1, PR4, PR5, PR7, PR10, PR13.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sqlalchemy.engine import Engine

from fx_agent.forwards.tools.fx_basis_panel import (
    FXBasisPanelInput,
    FXBasisPanelOutput,
    calculate_fx_basis_panel,
)
from fx_agent.forwards.tools.scan_fx_cross_currency_basis.schemas import (
    FXBasisScannerInput,
    FXBasisScannerOutput,
    FXBasisScannerRow,
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
    """Return a sort tuple so that descending sort puts the most
    'interesting' rows first."""
    if rank_by == "basis_signed":
        # We want most-negative first → invert by using -basis.
        return (1, -float(row["current_basis_bps"]))
    if rank_by == "abs_basis":
        return (1, abs(float(row["current_basis_bps"])))
    if rank_by == "abs_z_score":
        z = row.get("z_score")
        return (0 if z is None else 1, abs(z) if z is not None else 0)
    raise ValueError(f"Unsupported rank_by={rank_by!r}")


def run_fx_cross_currency_basis_scanner(
    engine: Engine,
    params: FXBasisScannerInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    z_window = int(config.convention_value("z_score_window_days"))
    z_min_periods = int(config.convention_value("z_score_min_periods"))
    z_ddof = int(config.convention_value("z_score_ddof"))
    trailing_window = int(config.convention_value("trailing_range_window_days"))
    basis_decimals = int(config.convention_value("basis_bps_round_decimals"))
    z_decimals = int(config.convention_value("z_score_round_decimals"))
    pct_decimals = int(config.convention_value("percentile_round_decimals"))

    start_date = date.today() - timedelta(days=int(params.lookback_days))

    panel_output = calculate_fx_basis_panel(
        engine,
        FXBasisPanelInput(
            market_scope="G10_BASIS_V1",
            tenor=params.tenor,
            start_date=start_date,
        ),
    )
    validated = FXBasisPanelOutput.model_validate(panel_output)
    if validated.panel is None:
        return FXBasisScannerOutput(tenor=params.tenor, rows=[]).model_dump()

    payload = validated.panel.payload

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
            "as_of_date": as_of_date_iso,
            "current_basis_bps": round(current, basis_decimals),
            "z_score": _round(z_score, z_decimals),
            "percentile_252d": _round(percentile, pct_decimals),
            "high_252d_bps": _round(hi, basis_decimals),
            "low_252d_bps": _round(lo, basis_decimals),
            "observation_count": int(len(series)),
        })

    snapshots.sort(key=lambda r: _sort_key(r, params.rank_by), reverse=True)
    if params.top_n is not None:
        snapshots = snapshots[: int(params.top_n)]
    for idx, snap in enumerate(snapshots, start=1):
        snap["rank"] = idx

    rows = [FXBasisScannerRow(**snap) for snap in snapshots]
    return FXBasisScannerOutput(
        tenor=params.tenor,
        rows=rows,
    ).model_dump()


__all__ = [
    "CONFIG_PATH",
    "run_fx_cross_currency_basis_scanner",
]

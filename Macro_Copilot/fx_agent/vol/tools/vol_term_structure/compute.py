"""FX vol term structure compute path.

For one pair, runs the ATM vol snapshot for every standard tenor
(1W/1M/3M/6M/12M) and assembles them in tenor-ascending order. Tenors
with no DB data are silently skipped (the returned rows list can be
shorter than 5).

Reuses atm_vol_level.get_fx_atm_vol_level as the per-tenor compute
worker so the underlying query / z-score semantics stay aligned across
the two tools.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.engine import Engine

from fx_agent.vol._shared import SUPPORTED_VOL_TENORS
from fx_agent.vol.tools.atm_vol_level import (
    FXAtmVolLevelInput,
    get_fx_atm_vol_level,
)
from fx_agent.vol.tools.vol_term_structure.schemas import (
    FXVolTermStructureInput,
    FXVolTermStructureOutput,
    FXVolTermStructureRow,
)
from shared.config import ToolConfig, load_tool_config


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


def get_fx_vol_term_structure(
    engine: Engine,
    params: FXVolTermStructureInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    pair = params.pair.upper().replace("/", "").strip()
    rows = []
    for tenor in SUPPORTED_VOL_TENORS:
        try:
            tenor_out = get_fx_atm_vol_level(
                engine,
                FXAtmVolLevelInput(
                    pair=pair,
                    tenor=tenor,
                    lookback_days=params.lookback_days,
                    field_name=params.field_name,
                ),
            )
        except ValueError:
            # Tenor not in DB for this pair — skip silently (curves can
            # be partial; the FX_VOL substrate has uneven coverage
            # across pairs).
            continue

        m = tenor_out["current_metrics"]
        rows.append(
            FXVolTermStructureRow(
                tenor=tenor,
                vendor_ticker=m["vendor_ticker"],
                as_of_date=m["as_of_date"],
                current_atm_vol_pct=m["current_atm_vol_pct"],
                daily_change_vol_pts=m["daily_change_vol_pts"],
                weekly_change_vol_pts=m["weekly_change_vol_pts"],
                monthly_change_vol_pts=m["monthly_change_vol_pts"],
                z_score=m["z_score"],
                high_252d=m["high_252d"],
                low_252d=m["low_252d"],
                percentile_252d=m["percentile_252d"],
                observation_count=m["observation_count"],
            )
        )

    if not rows:
        raise ValueError(
            f"No FX ATM vol data found for pair={pair} at any standard tenor."
        )

    return FXVolTermStructureOutput(pair=pair, rows=rows).model_dump()

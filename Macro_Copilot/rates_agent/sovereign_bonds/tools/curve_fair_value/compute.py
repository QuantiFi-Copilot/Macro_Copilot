"""compute.py — curve PCA fair-value (Bucket-2 model-state primitive).

Assembles one sovereign curve's tenor-yield Panel and runs the
finance-blind ``reconstruct_from_factors`` operator across EVERY tenor to
get each tenor's PCA fair-value residual (observed − top-k reconstruction;
positive = cheap, negative = rich), ranks the richest/cheapest point, and
surfaces the fitted PCA model state (loadings, explained-variance) read
back from the operator's lineage.

THE FINANCE this primitive owns: curve assembly, the across-tenor RANKING
+ NAMING of rich/cheap.  ZERO statistical math — the correlation-PCA +
reconstruction is the operator (``shared/quant/pca.py``); the operator
explicitly defers naming the richness to this primitive.

DESCRIBE, NOT RECOMMEND: rich/cheap is surfaced, never a buy/sell verdict.
Full-sample fit (fit_scope='full_sample', look-ahead — disclosed).

Test seam: ``fetch_instrument_panel`` is imported at module level for
monkeypatching.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.curve_fair_value.schemas import (
    CurveFairValueCurrentMetrics,
    CurveFairValueInput,
    CurveFairValueOutput,
    CurveFairValueTimeSeriesRow,
    TenorRichness,
)
from shared.analytics.panel_assembly import fetch_instrument_panel
from shared.analytics.rates_fetch import latest_trade_date
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits as ArtifactUnits
from shared.config import ToolConfig, load_tool_config
from shared.config.operator_config import load_operator_config
from shared.operators.reconstruct_from_factors import (
    CONFIG_PATH as RECONSTRUCT_CONFIG_PATH,
    ReconstructFromFactorsError,
    reconstruct_from_factors,
)
from shared.operators.reconstruct_from_factors.schemas import (
    ReconstructFromFactorsParams,
)
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.schemas import TimeSeriesUnits as SchemaUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "calculate_curve_fair_value_tool"
_TOOL_VERSION = "1.0.0"
_BPS_PER_PCT = 100.0  # percent → basis points (a mathematical truth)


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return round(float(value), decimals)


def _verdict(z: Optional[float], residual_bps: Optional[float]) -> Optional[str]:
    """Descriptive richness verdict (NEVER a buy/sell signal)."""
    if residual_bps is None:
        return None
    if z is None:
        return "cheap" if residual_bps > 0 else "rich" if residual_bps < 0 else "fair"
    if z > 1.0:
        return "cheap"        # unusually cheap vs its own history
    if z < -1.0:
        return "rich"
    return "fair"


def calculate_curve_fair_value(
    engine: Engine,
    params: CurveFairValueInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """PCA fair-value of a sovereign curve.

    Returns ``output.model_dump()`` on success or ``{"error": "..."}`` on
    a recoverable failure — never raises.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    n_components = int(config.convention_value("n_components"))
    round_dec = int(config.convention_value("fair_value_round_decimals"))

    field = params.field_name or default_field
    tenors = list(params.tenors)
    cols = [f"{params.curve_family}_{t}" for t in tenors]
    focus_col = f"{params.curve_family}_{params.focus_tenor}"

    if n_components > len(tenors):
        return {
            "error": (
                f"n_components ({n_components}) exceeds the number of "
                f"tenors ({len(tenors)}).  Request more tenors or lower "
                "n_components."
            )
        }

    # ------------------------------------------------------------------
    # 1. Fetch the curve's tenor yields → wide level Panel.
    # ------------------------------------------------------------------
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags; falls back to today
    # only when the curve has no rows (e.g. mocked engine=None in unit tests).
    anchor = (
        params.as_of_date
        or latest_trade_date(engine, curve_family=params.curve_family)
        or date.today()
    )
    start_date = anchor - timedelta(days=int(params.lookback_days))
    raw = fetch_instrument_panel(
        engine=engine,
        leg_specs=[(params.curve_family, t, field) for t in tenors],
        start_date=start_date,
        end_date=anchor,
        ffill_limit_days=ffill_limit,
    )
    if raw.empty:
        return {
            "error": (
                f"No observations for {params.curve_family} tenors {tenors} "
                f"(field {field}) since {start_date.isoformat()}."
            )
        }
    missing = [c for c in cols if c not in raw.columns or raw[c].isna().all()]
    if missing:
        return {
            "error": (
                f"No data for tenor leg(s) {missing}.  Verify each tenor "
                f"exists for {params.curve_family}, or request a different "
                "tenor set."
            )
        }

    # ------------------------------------------------------------------
    # 2. Wrap the RAW levels as a Panel (the operator z-scores internally).
    # ------------------------------------------------------------------
    panel_frame = raw[cols].astype(float)
    units_by_column = {c: ArtifactUnits.PERCENT for c in cols}
    as_of_iso = panel_frame.index[-1].strftime("%Y-%m-%d")
    step_params: Dict[str, Any] = {
        "curve_family": params.curve_family,
        "tenors": tenors,
        "field_name": field,
        "n_components": n_components,
        "n_observations": int(len(panel_frame)),
    }
    panel = Panel(
        payload=panel_frame,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([
            PrimitiveStep.build(
                name=_TOOL_NAME, version=_TOOL_VERSION, params=step_params,
                tool_config_hash=config.conventions_hash(),
                output_field="curve_panel", as_of_date=as_of_iso,
                tool_config_path=str(CONFIG_PATH),
            )
        ]),
    )

    # ------------------------------------------------------------------
    # 3. Run the fair-value residual across EVERY tenor (the operator
    #    re-fits PCA on the full Panel each call; same loadings every
    #    time).  Read the PCA model state back from the first call.
    # ------------------------------------------------------------------
    recon_config = load_operator_config(RECONSTRUCT_CONFIG_PATH)
    residual_pct: Dict[str, pd.Series] = {}
    model_state: Optional[Dict[str, Any]] = None
    for col in cols:
        try:
            res = reconstruct_from_factors(
                panel,
                params=ReconstructFromFactorsParams(
                    n_components=n_components, target_column=col,
                ),
                config=recon_config,
            )
        except ReconstructFromFactorsError as exc:
            return {"error": f"curve_fair_value: PCA residual failed — {exc}"}
        residual_pct[col] = res.payload
        if model_state is None:
            model_state = res.lineage.steps[-1].params

    assert model_state is not None  # at least one tenor ran

    # ------------------------------------------------------------------
    # 4. The FINANCE: per-tenor latest residual (bps) + full-sample
    #    z-score + rank richest/cheapest.
    # ------------------------------------------------------------------
    per_tenor: List[TenorRichness] = []
    latest_bps: Dict[str, float] = {}
    for tenor, col in zip(tenors, cols):
        series_bps = residual_pct[col] * _BPS_PER_PCT
        finite = series_bps.dropna()
        if finite.empty:
            per_tenor.append(TenorRichness(
                tenor=tenor, residual_bps=None, residual_zscore=None,
                verdict=None,
            ))
            continue
        latest = float(finite.iloc[-1])
        sd = float(finite.std(ddof=1))
        z = (latest - float(finite.mean())) / sd if sd > 0 else None
        latest_bps[tenor] = latest
        per_tenor.append(TenorRichness(
            tenor=tenor,
            residual_bps=_round(latest, round_dec),
            residual_zscore=_round(z, round_dec),
            verdict=_verdict(z, latest),
        ))

    if not latest_bps:
        return {
            "error": (
                "curve_fair_value: no tenor produced a finite residual "
                "(insufficient complete rows for the PCA fit)."
            )
        }
    cheapest = max(latest_bps, key=latest_bps.get)   # most positive = cheap
    richest = min(latest_bps, key=latest_bps.get)    # most negative = rich

    # ------------------------------------------------------------------
    # 5. The composable focus-tenor residual Series (bps) + bespoke rows.
    # ------------------------------------------------------------------
    focus_bps = (residual_pct[focus_col] * _BPS_PER_PCT)
    ts_rows: List[TimeSeriesRow] = []
    bespoke: List[CurveFairValueTimeSeriesRow] = []
    for dt, val in focus_bps.items():
        ds = dt.strftime("%Y-%m-%d")
        v = None if pd.isna(val) else round(float(val), round_dec)
        ts_rows.append(TimeSeriesRow(date=ds, value=v))
        bespoke.append(CurveFairValueTimeSeriesRow(date=ds, residual_bps=v))
    time_series_residual = TimeSeries(
        series_name=f"{params.curve_family}_{params.focus_tenor}_pca_residual",
        units=SchemaUnits.BPS,
        description=(
            f"PCA fair-value residual (observed − top-{n_components}-PC "
            f"reconstruction) for {params.curve_family} {params.focus_tenor}, "
            "in bps; +ve = cheap, -ve = rich; full-sample fit."
        ),
        rows=ts_rows,
    )

    current_metrics = CurveFairValueCurrentMetrics(
        as_of_date=as_of_iso,
        curve_family=params.curve_family,
        tenors_used=tenors,
        n_components=n_components,
        per_tenor=per_tenor,
        cheapest_tenor=cheapest,
        cheapest_residual_bps=_round(latest_bps[cheapest], round_dec),
        richest_tenor=richest,
        richest_residual_bps=_round(latest_bps[richest], round_dec),
        explained_variance_ratio=[
            _round(v, round_dec) for v in model_state["explained_variance_ratio"]
        ],
        near_degenerate=bool(model_state["near_degenerate"]),
        feature_columns=list(model_state["feature_columns"]),
        n_complete_rows=int(model_state["n_complete_rows"]),
        fit_scope=str(model_state["fit_scope"]),
        methodology_label=config.methodology.what_it_does.strip(),
    )

    disclosures = [
        f"fit_scope={model_state['fit_scope']} — full-sample PCA "
        "(look-ahead); describes relative value over the fitted window, "
        "NOT point-in-time.",
        "Positive residual = yield above curve-implied fair = CHEAP; "
        "negative = RICH.  A description, never a buy/sell signal.",
        f"Residual = observed − top-{n_components}-PC reconstruction, "
        "scaled percent→bps (×100); the operator z-scores each tenor "
        "internally.",
    ]

    output = CurveFairValueOutput(
        current_metrics=current_metrics,
        time_series_by_tenor=bespoke,
        time_series_residual=time_series_residual,
        methodology_disclosures=disclosures,
    )
    return output.model_dump()


__all__ = ["CONFIG_PATH", "calculate_curve_fair_value"]

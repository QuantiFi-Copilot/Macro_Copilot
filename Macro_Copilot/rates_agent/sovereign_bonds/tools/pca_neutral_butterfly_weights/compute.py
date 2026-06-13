"""compute.py — PCA-neutral butterfly weights (§7-C, Bucket-1B analytic).

Solves for the fly WING WEIGHTS (w_short, w_long) that make a butterfly
neutral to the first one or two principal components of the curve's yield
CHANGES, with the belly weight fixed.  Fits correlation PCA on the
yield-change panel (shared/quant/pca.py), reads the loadings at the three
fly tenors, and solves a 2×2 linear system.  Pure deterministic linear
algebra — no model state.

Test seam: ``fetch_instrument_panel`` is imported at module level for
monkeypatching.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.pca_neutral_butterfly_weights.schemas import (
    PcaNeutralButterflyWeightsCurrentMetrics,
    PcaNeutralButterflyWeightsInput,
    PcaNeutralButterflyWeightsOutput,
    PcaNeutralButterflyWeightsTimeSeriesRow,
    PcResidualExposure,
)
from shared.analytics.curve_bootstrap import sort_tenors_by_years, tenor_to_years
from shared.analytics.panel_assembly import fetch_instrument_panel
from shared.config import ToolConfig, load_tool_config
from shared.quant.pca import fit_pca
from shared.schemas import TimeSeries, TimeSeriesRow, TimeSeriesUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"
_BPS_PER_PCT = 100.0
# PCs reported in the residual-exposure diagnostic (PC1/PC2/PC3 when the fit
# allows); the solve uses only PC1 (+ PC2 when n_pcs_to_neutralize=2).
_N_REPORT_PCS = 3


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    return round(float(value), decimals)


def calculate_pca_neutral_butterfly_weights(
    engine: Engine,
    params: PcaNeutralButterflyWeightsInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Solve for the PCA-neutral butterfly wing weights.

    Returns ``output.model_dump()`` on success or ``{"error": "..."}`` on a
    recoverable failure — never raises.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    belly_weight = float(config.convention_value("belly_weight"))
    round_dec = int(config.convention_value("fly_round_decimals"))

    field = params.field_name or default_field
    n_pcs = int(params.n_pcs_to_neutralize)
    fly = [params.short_tenor, params.belly_tenor, params.long_tenor]

    # Fit-tenor set = the requested broad set ∪ the three fly tenors, sorted.
    try:
        fit_tenors = sort_tenors_by_years(list(set(params.fit_tenors) | set(fly)))
    except (ValueError, KeyError) as exc:
        return {"error": f"pca_neutral_butterfly_weights: bad tenor — {exc}"}
    cols = [f"{params.curve_family}_{t}" for t in fit_tenors]
    n_features = len(fit_tenors)
    if n_features < 3:
        return {"error": "pca_neutral_butterfly_weights: need >= 3 fit tenors."}

    # ------------------------------------------------------------------
    # 1. Fetch the yield panel.
    # ------------------------------------------------------------------
    start_date = date.today() - timedelta(days=int(params.lookback_days))
    raw = fetch_instrument_panel(
        engine=engine,
        leg_specs=[(params.curve_family, t, field) for t in fit_tenors],
        start_date=start_date, end_date=None, ffill_limit_days=ffill_limit,
    )
    if raw.empty:
        return {
            "error": (
                f"No observations for {params.curve_family} fit tenors "
                f"{fit_tenors} (field {field}) since {start_date.isoformat()}."
            )
        }
    missing = [c for c in cols if c not in raw.columns or raw[c].isna().all()]
    if missing:
        return {"error": f"No data for tenor leg(s) {missing}."}

    levels = raw[cols].astype(float)

    # ------------------------------------------------------------------
    # 2. PCA on the yield CHANGES (complete-case; the movement basis).
    # ------------------------------------------------------------------
    changes = levels.diff().dropna(how="any")
    n_components = min(_N_REPORT_PCS, n_features)
    if len(changes) < max(n_features + 1, 12):
        return {
            "error": (
                f"pca_neutral_butterfly_weights: only {len(changes)} complete "
                "change rows — too few to fit a stable PCA."
            )
        }
    try:
        fit = fit_pca(changes.to_numpy(dtype=float), n_components)
    except ValueError as exc:
        return {"error": f"pca_neutral_butterfly_weights: PCA failed — {exc}"}

    loadings = fit.loadings  # (n_components, n_features)
    i_s = fit_tenors.index(params.short_tenor)
    i_b = fit_tenors.index(params.belly_tenor)
    i_l = fit_tenors.index(params.long_tenor)

    # ------------------------------------------------------------------
    # 3. Solve the 2×2 for the wing weights (belly weight fixed).  A PC
    #    sign flip multiplies a whole equation by ±1, leaving the solution
    #    unchanged — deterministic regardless of the sign convention.
    # ------------------------------------------------------------------
    if n_pcs == 2 and n_components >= 2:
        A = np.array([
            [loadings[0, i_s], loadings[0, i_l]],
            [loadings[1, i_s], loadings[1, i_l]],
        ])
        b = -belly_weight * np.array([loadings[0, i_b], loadings[1, i_b]])
    else:  # n_pcs == 1: PC1-neutral + cash-neutral normalization
        A = np.array([
            [loadings[0, i_s], loadings[0, i_l]],
            [1.0, 1.0],
        ])
        b = np.array([-belly_weight * loadings[0, i_b], -belly_weight])
    if abs(float(np.linalg.det(A))) < 1e-12:
        return {
            "error": (
                "pca_neutral_butterfly_weights: the PCA-loading system is "
                "singular for these fly tenors (the wings are not "
                "PC-distinguishable) — choose more separated wings."
            )
        }
    w_short, w_long = (float(v) for v in np.linalg.solve(A, b))

    # ------------------------------------------------------------------
    # 4. Residual per-PC exposures (≈0 for the neutralized PCs).
    # ------------------------------------------------------------------
    residual: List[PcResidualExposure] = []
    for k in range(n_components):
        fly_loading = (
            w_short * loadings[k, i_s]
            + belly_weight * loadings[k, i_b]
            + w_long * loadings[k, i_l]
        )
        residual.append(PcResidualExposure(
            pc=k + 1,
            neutralized=(k < n_pcs),
            fly_loading=_round(float(fly_loading), round_dec),
            explained_variance_ratio=_round(
                float(fit.explained_variance_ratio[k]), round_dec),
        ))

    # ------------------------------------------------------------------
    # 5. The PCA-neutral fly series (on the LEVELS, in bps).
    # ------------------------------------------------------------------
    fly_series = (
        w_short * levels[cols[i_s]]
        + belly_weight * levels[cols[i_b]]
        + w_long * levels[cols[i_l]]
    ) * _BPS_PER_PCT
    fly_series = fly_series.dropna()
    if fly_series.empty:
        return {"error": "pca_neutral_butterfly_weights: empty fly series."}

    as_of_iso = fly_series.index[-1].strftime("%Y-%m-%d")
    ts_rows: List[TimeSeriesRow] = []
    bespoke: List[PcaNeutralButterflyWeightsTimeSeriesRow] = []
    for dt, v in fly_series.items():
        ds = dt.strftime("%Y-%m-%d")
        rv = _round(float(v), round_dec)
        ts_rows.append(TimeSeriesRow(date=ds, value=rv))
        bespoke.append(PcaNeutralButterflyWeightsTimeSeriesRow(
            date=ds, neutral_fly_bps=rv))

    time_series_neutral_fly = TimeSeries(
        series_name=(
            f"{params.curve_family}_{params.short_tenor}_{params.belly_tenor}_"
            f"{params.long_tenor}_pca_neutral_fly"
        ),
        units=TimeSeriesUnits.BPS,
        description=(
            f"PCA-neutral {params.short_tenor}/{params.belly_tenor}/"
            f"{params.long_tenor} butterfly for {params.curve_family} "
            f"(neutral to PC1{'+PC2' if n_pcs == 2 else ''}, full-sample), bps."
        ),
        rows=ts_rows,
    )

    current_metrics = PcaNeutralButterflyWeightsCurrentMetrics(
        as_of_date=as_of_iso,
        curve_family=params.curve_family,
        short_tenor=params.short_tenor,
        belly_tenor=params.belly_tenor,
        long_tenor=params.long_tenor,
        belly_weight=belly_weight,
        short_weight=_round(w_short, round_dec),
        long_weight=_round(w_long, round_dec),
        n_pcs_neutralized=n_pcs,
        residual_pc_exposures=residual,
        current_fly_bps=_round(float(fly_series.iloc[-1]), round_dec),
        fit_tenors=fit_tenors,
        n_complete_rows=int(len(changes)),
        near_degenerate=bool(fit.near_degenerate),
    )

    disclosures = [
        f"Wing weights solved to zero the fly's exposure to PC1"
        f"{'+PC2' if n_pcs == 2 else ' (with a cash-neutral wing normalization)'}; "
        f"belly weight fixed at {belly_weight}.",
        "PCA fit on yield CHANGES (correlation PCA via SVD); FULL-SAMPLE "
        "loadings (look-ahead) — a descriptive hedge ratio, NOT a forecast.",
    ]

    output = PcaNeutralButterflyWeightsOutput(
        current_metrics=current_metrics,
        time_series=bespoke,
        time_series_neutral_fly=time_series_neutral_fly,
        methodology_disclosures=disclosures,
    )
    return output.model_dump()


__all__ = ["CONFIG_PATH", "calculate_pca_neutral_butterfly_weights"]

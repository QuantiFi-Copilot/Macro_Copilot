"""compute.py — sovereign curve-regime fit (the first Bucket-2 primitive).

Assembles a four-feature panel for one sovereign curve
([level, slope (long − short), curvature, realized-vol]) and feeds the
RAW (natural-unit) features to the finance-blind ``fit_regime_hmm``
operator,
which z-scores them internally and fits a Gaussian HMM.  This primitive
carries THE FINANCE — which features, what the regimes mean (it names
them by their realized-vol mean) — and ZERO statistical math (no EM, no
Viterbi, no standardization: all of that is the operator / shared.quant).

Bucket-2 model-state pattern: the fitted model (transition matrix,
per-state means, loglik) rides in the operator's lineage; we read it back
out and surface "the fit, not just a snapshot" in ``current_metrics``.

DESCRIBE, NOT FORECAST: a full-sample HMM read of which regime each
HISTORICAL date sat in (``fit_scope='full_sample'``, disclosed).  Never a
prediction of the next regime nor a recommendation.

Test seam
---------
``fetch_instrument_panel`` is imported at module level so unit tests can
monkeypatch it via ``patch("rates_agent.sovereign_bonds.tools.
sovereign_curve_regime.compute.fetch_instrument_panel")``.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.sovereign_bonds.tools.sovereign_curve_regime.schemas import (
    RegimeFeatureMeans,
    SovereignCurveRegimeCurrentMetrics,
    SovereignCurveRegimeInput,
    SovereignCurveRegimeOutput,
    SovereignCurveRegimeTimeSeriesRow,
)
from shared.analytics.panel_assembly import fetch_instrument_panel
from shared.artifacts.lineage import Lineage, PrimitiveStep
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel
from shared.artifacts.units import TimeSeriesUnits as ArtifactUnits
from shared.config import ToolConfig, load_tool_config
from shared.config.operator_config import load_operator_config
from shared.operators.fit_regime_hmm import (
    CONFIG_PATH as HMM_CONFIG_PATH,
    FitRegimeHmmError,
    fit_regime_hmm,
)
from shared.operators.fit_regime_hmm.schemas import FitRegimeHmmParams
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.schemas import TimeSeriesUnits as SchemaUnits


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_TOOL_NAME = "calculate_sovereign_curve_regime_tool"
_TOOL_VERSION = "1.0.0"

# The natural-unit feature columns (the FINANCE — what the curve regime is
# read FROM).  The operator sorts columns internally; we group our own raw
# frame by these names to recover natural-unit per-regime means.
_LEVEL = "level"
_SLOPE = "slope"
_CURVATURE = "curvature"
_REALIZED_VOL = "realized_vol"
_FEATURE_UNITS = {
    _LEVEL: ArtifactUnits.PERCENT,
    _SLOPE: ArtifactUnits.BPS,
    _CURVATURE: ArtifactUnits.BPS,
    _REALIZED_VOL: ArtifactUnits.PERCENT,
}


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return round(float(value), decimals)


def calculate_sovereign_curve_regime(
    engine: Engine,
    params: SovereignCurveRegimeInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Fit a Gaussian HMM curve-regime model for one sovereign curve.

    Returns ``output.model_dump()`` on success, or ``{"error": "..."}``
    on a recoverable failure (no data, degenerate fit, too little
    history) — the standard primitive envelope; never raises.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    default_field = config.convention_value("default_field_name")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    vol_window = int(config.convention_value("realized_vol_window"))
    vol_return_method = config.convention_value("realized_vol_return_method")
    vol_annualization_days = int(
        config.convention_value("realized_vol_annualization_days")
    )
    naming_feature = config.convention_value("regime_naming_feature")
    naming_order = config.convention_value("regime_naming_order")
    label_low = config.convention_value("regime_label_low")
    label_mid = config.convention_value("regime_label_mid")
    label_high = config.convention_value("regime_label_high")
    round_dec = int(config.convention_value("metric_round_decimals"))

    field = params.field_name or default_field
    short_col = f"{params.curve_family}_{params.short_tenor}"
    belly_col = f"{params.curve_family}_{params.belly_tenor}"
    long_col = f"{params.curve_family}_{params.long_tenor}"

    # ------------------------------------------------------------------
    # 1. Fetch the three tenor yields for this curve.
    # ------------------------------------------------------------------
    start_date = date.today() - timedelta(days=int(params.lookback_days))
    leg_specs = [
        (params.curve_family, params.short_tenor, field),
        (params.curve_family, params.belly_tenor, field),
        (params.curve_family, params.long_tenor, field),
    ]
    raw = fetch_instrument_panel(
        engine=engine,
        leg_specs=leg_specs,
        start_date=start_date,
        end_date=None,
        ffill_limit_days=ffill_limit,
    )
    if raw.empty:
        return {
            "error": (
                f"No observations for {params.curve_family} "
                f"{[params.short_tenor, params.belly_tenor, params.long_tenor]} "
                f"(field {field}) since {start_date.isoformat()}.  Verify the "
                "curve_family + tenors exist in the database."
            )
        }
    missing = [c for c in (short_col, belly_col, long_col)
               if c not in raw.columns or raw[c].isna().all()]
    if missing:
        return {
            "error": (
                f"No data for tenor leg(s) {missing}.  Verify each "
                f"(curve_family, tenor, field) triple is valid for "
                f"{params.curve_family}."
            )
        }

    # ------------------------------------------------------------------
    # 2. Assemble the RAW features (pure finance-aware arithmetic — no
    #    statistics beyond the disclosed-convention realized-vol).  bps =
    #    yield-point × 100; realized-vol = annualized rolling std of daily
    #    level CHANGES.
    # ------------------------------------------------------------------
    level = raw[long_col].astype(float)
    slope = (raw[long_col] - raw[short_col]).astype(float) * 100.0
    curvature = (
        2.0 * raw[belly_col] - raw[short_col] - raw[long_col]
    ).astype(float) * 100.0
    if vol_return_method == "pct_change":
        returns = level.pct_change()
    else:  # "diff" — the disclosed default (level changes)
        returns = level.diff()
    realized_vol = (
        returns.rolling(window=vol_window).std() * math.sqrt(vol_annualization_days)
    )
    feature_frame = pd.DataFrame(
        {
            _LEVEL: level,
            _SLOPE: slope,
            _CURVATURE: curvature,
            _REALIZED_VOL: realized_vol,
        },
        index=raw.index,
    ).astype(float)

    # ------------------------------------------------------------------
    # 3. Wrap the RAW features as a Panel artifact (the operator z-scores
    #    them — we MUST NOT pre-standardize).  Internal lineage step.
    # ------------------------------------------------------------------
    units_by_column = dict(_FEATURE_UNITS)
    as_of_iso = feature_frame.index[-1].strftime("%Y-%m-%d")
    step_params: Dict[str, Any] = {
        "curve_family": params.curve_family,
        "tenors": {
            "short": params.short_tenor,
            "belly": params.belly_tenor,
            "long": params.long_tenor,
        },
        "field_name": field,
        "features": [_LEVEL, _SLOPE, _CURVATURE, _REALIZED_VOL],
        "realized_vol_window": vol_window,
        "realized_vol_return_method": vol_return_method,
        "realized_vol_annualization_days": vol_annualization_days,
        "ffill_limit_days": ffill_limit,
        "n_observations": int(len(feature_frame)),
    }
    feature_step = PrimitiveStep.build(
        name=_TOOL_NAME,
        version=_TOOL_VERSION,
        params=step_params,
        tool_config_hash=config.conventions_hash(),
        output_field="features",
        as_of_date=as_of_iso,
        tool_config_path=str(CONFIG_PATH),
    )
    features_panel = Panel(
        payload=feature_frame,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([feature_step]),
    )

    # ------------------------------------------------------------------
    # 4. Fit the regimes via the operator (explicit config — strict PR9).
    #    ALL statistical math lives here, never in this primitive.
    # ------------------------------------------------------------------
    hmm_config = load_operator_config(HMM_CONFIG_PATH)
    try:
        labels_series = fit_regime_hmm(
            features_panel,
            params=FitRegimeHmmParams(n_states=int(params.n_states)),
            config=hmm_config,
        )
    except FitRegimeHmmError as exc:
        return {"error": f"sovereign_curve_regime: regime fit failed — {exc}"}

    labels = labels_series.payload  # float labels, NaN on warmup/edge rows
    op_step = labels_series.lineage.steps[-1]
    model = op_step.params
    n_states = int(model["n_states"])
    transition_matrix = model["transition_matrix"]

    # ------------------------------------------------------------------
    # 5. The FINANCE: natural-unit per-regime means + regime NAMING (by
    #    the realized-vol mean) — read the fitted model state back out of
    #    the operator's lineage (the Bucket-2 "fit, not snapshot" pattern).
    # ------------------------------------------------------------------
    labels_int = labels.dropna().astype(int)
    if labels_int.empty:
        return {
            "error": (
                "sovereign_curve_regime: the regime fit produced no "
                "decoded labels (all rows fell in the feature warmup/edge)."
            )
        }
    regime_means: Dict[int, Dict[str, float]] = {}
    regime_counts: Dict[int, int] = {}
    for k in range(n_states):
        idx_k = labels_int.index[labels_int == k]
        regime_counts[k] = int(len(idx_k))
        sub = feature_frame.loc[idx_k]
        regime_means[k] = {
            col: float(sub[col].mean()) if len(idx_k) else float("nan")
            for col in feature_frame.columns
        }

    # Order regimes by their naming-feature mean, then assign descriptive
    # names (lowest realized-vol → low, highest → high).  Descending
    # config flips the order.  NaN means (empty regime) sort last.
    def _naming_key(k: int) -> float:
        m = regime_means[k][naming_feature]
        return m if math.isfinite(m) else math.inf
    ranked = sorted(range(n_states), key=_naming_key)
    if naming_order == "descending":
        ranked = list(reversed(ranked))
    name_map: Dict[int, str] = {}
    for rank, k in enumerate(ranked):
        if rank == 0:
            name_map[k] = label_low
        elif rank == len(ranked) - 1:
            name_map[k] = label_high
        else:
            name_map[k] = f"{label_mid}_{rank}"

    # Current regime = the last decoded label.
    current_idx = int(labels_int.iloc[-1])
    current_name = name_map[current_idx]
    persistence = _round(
        float(transition_matrix[current_idx][current_idx]), round_dec
    )
    as_of_date = labels_int.index[-1].strftime("%Y-%m-%d")

    per_regime: List[RegimeFeatureMeans] = [
        RegimeFeatureMeans(
            regime_index=k,
            regime_name=name_map[k],
            n_observations=regime_counts[k],
            level_pct=_round(regime_means[k][_LEVEL], round_dec),
            slope_bps=_round(regime_means[k][_SLOPE], round_dec),
            curvature_bps=_round(regime_means[k][_CURVATURE], round_dec),
            realized_vol_pct=_round(regime_means[k][_REALIZED_VOL], round_dec),
        )
        for k in range(n_states)
    ]

    # ------------------------------------------------------------------
    # 6. Build the regime time series (composable, FACTOR_LEVEL) + the
    #    bespoke per-row labels.
    # ------------------------------------------------------------------
    ts_rows: List[TimeSeriesRow] = []
    bespoke_rows: List[SovereignCurveRegimeTimeSeriesRow] = []
    for dt, val in labels.items():
        date_str = dt.strftime("%Y-%m-%d")
        if pd.isna(val):
            ts_rows.append(TimeSeriesRow(date=date_str, value=None))
            bespoke_rows.append(
                SovereignCurveRegimeTimeSeriesRow(
                    date=date_str, regime_index=None, regime_name=None,
                )
            )
        else:
            k = int(val)
            ts_rows.append(TimeSeriesRow(date=date_str, value=float(k)))
            bespoke_rows.append(
                SovereignCurveRegimeTimeSeriesRow(
                    date=date_str, regime_index=k, regime_name=name_map[k],
                )
            )

    time_series_regime = TimeSeries(
        series_name=f"{params.curve_family}_curve_regime_k{n_states}",
        units=SchemaUnits.FACTOR_LEVEL,
        description=(
            f"Gaussian-HMM curve-regime labels for {params.curve_family} "
            f"(K={n_states}; features level/slope/curvature/realized-vol; "
            "full-sample fit)."
        ),
        rows=ts_rows,
    )

    current_metrics = SovereignCurveRegimeCurrentMetrics(
        as_of_date=as_of_date,
        curve_family=params.curve_family,
        current_regime_index=current_idx,
        current_regime_name=current_name,
        regime_persistence=persistence,
        n_states=n_states,
        loglik=_round(float(model["loglik"]), round_dec),
        n_iter=int(model["n_iter"]),
        converged=bool(model["converged"]),
        fit_scope=str(model["fit_scope"]),
        feature_columns=list(model["feature_columns"]),
        n_core_rows=int(model["n_core_rows"]),
        per_regime=per_regime,
        methodology_label=config.methodology.what_it_does.strip(),
    )

    disclosures = [
        f"fit_scope={model['fit_scope']} — describes which regime each "
        "historical date sat in; NOT a forecast of the next regime.",
        f"Regimes NAMED by ascending {naming_feature} mean "
        "(low/intermediate/high volatility) — a descriptive label, not a "
        "trade signal.",
        f"Realized-vol = annualized ({vol_annualization_days}d) rolling "
        f"{vol_window}d std of yield {vol_return_method}; the HMM z-scores "
        "every feature internally.",
    ]

    output = SovereignCurveRegimeOutput(
        current_metrics=current_metrics,
        time_series=bespoke_rows,
        time_series_regime=time_series_regime,
        methodology_disclosures=disclosures,
    )
    return output.model_dump()


__all__ = [
    "CONFIG_PATH",
    "calculate_sovereign_curve_regime",
]

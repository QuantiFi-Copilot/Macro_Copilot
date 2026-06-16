"""compute.py — priced policy-path regime (Bucket-2 model-state).

Fits a Gaussian HMM to the PRICED policy path on a policy-futures STIR
strip and labels which regime the market is currently PRICING (easing /
neutral / tightening priced).  Features: the strip front level, slope
(back − front), curvature, and front-rate realized vol — assembled from
the strip and fed RAW to the finance-blind fit_regime_hmm operator.

THE FINANCE this primitive owns: the strip choice + the metadata-driven
implied-rate conversion + naming the priced regime.  ZERO statistical math
— the HMM is the operator (shared/quant/hmm.py).

DESCRIBE WHAT IS PRICED, do NOT forecast: the strip already encodes the
market's expected policy path; this reads its CURRENT shape and classifies
it (fit_scope='full_sample', disclosed).

Test seam: ``fetch_policy_futures_strip_panel`` is imported at module level
for monkeypatching.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy.engine import Engine

from rates_agent.ois.tools.ois_policy_path_regime.schemas import (
    OISPolicyPathRegimeCurrentMetrics,
    OISPolicyPathRegimeInput,
    OISPolicyPathRegimeOutput,
    OISPolicyPathRegimeTimeSeriesRow,
    PolicyRegimeFeatureMeans,
)
from shared.analytics.panel_assembly import (
    _strip_panel_column_key,
    fetch_policy_futures_strip_panel,
)
from shared.analytics.rates_fetch import latest_trade_date
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

_TOOL_NAME = "calculate_ois_policy_path_regime_tool"
_TOOL_VERSION = "1.0.0"
_BPS_PER_PCT = 100.0

_FRONT_LEVEL = "front_level"
_STRIP_SLOPE = "strip_slope"
_STRIP_CURVATURE = "strip_curvature"
_REALIZED_VOL = "realized_vol"
_FEATURE_UNITS = {
    _FRONT_LEVEL: ArtifactUnits.PERCENT,
    _STRIP_SLOPE: ArtifactUnits.BPS,
    _STRIP_CURVATURE: ArtifactUnits.BPS,
    _REALIZED_VOL: ArtifactUnits.PERCENT,
}


def _round(value: Optional[float], decimals: int) -> Optional[float]:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    return round(float(value), decimals)


def calculate_ois_policy_path_regime(
    engine: Engine,
    params: OISPolicyPathRegimeInput,
    config: Optional[ToolConfig] = None,
) -> Dict[str, Any]:
    """Fit the priced policy-regime HMM for one policy-futures strip.

    Returns ``output.model_dump()`` on success or ``{"error": "..."}`` on a
    recoverable failure — never raises.
    """
    if config is None:
        config = load_tool_config(CONFIG_PATH)

    field = config.convention_value("strip_price_field")
    ffill_limit = int(config.convention_value("ffill_limit_days"))
    vol_window = int(config.convention_value("realized_vol_window"))
    vol_return_method = config.convention_value("realized_vol_return_method")
    vol_ann_days = int(config.convention_value("realized_vol_annualization_days"))
    naming_feature = config.convention_value("policy_naming_feature")
    naming_order = config.convention_value("policy_naming_order")
    label_easing = config.convention_value("policy_label_easing")
    label_neutral = config.convention_value("policy_label_neutral")
    label_tightening = config.convention_value("policy_label_tightening")
    round_dec = int(config.convention_value("policy_regime_round_decimals"))
    front_pos = int(config.convention_value("strip_front_position"))
    belly_pos = int(config.convention_value("strip_belly_position"))
    back_pos = int(config.convention_value("strip_back_position"))

    cf = params.curve_family
    positions = sorted({front_pos, belly_pos, back_pos})
    keys = {p: _strip_panel_column_key(cf, p) for p in positions}
    if len({front_pos, belly_pos, back_pos}) != 3:
        return {
            "error": (
                "ois_policy_path_regime: the front / belly / back strip "
                f"positions must be distinct (got {front_pos}/{belly_pos}/"
                f"{back_pos}); check the config conventions."
            )
        }

    # ------------------------------------------------------------------
    # 1. Fetch the strip (front / belly / back) + the universe metadata.
    # ------------------------------------------------------------------
    # Anchor to the latest available trade_date (not date.today()) so the
    # window resolves to real data when ingestion lags (weekend / holiday /
    # stale snapshot).  Probe the same policy-futures filter the strip fetch
    # uses (curve_family + price field + instrument_type='policy_future');
    # falls back to today only when the strip has no rows.
    anchor = (
        latest_trade_date(
            engine,
            curve_family=cf,
            field_name=field,
            instrument_type="policy_future",
        )
        or date.today()
    )
    start_date = anchor - timedelta(days=int(params.lookback_days))
    raw_panel, universe_meta = fetch_policy_futures_strip_panel(
        engine=engine,
        curve_families=[cf],
        strip_positions=positions,
        field_name=field,
        start_date=start_date,
        end_date=None,
        ffill_limit_days=ffill_limit,
    )
    if raw_panel.empty:
        return {
            "error": (
                f"No policy-futures observations for {cf} strip positions "
                f"{positions} (field {field}) since {start_date.isoformat()}."
            )
        }
    missing = [keys[p] for p in positions
               if keys[p] not in raw_panel.columns or raw_panel[keys[p]].isna().all()]
    if missing:
        return {
            "error": (
                f"No data for strip cell(s) {missing}.  Verify {cf} has "
                f"positions {positions} ingested."
            )
        }

    # ------------------------------------------------------------------
    # 2. Convert each inverse-priced cell to an implied rate (100 − price),
    #    per the instrument_master inverse_pricing flag (metadata-driven;
    #    refuse a missing / mixed-flag family).
    # ------------------------------------------------------------------
    flags = universe_meta[universe_meta["curve_family"] == cf][
        "inverse_pricing"
    ].dropna().tolist()
    distinct = {bool(f) for f in flags}
    if not flags:
        return {
            "error": (
                f"Policy-futures curve_family={cf!r} is missing the "
                "'inverse_pricing' flag in instrument_master — the "
                "implied-rate conversion is metadata-driven and cannot be "
                "applied honestly.  Surface this metadata gap."
            )
        }
    if len(distinct) > 1:
        return {
            "error": (
                f"Policy-futures curve_family={cf!r} cells disagree on the "
                "'inverse_pricing' flag — refusing a mixed-convention strip."
            )
        }
    inverse = next(iter(distinct))
    implied = raw_panel.copy()
    if inverse:
        for col in implied.columns:
            implied[col] = 100.0 - implied[col]

    # ------------------------------------------------------------------
    # 3. Assemble the RAW priced-path features (the FINANCE).
    # ------------------------------------------------------------------
    front = implied[keys[front_pos]].astype(float)
    back = implied[keys[back_pos]].astype(float)
    belly = implied[keys[belly_pos]].astype(float)
    slope = (back - front) * _BPS_PER_PCT          # +ve = tightening priced
    curvature = (2.0 * belly - front - back) * _BPS_PER_PCT
    if vol_return_method == "pct_change":
        returns = front.pct_change()
    else:
        returns = front.diff()
    realized_vol = (
        returns.rolling(window=vol_window).std() * math.sqrt(vol_ann_days)
    )
    feature_frame = pd.DataFrame(
        {
            _FRONT_LEVEL: front,
            _STRIP_SLOPE: slope,
            _STRIP_CURVATURE: curvature,
            _REALIZED_VOL: realized_vol,
        },
        index=implied.index,
    ).astype(float)

    # ------------------------------------------------------------------
    # 4. Wrap RAW features as a Panel (operator z-scores internally).
    # ------------------------------------------------------------------
    units_by_column = dict(_FEATURE_UNITS)
    as_of_iso = feature_frame.index[-1].strftime("%Y-%m-%d")
    step_params: Dict[str, Any] = {
        "curve_family": cf,
        "strip_positions": positions,
        "strip_front_position": front_pos,
        "strip_belly_position": belly_pos,
        "strip_back_position": back_pos,
        "field_name": field,
        "inverse_priced": bool(inverse),
        "features": [_FRONT_LEVEL, _STRIP_SLOPE, _STRIP_CURVATURE, _REALIZED_VOL],
        "realized_vol_window": vol_window,
        "realized_vol_return_method": vol_return_method,
        "realized_vol_annualization_days": vol_ann_days,
        "ffill_limit_days": ffill_limit,
        "n_observations": int(len(feature_frame)),
    }
    features_panel = Panel(
        payload=feature_frame,
        units_by_column=units_by_column,
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([
            PrimitiveStep.build(
                name=_TOOL_NAME, version=_TOOL_VERSION, params=step_params,
                tool_config_hash=config.conventions_hash(),
                output_field="features", as_of_date=as_of_iso,
                tool_config_path=str(CONFIG_PATH),
            )
        ]),
    )

    # ------------------------------------------------------------------
    # 5. Fit the regimes (explicit operator config — strict PR9).
    # ------------------------------------------------------------------
    hmm_config = load_operator_config(HMM_CONFIG_PATH)
    try:
        labels_series = fit_regime_hmm(
            features_panel,
            params=FitRegimeHmmParams(n_states=int(params.n_states)),
            config=hmm_config,
        )
    except FitRegimeHmmError as exc:
        return {"error": f"ois_policy_path_regime: regime fit failed — {exc}"}

    labels = labels_series.payload
    # m21: loglik + n_iter now ride the operator step's non-hashed
    # diagnostics channel (solver telemetry, excluded from the head_hash
    # for cross-version stability).  Merge it onto the content params so
    # the model-state read below sees both — display-only.
    _op_step = labels_series.lineage.steps[-1]
    model = {**_op_step.params, **_op_step.diagnostics}
    n_states = int(model["n_states"])
    transition_matrix = model["transition_matrix"]

    # ------------------------------------------------------------------
    # 6. The FINANCE: natural-unit per-regime means + naming by slope.
    # ------------------------------------------------------------------
    labels_int = labels.dropna().astype(int)
    if labels_int.empty:
        return {
            "error": (
                "ois_policy_path_regime: the fit produced no decoded labels."
            )
        }
    regime_means: Dict[int, Dict[str, float]] = {}
    regime_counts: Dict[int, int] = {}
    for k in range(n_states):
        idx_k = labels_int.index[labels_int == k]
        regime_counts[k] = int(len(idx_k))
        sub = feature_frame.loc[idx_k]
        regime_means[k] = {
            c: float(sub[c].mean()) if len(idx_k) else float("nan")
            for c in feature_frame.columns
        }

    def _naming_key(k: int) -> float:
        m = regime_means[k][naming_feature]
        return m if math.isfinite(m) else math.inf
    ranked = sorted(range(n_states), key=_naming_key)
    if naming_order == "descending":
        ranked = list(reversed(ranked))
    name_map: Dict[int, str] = {}
    n_mid = len(ranked) - 2
    for rank, k in enumerate(ranked):
        if rank == 0:
            name_map[k] = label_easing
        elif rank == len(ranked) - 1:
            name_map[k] = label_tightening
        elif n_mid == 1:
            name_map[k] = label_neutral
        else:
            name_map[k] = f"{label_neutral}_{rank}"

    current_idx = int(labels_int.iloc[-1])
    current_name = name_map[current_idx]
    persistence = _round(
        float(transition_matrix[current_idx][current_idx]), round_dec
    )
    as_of_date = labels_int.index[-1].strftime("%Y-%m-%d")

    per_regime: List[PolicyRegimeFeatureMeans] = [
        PolicyRegimeFeatureMeans(
            regime_index=k,
            regime_name=name_map[k],
            n_observations=regime_counts[k],
            front_level_pct=_round(regime_means[k][_FRONT_LEVEL], round_dec),
            strip_slope_bps=_round(regime_means[k][_STRIP_SLOPE], round_dec),
            strip_curvature_bps=_round(regime_means[k][_STRIP_CURVATURE], round_dec),
            realized_vol_pct=_round(regime_means[k][_REALIZED_VOL], round_dec),
        )
        for k in range(n_states)
    ]

    # ------------------------------------------------------------------
    # 7. Regime time series (composable, FACTOR_LEVEL) + bespoke rows.
    # ------------------------------------------------------------------
    ts_rows: List[TimeSeriesRow] = []
    bespoke: List[OISPolicyPathRegimeTimeSeriesRow] = []
    for dt, val in labels.items():
        ds = dt.strftime("%Y-%m-%d")
        if pd.isna(val):
            ts_rows.append(TimeSeriesRow(date=ds, value=None))
            bespoke.append(OISPolicyPathRegimeTimeSeriesRow(
                date=ds, regime_index=None, regime_name=None,
            ))
        else:
            k = int(val)
            ts_rows.append(TimeSeriesRow(date=ds, value=float(k)))
            bespoke.append(OISPolicyPathRegimeTimeSeriesRow(
                date=ds, regime_index=k, regime_name=name_map[k],
            ))

    time_series_regime = TimeSeries(
        series_name=f"{cf}_policy_path_regime_k{n_states}",
        units=SchemaUnits.FACTOR_LEVEL,
        description=(
            f"Gaussian-HMM priced-policy-regime labels for the {cf} strip "
            f"(K={n_states}; features front/slope/curvature/realized-vol; "
            "full-sample fit)."
        ),
        rows=ts_rows,
    )

    current_metrics = OISPolicyPathRegimeCurrentMetrics(
        as_of_date=as_of_date,
        curve_family=cf,
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
        f"fit_scope={model['fit_scope']} — describes which regime history "
        "PRICED; the strip already encodes the market's expectation, so this "
        "is NOT our forecast of the policy path.",
        f"Regimes named by ascending {naming_feature} "
        "(easing / neutral / tightening priced) — descriptive, not a signal.",
        f"Implied rate = 100 − price (inverse_priced={bool(inverse)}, "
        "metadata-driven); single curve_family per fit (RFR vs IBOR differ).",
    ]

    output = OISPolicyPathRegimeOutput(
        current_metrics=current_metrics,
        time_series=bespoke,
        time_series_regime=time_series_regime,
        methodology_disclosures=disclosures,
    )
    return output.model_dump()


__all__ = ["CONFIG_PATH", "calculate_ois_policy_path_regime"]

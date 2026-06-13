"""fit_regime_gmm — Gaussian-mixture regime labelling of a Panel.

Finance-blind ``cross_sectional`` operator and the marquee A4 model-fit
engine.  Fits a K-component Gaussian mixture to the per-date feature
vectors of a Panel (the EM math lives in ``shared/quant/gmm.py``, the
sixth module there — no library) and emits the per-date most-likely
REGIME LABEL as a Series.  This is the FIRST Panel-consuming operator.

The state SEQUENCE is the composable output; the fitted model object
(means/covariances/weights) does NOT fit the closed artifact family, so
it rides in LINEAGE only (NOT a second output, NOT a new artifact type
— the plan §7-A4 ruling; the finance of naming the regimes is the B2
``sovereign_curve_regime`` primitive's job).

ONE OUTPUT (OPR2): the regime-label Series only.  The state-
PROBABILITIES Panel is a SEPARATE operator (``fit_regime_gmm_probs``, a
declared planned extension).

THE LABEL IS CATEGORICAL (P5 — load-bearing): the output is a regime
INDEX (0..K-1) in FACTOR_LEVEL units; it is NON-ARITHMETIC (you cannot
average regime 0 and regime 2).  Disclosed as
``label_semantics='categorical_nonarithmetic'`` in lineage — feed it to
``transition_events`` / ``apply_mask`` / ``summarize_series(mode)``,
NEVER an arithmetic operator.

DETERMINISM (OPR14 — the reproducibility the platform sells): the EM
runs on the COLUMN-STANDARDISED features from a FIXED-seed k-means++
init, and the components are RELABELLED into a canonical order
(ascending by the standardised centroid grand-mean) so "state 0" is the
same regime across reruns.

LOOK-AHEAD (P5): the mixture params are fit over the WHOLE Panel, so
each per-date label uses full-sample information — disclosed as
``fit_scope='full_sample'``; never feed into point-in-time compositions.

NaN POLICY (ORDER-INSENSITIVE — the per-date clustering is not a
sequential recursion): a row with a NaN in ANY feature is DROPPED
(complete-case), the mixture is fit on the remaining rows, and the
output Series is on the FULL Panel index with a NaN label on every
dropped date (the honest "no regime" sentinel).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind clustering model; zero finance
    vocabulary (no regime NAMES, no tenors).
  - OPR2      : ONE output artifact type — always a ``Series`` (the
    label sequence; the fitted model rides in lineage).
  - OPR8      : n_changepoints... n_states required (no honest
    default); the estimator/init/ordering locks documented + stamped.
  - OPR9      : one ``Panel`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the means/weights/loglik + the
    locks + the scope + the drop count ride in params.
  - OPR11     : FACTOR_LEVEL units (a latent-model categorical label);
    the non-arithmetic semantics disclosed in lineage.
  - OPR13     : typed ``FitRegimeGmmError`` refusals: non-Panel input;
    missing params (n_states); fewer than 2 feature columns; fewer
    than max(12, n_states*10) complete-case rows; a zero-variance
    feature column; EM NON-CONVERGENCE / a collapsed component.
  - OPR14     : pure + rerun-deterministic (fixed-seed init + canonical
    relabel).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import Panel, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.fit_regime_gmm.schemas import FitRegimeGmmParams
from shared.quant.gmm import fit_gmm_em


_OPERATOR_NAME = "fit_regime_gmm"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_FAMILY_FLOOR = 12
# Locked: a regime needs a reasonable sample to estimate its Gaussian.
_MIN_ROWS_PER_STATE = 10
_MIN_FEATURES = 2


class FitRegimeGmmError(ValueError):
    """Raised by ``fit_regime_gmm`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def fit_regime_gmm(
    features: Panel,
    *,
    params: Optional[FitRegimeGmmParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Per-date Gaussian-mixture regime labels of a feature ``Panel``.

    Parameters
    ----------
    features :
        The input ``Panel`` (dates x feature-columns; >= 2 features).
        Rows with any NaN feature are dropped (complete-case); the
        output carries a NaN label on those dates.
    params :
        ``FitRegimeGmmParams`` — REQUIRED (n_states has no honest
        default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The per-date regime label (0..K-1) on the FULL Panel index, in
        FACTOR_LEVEL units (a NON-arithmetic categorical index); NaN on
        dropped dates.  The fitted means/weights/loglik and the locks
        ride in lineage.

    Raises
    ------
    FitRegimeGmmError
        Missing params (n_states required); a non-Panel input; fewer
        than 2 feature columns; fewer than max(12, n_states*10)
        complete-case rows; a zero-variance feature column; or EM
        non-convergence / a collapsed component.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. n_states is methodology — params=None refuses naming it
    #    (the changepoint/hp_filter precedent; OPR8 honest refusal).
    # ------------------------------------------------------------------
    if params is None:
        raise FitRegimeGmmError(
            "fit_regime_gmm requires explicit params with n_states "
            "(the number of regimes K) — it is the central methodology "
            "choice with no honest default."
        )
    n_states = int(params.n_states)

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(features, Panel):
        raise FitRegimeGmmError(
            "fit_regime_gmm: input must be a Panel artifact; got "
            f"{type(features).__name__}."
        )

    frame = features.payload
    columns = [str(c) for c in frame.columns]
    n_features = len(columns)
    if n_features < _MIN_FEATURES:
        raise FitRegimeGmmError(
            f"fit_regime_gmm: needs at least {_MIN_FEATURES} feature "
            f"columns (got {n_features}) — a regime on a single feature "
            "is a threshold; use threshold_events instead."
        )

    # ORDER-INSENSITIVE NaN policy: drop rows with any NaN feature
    # (complete-case); the output carries a NaN label on those dates.
    full_index = frame.index
    n_total = int(len(full_index))
    complete_mask = frame.notna().all(axis=1).to_numpy()
    n_complete = int(complete_mask.sum())
    n_dropped = n_total - n_complete

    floor = max(_FAMILY_FLOOR, n_states * _MIN_ROWS_PER_STATE)
    if n_complete < floor:
        raise FitRegimeGmmError(
            f"fit_regime_gmm: needs at least max(12, n_states*"
            f"{_MIN_ROWS_PER_STATE}) = {floor} complete-case rows for "
            f"{n_states} regimes (got {n_complete} after dropping "
            f"{n_dropped} NaN row(s)).  Lower n_states or widen the "
            "lookback."
        )

    x = frame.to_numpy(dtype=float)[complete_mask]

    # ------------------------------------------------------------------
    # 4. The fit (shared/quant; design-locked EM + canonical relabel).
    #    A zero-variance feature column refuses inside the quant.
    # ------------------------------------------------------------------
    try:
        result = fit_gmm_em(x, n_states)
    except ValueError as exc:
        raise FitRegimeGmmError(f"fit_regime_gmm: {exc}") from exc
    if not result.converged:
        raise FitRegimeGmmError(
            "fit_regime_gmm: the EM fit did not converge or a "
            "component collapsed — the regime labelling would be "
            "unreliable.  (Try fewer regimes or a longer / "
            "better-separated feature history.)"
        )

    # ------------------------------------------------------------------
    # 5. Build the output Series — labels on the FULL Panel index;
    #    dropped (NaN-feature) dates get a NaN label.
    # ------------------------------------------------------------------
    out_values = np.full(n_total, np.nan, dtype=float)
    out_values[complete_mask] = result.labels.astype(float)
    out_key = f"regime_gmm__k{n_states}__{n_features}feat"
    out_payload = pd.Series(out_values, index=full_index, dtype=float)
    out_payload.name = out_key

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep (OPR10); the fitted model + locks.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "n_states": n_states,
        "means": [[float(v) for v in row] for row in result.means],
        "weights": [float(w) for w in result.weights],
        "loglik": float(result.loglik),
        "n_iter": int(result.n_iter),
        "converged": True,
        "covariance_type": "full",                # design-locked (OPR7)
        "algorithm": "em",                         # design-locked (OPR7)
        "init_method": "kmeans2_pp_seed0",         # design-locked (OPR7)
        "feature_standardization": "zscore_per_column",
        "canonical_ordering": "ascending_standardized_centroid_grand_mean",
        "label_semantics": "categorical_nonarithmetic",  # P5 disclosure
        "fit_scope": "full_sample",                # LOOK-AHEAD (P5)
        "n_features": n_features,
        "feature_columns": sorted(columns),
        "n_obs": n_total,
        "n_complete_rows": n_complete,
        "n_dropped_rows": n_dropped,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(features.lineage.head_hash,),
    )

    return Series(
        series_key=out_key,
        payload=out_payload,
        units=TimeSeriesUnits.FACTOR_LEVEL,
        frequency=None,  # a Panel carries no frequency tag
        missingness_policy=RawNoCleaning(),
        lineage=features.lineage.append(step),
    )


__all__ = ["fit_regime_gmm", "FitRegimeGmmError"]

"""fit_regime_hmm — Gaussian Hidden-Markov regime labelling of a Panel.

Finance-blind ``cross_sectional`` operator and the marquee A4 model-fit
engine — the TEMPORAL sibling of ``fit_regime_gmm``.  Fits a K-state
Gaussian HMM to the per-date feature vectors of a Panel (the Baum-Welch
math lives in ``shared/quant/hmm.py``, the eighth module there — no
library) and emits the VITERBI-decoded per-date REGIME LABEL as a
Series.

Unlike ``fit_regime_gmm`` (which clusters each date INDEPENDENTLY, so
the labels can flip date-to-date), the HMM adds a TRANSITION MATRIX so
regimes PERSIST — the decoded sequence is temporally smoothed (the
Viterbi path is consistent with the fitted transitions).  The fitted
model — the transition matrix, the initial distribution, the means —
does NOT fit the closed artifact family, so it rides in LINEAGE only
(NOT a second output, NOT a new artifact type — the plan §7-A4 ruling).

ONE OUTPUT (OPR2): the regime-label Series only.  The state-
PROBABILITIES Panel is a SEPARATE operator (``fit_regime_hmm_probs``, a
declared planned extension).

THE LABEL IS CATEGORICAL (P5): the output is a regime INDEX (0..K-1) in
FACTOR_LEVEL units; it is NON-ARITHMETIC (``label_semantics=
'categorical_nonarithmetic'`` in lineage — feed ``transition_events`` /
``apply_mask``, NEVER an arithmetic operator).

DETERMINISM (OPR14): the EM runs on the COLUMN-STANDARDISED features
from a FIXED-seed k-means++ init with a sticky-diagonal transition
init, and the K states are RELABELLED into a canonical order (ascending
standardised-centroid grand-mean) — permuting the means, the initial
distribution AND both axes of the transition matrix consistently — so
"state 0" is the same regime across reruns.

LOOK-AHEAD (P5): the params AND the Viterbi path use the WHOLE sequence
— disclosed as ``fit_scope='full_sample'``; never feed into
point-in-time compositions.

NaN POLICY (TWO-TIER — the SHARP divergence from the gmm twin): the HMM
is ORDER-SENSITIVE (the forward-backward and the transition counts run
over CONTIGUOUS adjacent rows), so a dropped INTERIOR row would splice
non-adjacent dates and corrupt the transition matrix.  Contiguous
LEADING/TRAILING NaN rows (a row is NaN iff ANY feature is) are
TOLERATED — the fit runs on the interior finite block, the edges pass
through as NaN labels.  An INTERIOR NaN row is REFUSED, naming the
remedy (``align_series`` ffill).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind temporal clustering model; zero
    finance vocabulary (no regime NAMES, no tenors).
  - OPR2      : ONE output artifact type — always a ``Series`` (the
    label sequence; the transition matrix rides in lineage).
  - OPR8      : n_states required (no honest default); the
    estimator/init/decode/ordering locks documented + stamped.
  - OPR9      : one ``Panel`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep``; the transition matrix + initial
    dist + means + loglik + the locks ride in params.
  - OPR11     : FACTOR_LEVEL units; the non-arithmetic semantics
    disclosed in lineage.
  - OPR13     : typed ``FitRegimeHmmError`` refusals: non-Panel input;
    missing params; fewer than 2 feature columns; fewer than
    max(12, n_states*10) interior rows; a zero-variance feature
    column; EM non-convergence / a collapsed state; an INTERIOR NaN.
  - OPR14     : pure + rerun-deterministic.
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
from shared.operators.fit_regime_hmm.schemas import FitRegimeHmmParams
from shared.quant.hmm import fit_hmm_em


_OPERATOR_NAME = "fit_regime_hmm"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

_FAMILY_FLOOR = 12
_MIN_ROWS_PER_STATE = 10
_MIN_FEATURES = 2


class FitRegimeHmmError(ValueError):
    """Raised by ``fit_regime_hmm`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def fit_regime_hmm(
    features: Panel,
    *,
    params: Optional[FitRegimeHmmParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Per-date Gaussian-HMM (Viterbi) regime labels of a ``Panel``.

    Parameters
    ----------
    features :
        The input ``Panel`` (dates x feature-columns; >= 2 features).
        Contiguous leading/trailing NaN rows are tolerated (passed
        through as NaN labels); an INTERIOR NaN row is refused.
    params :
        ``FitRegimeHmmParams`` — REQUIRED (n_states has no honest
        default).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The Viterbi-decoded per-date regime label (0..K-1) on the FULL
        Panel index, in FACTOR_LEVEL units (a NON-arithmetic categorical
        index); NaN on the contiguous edge rows.  The transition matrix,
        initial distribution, means and the locks ride in lineage.

    Raises
    ------
    FitRegimeHmmError
        Missing params (n_states required); a non-Panel input; fewer
        than 2 feature columns; fewer than max(12, n_states*10)
        interior rows; a zero-variance feature column; EM
        non-convergence / a collapsed state; or an INTERIOR NaN.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. n_states is methodology — params=None refuses naming it
    #    (the fit_regime_gmm precedent; OPR8 honest refusal).
    # ------------------------------------------------------------------
    if params is None:
        raise FitRegimeHmmError(
            "fit_regime_hmm requires explicit params with n_states (the "
            "number of regimes K) — it is the central methodology "
            "choice with no honest default."
        )
    n_states = int(params.n_states)

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(features, Panel):
        raise FitRegimeHmmError(
            "fit_regime_hmm: input must be a Panel artifact; got "
            f"{type(features).__name__}."
        )

    frame = features.payload
    columns = sorted(str(c) for c in frame.columns)
    n_features = len(columns)
    if n_features < _MIN_FEATURES:
        raise FitRegimeHmmError(
            f"fit_regime_hmm: needs at least {_MIN_FEATURES} feature "
            f"columns (got {n_features}) — a regime on a single feature "
            "is a threshold; use threshold_events instead."
        )

    full_index = frame.index
    n_total = int(len(full_index))
    sorted_frame = frame[columns]

    # TWO-TIER NaN: the HMM is order-sensitive (the transition counts
    # run over CONTIGUOUS rows).  A row is finite iff ALL features are.
    # Contiguous leading/trailing NaN tolerated; INTERIOR NaN refused.
    row_finite = sorted_frame.notna().all(axis=1).to_numpy()
    n_finite = int(row_finite.sum())
    if n_finite == 0:
        raise FitRegimeHmmError(
            "fit_regime_hmm: the input has no fully-finite rows."
        )
    first = int(np.argmax(row_finite))
    last = n_total - int(np.argmax(row_finite[::-1]))  # exclusive
    core_finite = row_finite[first:last]
    n_interior_nan = int((~core_finite).sum())
    if n_interior_nan > 0:
        raise FitRegimeHmmError(
            f"fit_regime_hmm: the input has {n_interior_nan} INTERIOR "
            "NaN row(s).  The HMM transition counts run over ADJACENT "
            "rows; dropping an interior gap would splice non-adjacent "
            "dates and corrupt the transition matrix.  Fill the gaps "
            "upstream (align_series ffill) and re-run.  (Contiguous "
            "leading/trailing NaN rows are tolerated.)"
        )

    n_core = last - first
    floor = max(_FAMILY_FLOOR, n_states * _MIN_ROWS_PER_STATE)
    if n_core < floor:
        raise FitRegimeHmmError(
            f"fit_regime_hmm: needs at least max(12, n_states*"
            f"{_MIN_ROWS_PER_STATE}) = {floor} contiguous finite rows "
            f"for {n_states} regimes (got {n_core}).  Lower n_states or "
            "widen the lookback."
        )

    x = sorted_frame.to_numpy(dtype=float)[first:last]
    if float(np.var(x[:, 0])) == 0.0 or np.any(x.std(axis=0) == 0.0):
        raise FitRegimeHmmError(
            "fit_regime_hmm: a feature column has zero variance over "
            "the finite block — the emission covariance is degenerate."
        )

    # ------------------------------------------------------------------
    # 4. The fit (shared/quant; design-locked Baum-Welch + Viterbi).
    # ------------------------------------------------------------------
    try:
        result = fit_hmm_em(x, n_states)
    except ValueError as exc:
        raise FitRegimeHmmError(f"fit_regime_hmm: {exc}") from exc
    if not result.converged:
        raise FitRegimeHmmError(
            "fit_regime_hmm: the Baum-Welch fit did not converge or a "
            "state collapsed — the regime labelling would be "
            "unreliable.  (Try fewer regimes or a longer / "
            "better-separated feature history.)"
        )

    # ------------------------------------------------------------------
    # 5. Build the output Series — Viterbi labels on the interior block;
    #    the contiguous edge rows carry a NaN label.
    # ------------------------------------------------------------------
    out_values = np.full(n_total, np.nan, dtype=float)
    out_values[first:last] = result.labels.astype(float)
    out_key = f"regime_hmm__k{n_states}__{n_features}feat"
    out_payload = pd.Series(out_values, index=full_index, dtype=float)
    out_payload.name = out_key

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep (OPR10); the fitted model + locks.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "n_states": n_states,
        "transition_matrix": [
            [float(v) for v in row] for row in result.transition_matrix
        ],
        "initial_dist": [float(v) for v in result.initial_dist],
        "means": [[float(v) for v in row] for row in result.means],
        "loglik": float(result.loglik),
        "n_iter": int(result.n_iter),
        "converged": True,
        "decode_method": result.decode_method,    # "viterbi"
        "covariance_type": "full",                 # design-locked (OPR7)
        "algorithm": "baum_welch",                 # design-locked (OPR7)
        "init_method": "kmeans2_pp_seed0",         # design-locked (OPR7)
        "transition_init": "sticky_diagonal_0.9",  # design-locked (OPR7)
        "feature_standardization": "zscore_per_column",
        "canonical_ordering": "ascending_standardized_centroid_grand_mean",
        "label_semantics": "categorical_nonarithmetic",  # P5 disclosure
        "fit_scope": "full_sample",                # LOOK-AHEAD (P5)
        "n_features": n_features,
        "feature_columns": columns,
        "n_obs": n_total,
        "n_core_rows": n_core,
        "n_leading_nan": first,
        "n_trailing_nan": n_total - last,
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


__all__ = ["fit_regime_hmm", "FitRegimeHmmError"]

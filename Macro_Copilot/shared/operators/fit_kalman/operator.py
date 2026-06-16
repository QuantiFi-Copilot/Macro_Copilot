"""fit_kalman — FILTERED time-varying-parameter linear regression.

Finance-blind ``statistical_relationship`` operator and the last A4
model-fit engine.  Runs a random-walk-coefficient dynamic-linear-model
Kalman FILTER (reusing ``shared/quant/kalman.py``) of one designated
target member of a ``SeriesSet`` on the remaining members, and emits the
FILTERED coefficient paths beta_{t|t} (+ a time-varying intercept alpha)
as a ``SeriesSet``.  SeriesSet(y, x1..xN) -> SeriesSet(beta_x1..beta_xN,
alpha).

WHAT IT IS (and is NOT — the forecast boundary):  the emitted beta_{t|t}
is the CURRENT-STATE read of a dynamic relationship — which coefficients
fit the data NOW, using observations only up to t.  This is descriptive,
exactly like the HMM's "which regime now", GARCH's "current vol state",
or rolling_pca's "current factor loadings".  It is NOT a forecast: this
operator NEVER emits a one-step-ahead observation forecast y_hat_{t+1|t}
nor any coefficient beyond the last observation.  Stamped
``fit_scope='filtered'``.

WHY NOT rolling_regression:  rolling_regression gives windowed-OLS betas
(an equal-weight moving box).  fit_kalman is recursive Bayesian state
estimation — every past observation informs beta_t with exponentially-
decaying weight, no fixed window — a genuinely different estimator (the
rolling_beta defer-log entry names THIS engine as the non-composition
extension).

POINT-IN-TIME (honest disclosure):  the STATE recursion is causal — each
beta_t uses observations only up to t.  The observation/process noise
SCALE is a single hyperparameter calibrated once on the full sample
(R = the static OLS residual variance; Q = signal_to_noise_ratio * R),
disclosed in lineage as ``noise_calibration='full_sample_ols_residual_
variance'``.  So this is filtered/causal in the state, with a globally-
calibrated noise scale — it is NOT the strict truncation-invariance of
rolling_pca, and the card says so.

NaN POLICY (TWO-TIER — the recursion is order-sensitive): a row is finite
iff the target AND every regressor are finite there.  Contiguous
LEADING/TRAILING NaN rows are TOLERATED (the filter runs on the interior
finite block); an INTERIOR NaN row is REFUSED (the remedy align_series
ffill is named).  The warmup head (the first n_params-1 rows of the
filtered block) carries NaN.

DESIGN LOCKS (OPR7, lineage-stamped):  the random-walk-coefficient state
model, the diffuse prior (beta_0=0, P_0=kappa*I), the full-sample-OLS
noise calibration, and Q = delta * R * I (isotropic).  A smoother
(beta_{t|T}, full-sample look-ahead), a per-coefficient Q, and an
expanding-window noise calibration are declared planned extensions —
never silent knobs.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind time-varying linear-regression filter;
    zero finance vocabulary; keys beta_<regressor> + alpha.
  - OPR2      : ONE output artifact type — always a ``SeriesSet``.
  - OPR8      : target_key + signal_to_noise_ratio required (no honest
    defaults); params=None refuses.
  - OPR9      : one ``SeriesSet`` in, one ``SeriesSet`` out.
  - OPR10     : one ``OperatorStep``; the OLS anchor, R, delta, kappa,
    the locks + the scope ride in params.
  - OPR11     : beta members in RATIO units; alpha in the effective
    target unit.
  - OPR13     : typed ``FitKalmanError`` refusals (see Raises).
  - OPR14     : pure + rerun-deterministic (sorted regressors; a single
    forward pass; no RNG / optimiser).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import (
    Lineage,
    OperatorStep,
    sanitize_params_for_lineage,
)
from shared.artifacts.missingness import RawNoCleaning
from shared.artifacts.types import SeriesSet
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.fit_kalman.schemas import FitKalmanParams
from shared.quant.kalman import dlm_filter


_OPERATOR_NAME = "fit_kalman"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class FitKalmanError(ValueError):
    """Raised by ``fit_kalman`` on a recoverable user-facing failure
    (a ``ValueError`` subclass, OPR13)."""


def _effective_unit(unit: TimeSeriesUnits, basis: str) -> TimeSeriesUnits:
    """(unit, basis) -> effective unit.  A first difference PRESERVES the
    input unit (finance-blind; the rolling_regression / event_windows
    convention — never a hidden ×100)."""
    return unit


def fit_kalman(
    series_set: SeriesSet,
    *,
    params: Optional[FitKalmanParams] = None,
    config: Optional[OperatorConfig] = None,
) -> SeriesSet:
    """Filtered time-varying-parameter regression of one member of
    ``series_set`` on the others.

    Parameters
    ----------
    series_set :
        An aligned ``SeriesSet`` of >= 2 members sharing one common
        index — the target (``params.target_key``) plus >= 1 regressor.
        Contiguous leading/trailing NaN rows are tolerated; an INTERIOR
        NaN row is refused.
    params :
        ``FitKalmanParams`` — REQUIRED (target_key + signal_to_noise_ratio
        have no honest default).
    config :
        Optional ``OperatorConfig``; ``None`` loads the bundled
        config.yaml (process-cached).

    Returns
    -------
    SeriesSet
        The filtered coefficient paths — one ``beta_<regressor>`` member
        per regressor (RATIO units) plus ``alpha`` (the target's
        effective unit) when ``add_constant`` — on the FULL input index;
        NaN in the warmup head and the edge rows.

    Raises
    ------
    FitKalmanError
        Missing params; a non-SeriesSet input; ``target_key`` absent;
        fewer than 2 members (no regressor); too few finite rows after
        the basis transform; an INTERIOR NaN; a rank-deficient /
        zero-variance design; or a fit whose static-OLS residual
        variance is NUMERICALLY ZERO (R == 0.0 exactly).  NOTE the
        zero-residual refusal fires only at R == 0.0; a merely
        NEAR-degenerate fit (a tiny but non-zero residual, e.g. an
        algebraically-perfect y = 2x leaving ~1e-31) is NOT refused —
        it degrades gracefully (the common R/Q scale cancels out of the
        filtered path).  No relative floor is imposed (m25 / P5).
    """
    # ------------------------------------------------------------------
    # 1. Config identity (OPR12).
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. target_key + signal_to_noise_ratio are methodology — params=None
    #    refuses naming them (OPR8; the rolling_pca / rolling_regression
    #    precedent).
    # ------------------------------------------------------------------
    if params is None:
        raise FitKalmanError(
            "fit_kalman requires explicit params with target_key (which "
            "member is the dependent series) and signal_to_noise_ratio "
            "(how fast the coefficients may move) — both are choices with "
            "no honest default."
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(series_set, SeriesSet):
        raise FitKalmanError(
            "fit_kalman: input must be a SeriesSet artifact; got "
            f"{type(series_set).__name__}."
        )

    all_keys = series_set.keys()  # stable sorted
    if params.target_key not in all_keys:
        raise FitKalmanError(
            f"fit_kalman: target_key {params.target_key!r} is not a "
            f"member of the SeriesSet.  Available keys: {all_keys}."
        )
    regressor_keys = [k for k in all_keys if k != params.target_key]
    if len(regressor_keys) < 1:
        raise FitKalmanError(
            "fit_kalman: needs at least one regressor besides the target "
            f"(the SeriesSet has only {len(all_keys)} member(s)).  Build a "
            "wider SeriesSet (align_series) with >= 2 members."
        )

    # ------------------------------------------------------------------
    # 4. Assemble the aligned frame [target, *regressors] on the common
    #    index, then the two-tier NaN gate.  A row is finite iff the
    #    target AND every regressor is finite there (the recursion is
    #    order-sensitive).
    # ------------------------------------------------------------------
    full_index = series_set.common_index
    n_total = int(len(full_index))
    ordered_cols = [params.target_key] + regressor_keys
    frame = pd.DataFrame(
        {c: series_set.series_by_key[c] for c in ordered_cols},
        index=full_index,
    ).astype(float)

    row_finite = frame.notna().all(axis=1).to_numpy()
    n_finite = int(row_finite.sum())
    if n_finite == 0:
        raise FitKalmanError(
            "fit_kalman: no row has the target and every regressor finite "
            "simultaneously."
        )
    first = int(np.argmax(row_finite))
    last = n_total - int(np.argmax(row_finite[::-1]))  # exclusive
    core_finite = row_finite[first:last]
    n_interior_nan = int((~core_finite).sum())
    if n_interior_nan > 0:
        raise FitKalmanError(
            f"fit_kalman: the aligned inputs have {n_interior_nan} "
            "INTERIOR NaN row(s).  The filter recursion runs over "
            "ADJACENT dates; splicing across an interior gap would "
            "corrupt the state.  Fill the gaps upstream (align_series "
            "ffill) and re-run.  (Contiguous leading/trailing NaN rows "
            "are tolerated.)"
        )

    # ------------------------------------------------------------------
    # 5. Apply the basis UNIFORMLY (raw_value / level_change).  A
    #    level_change diff drops the first row of the finite block.
    # ------------------------------------------------------------------
    block = frame.iloc[first:last]
    if params.basis == "level_change":
        block = block.diff().iloc[1:]
        block_start = first + 1
    else:
        block_start = first
    n_block = int(len(block))

    target_unit = series_set.units_by_key[params.target_key]
    n_params = len(regressor_keys) + (1 if params.add_constant else 0)
    if n_block <= n_params:
        raise FitKalmanError(
            f"fit_kalman: after the {params.basis} transform the finite "
            f"block has {n_block} row(s) <= n_params ({n_params}) — too "
            "few to identify the time-varying coefficients.  Widen the "
            "lookback or drop regressors."
        )

    # ------------------------------------------------------------------
    # 6. Build y, X (constant column LAST so the coefficient order is
    #    [regressors..., alpha]) and run the shared DLM forward filter.
    # ------------------------------------------------------------------
    y = block[params.target_key].to_numpy(dtype=float)
    x_cols = [block[k].to_numpy(dtype=float) for k in regressor_keys]
    if params.add_constant:
        x_cols.append(np.ones(n_block, dtype=float))
    X = np.column_stack(x_cols)

    try:
        result = dlm_filter(y, X, float(params.signal_to_noise_ratio))
    except ValueError as exc:
        raise FitKalmanError(f"fit_kalman: {exc}") from exc

    output_keys = [f"beta_{k}" for k in regressor_keys]
    if params.add_constant:
        output_keys.append("alpha")

    # ------------------------------------------------------------------
    # 7. Lineage — ONE OperatorStep (OPR10).  The OLS anchor, the noise
    #    calibration, the locks + the scope ride in params.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "target_key": params.target_key,
        "regressor_keys": list(regressor_keys),
        "output_keys": list(output_keys),
        "basis": params.basis,
        "add_constant": bool(params.add_constant),
        "signal_to_noise_ratio": float(params.signal_to_noise_ratio),
        "static_ols_coef": [float(v) for v in result.static_ols_coef],
        "observation_variance": float(result.observation_variance),
        "diffuse_prior_variance": float(result.diffuse_prior_variance),
        "n_params": int(n_params),
        "n_warmup": int(result.n_warmup),
        "n_block_rows": int(n_block),
        "n_obs": n_total,
        "n_leading_nan": int(block_start),
        "n_trailing_nan": int(n_total - last),
        "state_model": "random_walk_coefficients",        # OPR7 lock
        "estimator": "dlm_forward_filter",                 # OPR7 lock
        "noise_calibration": "full_sample_ols_residual_variance",
        "process_noise_shape": "isotropic_delta_times_R",  # OPR7 lock
        "prior": "diffuse_zero_mean",                      # OPR7 lock
        "fit_scope": "filtered",                           # P5 (NOT smoothed)
        "effective_target_unit": str(
            _effective_unit(target_unit, params.basis).value
        ),
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series_set.lineage.head_hash,),
    )
    member_lineage: Lineage = series_set.lineage.append(step)

    # ------------------------------------------------------------------
    # 8. Build the output SeriesSet — one coefficient Series per output
    #    key on the FULL input index; warmup + edge rows NaN.
    # ------------------------------------------------------------------
    eff_target_unit = _effective_unit(target_unit, params.basis)
    series_by_key: Dict[str, pd.Series] = {}
    units_by_key: Dict[str, TimeSeriesUnits] = {}
    missingness_by_key: Dict[str, RawNoCleaning] = {}
    upstream_lineage_by_key: Dict[str, Lineage] = {}
    for j, key in enumerate(output_keys):
        col_values = np.full(n_total, np.nan, dtype=float)
        col_values[block_start:block_start + n_block] = result.filtered_states[:, j]
        payload = pd.Series(col_values, index=full_index, dtype=float)
        payload.name = key
        series_by_key[key] = payload
        # beta = (target_unit / regressor_unit) ⇒ RATIO under the V1
        # matching-basis invariant; alpha carries the target's effective
        # unit (the level it sits at).
        units_by_key[key] = (
            eff_target_unit if key == "alpha" else TimeSeriesUnits.RATIO
        )
        missingness_by_key[key] = RawNoCleaning()
        # Per-key UPSTREAM = the input SeriesSet's lineage WITHOUT this
        # step; SeriesSet.get_series / the downstream SeriesSet transformers
        # append ``self.lineage.steps[-1]`` (this fit_kalman step), so
        # storing member_lineage (which already has it) would duplicate it
        # and break ART9 LIN-2 connectivity.  The coefficient paths derive
        # from the whole input set, so every member shares its lineage.
        upstream_lineage_by_key[key] = series_set.lineage

    return SeriesSet(
        series_by_key=series_by_key,
        units_by_key=units_by_key,
        missingness_by_key=missingness_by_key,
        upstream_lineage_by_key=upstream_lineage_by_key,
        common_index=full_index,
        frequency=series_set.frequency,
        lineage=member_lineage,
    )


__all__ = ["fit_kalman", "FitKalmanError"]

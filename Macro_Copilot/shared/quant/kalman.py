"""shared.quant.kalman — the dynamic-linear-model (DLM) forward filter.

A finance-blind Kalman FILTER for TIME-VARYING-PARAMETER linear
regression — the numeric behind the ``fit_kalman`` operator.  No library
(the house style of every other ``shared/quant`` engine); ~one screen of
pure numpy.

The model (a random-walk-coefficient dynamic linear model)::

    observation :  y_t = x_t' beta_t + eps_t,   eps_t ~ N(0, R)
    state       :  beta_t = beta_{t-1} + eta_t,  eta_t ~ N(0, Q)

What we EMIT is the **filtered** coefficient path ``beta_{t|t}`` — the
current-state estimate of the relationship, using observations only up to
``t``.  This is a DESCRIPTIVE read (which coefficients now), exactly like
the HMM's "which regime now" or rolling_pca's "current factor loadings".
It is NOT a forecast: we never emit a one-step-ahead observation forecast
``y_hat_{t+1|t}`` nor any state beyond the last observation.

Determinism (no RNG, no optimiser, byte-reproducible):

  * fixed diffuse prior  ``beta_0 = 0``, ``P_0 = kappa * I``
    (``kappa`` a stamped design-lock — large enough that the prior is
    overwhelmed once ``n_params`` observations have arrived);
  * ``R`` = the full-sample OLS residual variance (a closed-form scale
    hyperparameter — calibrated once, NOT re-estimated per step);
  * ``Q = signal_to_noise_ratio * R * I`` (the SNR ratio is THE knob —
    the only thing that sets how fast the coefficients are allowed to
    move; the common (R, Q) scale cancels out of the filtered path);
  * a single forward filtering pass — no EM, no MLE, no restarts.

The warmup head (the first ``n_params - 1`` rows — a ``p``-coefficient
state is only pinned once ``p`` observations have arrived) is emitted as
NaN, mirroring rolling_pca's ``window - 1`` warmup.

Correctness is pinned by CONSTRUCTION in the tests (a KNOWN time-varying
coefficient is recovered by the filtered path), not by a library oracle —
the same discipline as the other quant engines.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


# A stamped design-lock: the diffuse-prior variance.  Large enough that
# P_0 = kappa * I dominates the data covariance, so beta_0 = 0 is washed
# out after the first n_params observations (the textbook diffuse
# initialisation).  Lineage-stamped by the operator; never a silent knob.
DIFFUSE_PRIOR_VARIANCE: float = 1.0e6


@dataclass(frozen=True)
class DlmFilterResult:
    """The output of :func:`dlm_filter`.

    Attributes
    ----------
    filtered_states :
        ``(n_obs, n_params)`` filtered coefficient paths ``beta_{t|t}``.
        The first ``n_params - 1`` rows (the warmup head) are NaN.
    static_ols_coef :
        ``(n_params,)`` the full-sample OLS coefficients — the anchor the
        time-varying path fluctuates around (disclosed in lineage).
    observation_variance :
        ``R`` — the full-sample OLS residual variance.
    process_variance_scale :
        the signal-to-noise ratio ``Q / R`` actually used.
    diffuse_prior_variance :
        ``kappa`` — the stamped diffuse-prior variance.
    n_warmup :
        the number of leading NaN (warmup) rows = ``n_params - 1``.
    n_obs, n_params :
        shapes.
    """

    filtered_states: np.ndarray
    static_ols_coef: np.ndarray
    observation_variance: float
    process_variance_scale: float
    diffuse_prior_variance: float
    n_warmup: int
    n_obs: int
    n_params: int


def dlm_filter(
    y: np.ndarray,
    X: np.ndarray,
    signal_to_noise_ratio: float,
    *,
    diffuse_prior_variance: float = DIFFUSE_PRIOR_VARIANCE,
    observation_variance: Optional[float] = None,
) -> DlmFilterResult:
    """Run the random-walk-coefficient DLM forward filter.

    Parameters
    ----------
    y :
        ``(n_obs,)`` observation vector.  MUST be fully finite and
        contiguous (the caller handles the two-tier NaN policy and the
        basis transform).
    X :
        ``(n_obs, n_params)`` design matrix.  The caller appends the
        constant column when an intercept is wanted; this routine treats
        every column uniformly.  MUST be fully finite and full column
        rank.
    signal_to_noise_ratio :
        ``delta = Q / R`` — strictly positive.  Larger ⇒ the coefficients
        are allowed to move faster (a more adaptive, noisier path);
        smaller ⇒ a stiffer path closer to the static OLS fit.
    diffuse_prior_variance :
        ``kappa`` (default :data:`DIFFUSE_PRIOR_VARIANCE`).  The prior
        covariance is ``kappa * I``.
    observation_variance :
        an OPTIONAL explicit ``R``.  When ``None`` (the operator's path)
        ``R`` is calibrated from the full-sample OLS residual variance.
        Supplying it externally lets a caller pin the noise scale — used
        to prove the recursion is causal (fix ``R``, truncate, and the
        filtered prefix is byte-identical) and to host a future
        point-in-time (expanding-window) calibration.  Must be > 0.

    Returns
    -------
    DlmFilterResult

    Raises
    ------
    ValueError
        Non-finite input; mismatched shapes; ``n_obs <= n_params``
        (the residual variance / state is unidentified); a non-positive
        ``signal_to_noise_ratio`` or ``diffuse_prior_variance``; a
        rank-deficient design (collinear regressors / a zero-variance
        regressor against the constant); a degenerate (non-positive)
        residual variance.
    """
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    if y.ndim != 1:
        raise ValueError("dlm_filter: y must be 1-D.")
    if X.ndim != 2:
        raise ValueError("dlm_filter: X must be 2-D (n_obs, n_params).")
    n_obs, n_params = X.shape
    if y.shape[0] != n_obs:
        raise ValueError(
            f"dlm_filter: y has {y.shape[0]} rows but X has {n_obs}."
        )
    if not (np.isfinite(y).all() and np.isfinite(X).all()):
        raise ValueError(
            "dlm_filter: y and X must be fully finite (the caller "
            "enforces the two-tier NaN policy)."
        )
    if not signal_to_noise_ratio > 0.0:
        raise ValueError(
            "dlm_filter: signal_to_noise_ratio must be strictly positive "
            f"(got {signal_to_noise_ratio})."
        )
    if not diffuse_prior_variance > 0.0:
        raise ValueError(
            "dlm_filter: diffuse_prior_variance must be strictly positive "
            f"(got {diffuse_prior_variance})."
        )
    if n_obs <= n_params:
        raise ValueError(
            f"dlm_filter: needs more observations ({n_obs}) than "
            f"parameters ({n_params}) to identify the state and the "
            "residual variance."
        )
    if np.linalg.matrix_rank(X) < n_params:
        raise ValueError(
            "dlm_filter: the design matrix is rank-deficient (collinear "
            "regressors, or a zero-variance regressor against the "
            "intercept).  Drop the redundant regressor."
        )

    # ------------------------------------------------------------------
    # Calibrate the noise hyperparameters once (full-sample OLS).
    #   R  = residual variance of the static fit (the observation noise).
    #   Q  = delta * R * I (the process noise; delta = the SNR knob).
    # The common (R, Q) scale cancels out of the filtered path, so only
    # the ratio delta matters — but R sets the units so Q is on the data
    # scale.  beta_ols is the anchor the time-varying path moves around.
    # ------------------------------------------------------------------
    beta_ols, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
    if observation_variance is None:
        resid = y - X @ beta_ols
        ssr = float(resid @ resid)
        observation_variance = ssr / (n_obs - n_params)
        if not observation_variance > 0.0:
            raise ValueError(
                "dlm_filter: the static OLS residual variance is zero "
                "(the regression fits exactly) — there is no noise scale "
                "to calibrate the filter against.  Supply genuinely noisy "
                "data."
            )
    else:
        observation_variance = float(observation_variance)
        if not observation_variance > 0.0:
            raise ValueError(
                "dlm_filter: an explicit observation_variance must be "
                f"strictly positive (got {observation_variance})."
            )
    process_variance = signal_to_noise_ratio * observation_variance

    # ------------------------------------------------------------------
    # The forward filter (random-walk state ⇒ the prediction step is the
    # identity on the mean; only the covariance grows by Q).
    # ------------------------------------------------------------------
    eye = np.eye(n_params, dtype=float)
    Q = process_variance * eye
    beta = np.zeros(n_params, dtype=float)           # beta_0 = 0
    P = diffuse_prior_variance * eye                 # P_0 = kappa * I

    filtered = np.full((n_obs, n_params), np.nan, dtype=float)
    for t in range(n_obs):
        x_t = X[t]
        # Predict (random walk): mean unchanged, covariance grows by Q.
        P_pred = P + Q
        # Update with observation y_t.
        Px = P_pred @ x_t
        innovation = y[t] - float(x_t @ beta)
        innovation_var = float(x_t @ Px) + observation_variance  # >= R > 0
        gain = Px / innovation_var
        beta = beta + gain * innovation
        P = P_pred - np.outer(gain, Px)
        filtered[t] = beta

    # Warmup head: a p-coefficient random-walk state is only pinned once
    # p observations have arrived; the earlier rows are diffuse-prior
    # dominated and emitted as NaN (the rolling_pca window-1 precedent).
    n_warmup = n_params - 1
    if n_warmup > 0:
        filtered[:n_warmup] = np.nan

    return DlmFilterResult(
        filtered_states=filtered,
        static_ols_coef=beta_ols,
        observation_variance=observation_variance,
        process_variance_scale=float(signal_to_noise_ratio),
        diffuse_prior_variance=float(diffuse_prior_variance),
        n_warmup=int(n_warmup),
        n_obs=int(n_obs),
        n_params=int(n_params),
    )


__all__ = ["dlm_filter", "DlmFilterResult", "DIFFUSE_PRIOR_VARIANCE"]

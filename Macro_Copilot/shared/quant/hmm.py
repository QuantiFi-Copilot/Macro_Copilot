"""shared.quant.hmm — Gaussian Hidden-Markov regime fit via Baum-Welch.

The EIGHTH numeric in ``shared/quant/`` (after variance_ratio, hurst,
ou, changepoint, garch, gmm, pca).  Pure, finance-blind, deterministic
per the package boundary rules — the L3 ``fit_regime_hmm`` operator is
the only production consumer.

THE MODEL (a K-state Gaussian HMM — the TEMPORAL sibling of the GMM).
Unlike the GMM (which treats every date independently), the HMM adds a
K×K transition matrix A so regimes PERSIST: the hidden state sequence
is a Markov chain with initial distribution π, transition matrix A and
Gaussian emissions N(μ_k, Σ_k).  Fit by Baum-Welch (EM):

    E-step (LOG-space, log-sum-exp stable): forward log-α, backward
      log-β; γ_t(k) = P(state_t=k | obs); ξ_t(j,k) = P(state_t=j,
      state_{t+1}=k | obs).
    M-step: π = γ_1; A[j,k] = Σ_t ξ_t(j,k) / Σ_t γ_t(j); μ_k, Σ_k =
      γ-weighted Gaussian moments (+ ridge).

The state SEQUENCE is decoded by VITERBI (the most-likely PATH — it is
temporally consistent with the fitted transitions; the marginal
γ-argmax can emit a transition the matrix forbids and is a declared
planned extension).  Viterbi ties break to the lowest state index.

DETERMINISM (the reproducibility the platform sells — IDENTICAL to the
GMM plus the temporal extension):

  - the EM runs on the COLUMN-STANDARDISED features from a FIXED-seed
    k-means++ init; the transition matrix is seeded sticky/diagonal
    (0.9 diagonal) and π uniform — no unfixed randomness anywhere;
  - after EM the K states are RELABELLED into a canonical order
    (ascending standardised-centroid grand-mean), and the relabel
    permutes the means, covariances, π AND BOTH AXES of A consistently;
    the Viterbi decode runs on the relabelled params.

Refuses degenerate input with a plain ``ValueError`` (the operator
pre-validates with its typed error): fewer than ``n_states`` rows; a
non-finite input; or a zero-variance feature column.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.cluster.vq import kmeans2
from scipy.special import logsumexp
from scipy.stats import multivariate_normal


# Locked EM constants (NOT knobs — the gmm precedent).
_MAX_ITER = 200
_TOL = 1e-6
_RIDGE = 1e-6
_SEED = 0
_MIN_MASS = 1e-6
# Deterministic sticky transition-matrix init: 0.9 on the diagonal.
_DIAG_INIT = 0.9
_TINY = 1e-300


@dataclass(frozen=True)
class HmmFitResult:
    """The result of :func:`fit_hmm_em` (canonically ordered).

    Attributes
    ----------
    labels :
        The Viterbi-decoded per-row regime label (0..K-1), canonical
        order.
    means :
        ``(K, n_features)`` standardised emission means, canonical
        order.
    covariances :
        ``(K, n_features, n_features)`` standardised emission
        covariances.
    transition_matrix :
        ``(K, K)`` the fitted transition matrix A (rows sum to 1),
        canonical order (both axes permuted).
    initial_dist :
        ``(K,)`` the fitted initial distribution π, canonical order.
    loglik :
        The converged log-likelihood.
    converged :
        Whether Baum-Welch converged before ``max_iter``.
    n_iter :
        The number of EM iterations run.
    n_states :
        K.
    decode_method :
        ``"viterbi"``.
    """

    labels: np.ndarray
    means: np.ndarray
    covariances: np.ndarray
    transition_matrix: np.ndarray
    initial_dist: np.ndarray
    loglik: float
    converged: bool
    n_iter: int
    n_states: int
    decode_method: str


def _log_emission(xs: np.ndarray, means: np.ndarray, covs: np.ndarray) -> np.ndarray:
    """``(n_rows, K)`` log N(x_t; μ_k, Σ_k) per state."""
    n_states = means.shape[0]
    return np.column_stack([
        multivariate_normal.logpdf(
            xs, mean=means[k], cov=covs[k], allow_singular=False,
        )
        for k in range(n_states)
    ])


def _viterbi(
    log_pi: np.ndarray, log_a: np.ndarray, log_b: np.ndarray,
) -> np.ndarray:
    """The most-likely state path (log-space max-product + traceback);
    ties break to the lowest state index."""
    n, k = log_b.shape
    log_delta = np.empty((n, k), dtype=float)
    psi = np.zeros((n, k), dtype=int)
    log_delta[0] = log_pi + log_b[0]
    for t in range(1, n):
        # scores[j, s] = delta[t-1, j] + A[j, s]
        scores = log_delta[t - 1][:, None] + log_a
        psi[t] = np.argmax(scores, axis=0)  # lowest j on ties
        log_delta[t] = scores[psi[t], np.arange(k)] + log_b[t]
    path = np.empty(n, dtype=int)
    path[-1] = int(np.argmax(log_delta[-1]))
    for t in range(n - 2, -1, -1):
        path[t] = psi[t + 1, path[t + 1]]
    return path


def fit_hmm_em(X: np.ndarray, n_states: int) -> HmmFitResult:
    """Fit a K-state Gaussian HMM to the rows of ``X`` by Baum-Welch.

    Parameters
    ----------
    X :
        A 2-D ``(n_rows, n_features)`` array of FINITE floats (the
        CONTIGUOUS interior feature rows; the caller refuses interior
        NaN and passes the finite core).
    n_states :
        The number of regimes K (``>= 2``).

    Returns
    -------
    HmmFitResult
        Canonically ordered; the Viterbi-decoded state sequence.

    Raises
    ------
    ValueError
        ``n_states < 2``; fewer rows than ``n_states``; a non-finite
        input; or a zero-variance feature column.
    """
    arr = np.asarray(X, dtype=float)
    if arr.ndim != 2:
        raise ValueError("fit_hmm_em: X must be a 2-D array.")
    n, d = arr.shape
    k = int(n_states)
    if k < 2:
        raise ValueError(f"fit_hmm_em: n_states must be >= 2 (got {k}).")
    if n < k:
        raise ValueError(
            f"fit_hmm_em: needs at least n_states = {k} rows (got {n})."
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "fit_hmm_em: all values must be finite (the operator refuses "
            "interior NaN upstream)."
        )
    col_std = arr.std(axis=0)
    if np.any(col_std == 0.0):
        bad = int(np.argmin(col_std))
        raise ValueError(
            f"fit_hmm_em: feature column {bad} has zero variance — the "
            "emission covariance is degenerate."
        )

    xs = (arr - arr.mean(axis=0)) / col_std

    # Deterministic init: k-means++ emissions, sticky-diagonal A, uniform π.
    _centroids, init_labels = kmeans2(
        xs, k, seed=_SEED, minit="++", missing="warn",
    )
    eye = np.eye(d)
    means = np.empty((k, d), dtype=float)
    covs = np.empty((k, d, d), dtype=float)
    for s in range(k):
        members = xs[init_labels == s]
        if members.shape[0] >= 2:
            means[s] = members.mean(axis=0)
            covs[s] = np.cov(members, rowvar=False) + _RIDGE * eye
        else:
            means[s] = xs.mean(axis=0)
            covs[s] = np.cov(xs, rowvar=False) + _RIDGE * eye
    pi = np.full(k, 1.0 / k, dtype=float)
    a = np.full((k, k), (1.0 - _DIAG_INIT) / (k - 1), dtype=float)
    np.fill_diagonal(a, _DIAG_INIT)

    prev_ll = -np.inf
    total_ll = -np.inf
    converged = False
    n_iter = 0
    gamma = np.full((n, k), 1.0 / k, dtype=float)
    for n_iter in range(1, _MAX_ITER + 1):
        try:
            log_b = _log_emission(xs, means, covs)
        except (np.linalg.LinAlgError, ValueError):
            converged = False
            break
        log_a = np.log(a + _TINY)
        log_pi = np.log(pi + _TINY)

        # Forward (log-α).
        log_alpha = np.empty((n, k), dtype=float)
        log_alpha[0] = log_pi + log_b[0]
        for t in range(1, n):
            log_alpha[t] = (
                logsumexp(log_alpha[t - 1][:, None] + log_a, axis=0)
                + log_b[t]
            )
        total_ll = float(logsumexp(log_alpha[-1]))

        # Backward (log-β).
        log_beta = np.zeros((n, k), dtype=float)
        for t in range(n - 2, -1, -1):
            log_beta[t] = logsumexp(
                log_a + log_b[t + 1][None, :] + log_beta[t + 1][None, :],
                axis=1,
            )

        # Posteriors.
        log_gamma = log_alpha + log_beta - total_ll
        gamma = np.exp(log_gamma)
        nk = gamma.sum(axis=0)
        if np.any(nk < _MIN_MASS):  # a state collapsed
            converged = False
            break

        # ξ summed over t (vectorised over the sequence).
        log_xi = (
            log_alpha[:-1, :, None]
            + log_a[None, :, :]
            + log_b[1:, None, :]
            + log_beta[1:, None, :]
            - total_ll
        )
        xi_sum = np.exp(log_xi).sum(axis=0)  # (K, K)

        # M-step.
        pi = gamma[0]
        denom = gamma[:-1].sum(axis=0)
        a = xi_sum / np.maximum(denom[:, None], _TINY)
        a = a / a.sum(axis=1, keepdims=True)
        means = (gamma.T @ xs) / nk[:, None]
        for s in range(k):
            diff = xs - means[s]
            covs[s] = (gamma[:, s, None] * diff).T @ diff / nk[s] + _RIDGE * eye

        if abs(total_ll - prev_ll) < _TOL:
            converged = True
            break
        prev_ll = total_ll

    # Canonical relabel: ascending standardised-centroid grand-mean,
    # ties lexsorted — permutes means/covs/π AND BOTH AXES of A.
    grand = means.mean(axis=1)
    sort_keys = tuple(means[:, j] for j in range(d - 1, -1, -1)) + (grand,)
    order = np.lexsort(sort_keys)
    means = means[order]
    covs = covs[order]
    pi = pi[order]
    a = a[order][:, order]

    # Viterbi decode on the RELABELLED params.
    try:
        log_b = _log_emission(xs, means, covs)
        labels = _viterbi(np.log(pi + _TINY), np.log(a + _TINY), log_b)
    except (np.linalg.LinAlgError, ValueError):
        converged = False
        labels = np.zeros(n, dtype=int)

    return HmmFitResult(
        labels=labels,
        means=means,
        covariances=covs,
        transition_matrix=a,
        initial_dist=pi,
        loglik=float(total_ll),
        converged=converged,
        n_iter=n_iter,
        n_states=k,
        decode_method="viterbi",
    )


__all__ = ["fit_hmm_em", "HmmFitResult"]

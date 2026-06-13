"""shared.quant.gmm — Gaussian-mixture regime fit via EM.

The SIXTH numeric in ``shared/quant/`` (after variance_ratio, hurst,
ou, changepoint, garch).  Pure, finance-blind, deterministic per the
package boundary rules — the L3 ``fit_regime_gmm`` operator is the only
production consumer.

THE MODEL (a K-component Gaussian Mixture, full covariances, fit by
EM).  Each per-date feature vector x_t (a ROW of the feature matrix) is
soft-assigned to K Gaussians N(μ_k, Σ_k) with weights π_k::

    E-step:  r_tk ∝ π_k · N(x_t; μ_k, Σ_k)         (log-sum-exp stable)
    M-step:  π_k, μ_k, Σ_k ← weighted moments of r_·k
             Σ_k ← Σ_k + ridge·I                   (PD regularisation)

The regime label at t is ``argmax_k r_tk``.

DETERMINISM (the load-bearing property — the platform sells "the same
numbers six months later"):

  - the EM runs on the COLUMN-STANDARDISED feature matrix (z-score per
    column) so one large-scale feature cannot dominate, and the
    initialisation is ``scipy.cluster.vq.kmeans2`` with a FIXED seed
    (k-means++ start) — no unfixed randomness anywhere;
  - GMM components are UNORDERED, so after EM the components are
    RELABELLED into a canonical order — ascending by the grand-mean of
    the (standardised) centroid μ_k, ties broken lexicographically on
    the full μ_k — so "state 0" is always the same regime across
    reruns and across re-fits on overlapping windows.

The function RETURNS the fit (already canonically ordered, with
``converged``); the operator REFUSES on non-convergence or a collapsed
component.  It raises ``ValueError`` only on degenerate input the math
cannot start on (fewer than ``n_states`` rows, non-finite, a
zero-variance feature column).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.cluster.vq import kmeans2
from scipy.special import logsumexp
from scipy.stats import multivariate_normal


# Locked EM constants (NOT knobs — the garch _ALPHA0 precedent).
_MAX_ITER = 200
_TOL = 1e-6
_RIDGE = 1e-6
_SEED = 0
# A component with less than this responsibility mass has collapsed.
_MIN_MASS = 1e-6


@dataclass(frozen=True)
class GmmFitResult:
    """The result of :func:`fit_gmm_em` (canonically ordered).

    Attributes
    ----------
    labels :
        The per-row most-likely regime label (0..K-1), canonical order.
    means :
        ``(K, n_features)`` standardised component means, canonical
        order.
    covariances :
        ``(K, n_features, n_features)`` standardised component
        covariances (for the planned state-probabilities operator).
    weights :
        ``(K,)`` mixture weights, canonical order.
    responsibilities :
        ``(n_rows, K)`` soft assignments (for the planned probs
        operator).
    loglik :
        The converged log-likelihood.
    converged :
        Whether EM converged before ``max_iter``.
    n_iter :
        The number of EM iterations run.
    n_states :
        ``K``.
    """

    labels: np.ndarray
    means: np.ndarray
    covariances: np.ndarray
    weights: np.ndarray
    responsibilities: np.ndarray
    loglik: float
    converged: bool
    n_iter: int
    n_states: int


def fit_gmm_em(X: np.ndarray, n_states: int) -> GmmFitResult:
    """Fit a K-component Gaussian mixture to the rows of ``X`` by EM.

    Parameters
    ----------
    X :
        A 2-D ``(n_rows, n_features)`` array of FINITE floats (the
        complete-case feature rows; the caller drops NaN rows first).
    n_states :
        The number of regimes ``K`` (``>= 2``).

    Returns
    -------
    GmmFitResult
        Canonically ordered (state 0 = lowest-centroid regime).

    Raises
    ------
    ValueError
        ``n_states < 2``; fewer rows than ``n_states``; a non-finite
        input; or a zero-variance feature column.
    """
    arr = np.asarray(X, dtype=float)
    if arr.ndim != 2:
        raise ValueError("fit_gmm_em: X must be a 2-D array.")
    n, d = arr.shape
    if n_states < 2:
        raise ValueError(
            f"fit_gmm_em: n_states must be >= 2 (got {n_states})."
        )
    if n < n_states:
        raise ValueError(
            f"fit_gmm_em: needs at least n_states = {n_states} rows "
            f"(got {n})."
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "fit_gmm_em: all values must be finite (drop NaN rows "
            "upstream)."
        )
    col_std = arr.std(axis=0)
    if np.any(col_std == 0.0):
        bad = int(np.argmin(col_std))
        raise ValueError(
            f"fit_gmm_em: feature column {bad} has zero variance — the "
            "covariance is degenerate."
        )

    # Column-standardise (z-score) so scale cannot dominate the fit.
    col_mean = arr.mean(axis=0)
    xs = (arr - col_mean) / col_std

    # Deterministic init: k-means++ with a FIXED seed (OPR14).
    _centroids, init_labels = kmeans2(
        xs, n_states, seed=_SEED, minit="++", missing="warn",
    )
    eye = np.eye(d)
    means = np.empty((n_states, d), dtype=float)
    covs = np.empty((n_states, d, d), dtype=float)
    weights = np.empty(n_states, dtype=float)
    for k in range(n_states):
        members = xs[init_labels == k]
        if members.shape[0] >= 2:
            means[k] = members.mean(axis=0)
            covs[k] = np.cov(members, rowvar=False) + _RIDGE * eye
        else:  # empty/singleton init cell — seed from the global moments
            means[k] = xs.mean(axis=0)
            covs[k] = np.cov(xs, rowvar=False) + _RIDGE * eye
        weights[k] = max(np.mean(init_labels == k), _MIN_MASS)
    weights = weights / weights.sum()

    prev_ll = -np.inf
    total_ll = -np.inf  # bound before the loop — a first-iteration
    # E-step failure breaks with converged=False without a NameError.
    converged = False
    n_iter = 0
    resp = np.full((n, n_states), 1.0 / n_states, dtype=float)
    for n_iter in range(1, _MAX_ITER + 1):
        # E-step (log-domain).
        log_comp = np.empty((n, n_states), dtype=float)
        try:
            for k in range(n_states):
                log_comp[:, k] = (
                    np.log(weights[k] + 1e-300)
                    + multivariate_normal.logpdf(
                        xs, mean=means[k], cov=covs[k], allow_singular=False,
                    )
                )
        except (np.linalg.LinAlgError, ValueError):
            converged = False  # a covariance went singular despite ridge
            break
        ll_per = logsumexp(log_comp, axis=1)
        total_ll = float(ll_per.sum())
        resp = np.exp(log_comp - ll_per[:, None])

        # M-step.
        nk = resp.sum(axis=0)
        if np.any(nk < _MIN_MASS):  # a component collapsed
            converged = False
            break
        weights = nk / n
        means = (resp.T @ xs) / nk[:, None]
        for k in range(n_states):
            diff = xs - means[k]
            covs[k] = (resp[:, k, None] * diff).T @ diff / nk[k] + _RIDGE * eye

        if abs(total_ll - prev_ll) < _TOL:
            converged = True
            break
        prev_ll = total_ll

    labels = resp.argmax(axis=1)
    # total_ll is the most-converged log-likelihood (the loop breaks
    # before re-stamping prev_ll); -inf only if the first E-step failed
    # (then converged=False and the operator refuses).
    loglik = float(total_ll)

    # Canonical relabel: ascending by the grand-mean of the centroid,
    # ties broken lexicographically on the full mean vector (OPR14).
    grand = means.mean(axis=1)
    sort_keys = tuple(means[:, j] for j in range(d - 1, -1, -1)) + (grand,)
    order = np.lexsort(sort_keys)
    remap = np.empty(n_states, dtype=int)
    remap[order] = np.arange(n_states)
    labels = remap[labels]
    means = means[order]
    covs = covs[order]
    weights = weights[order]
    resp = resp[:, order]

    return GmmFitResult(
        labels=labels,
        means=means,
        covariances=covs,
        weights=weights,
        responsibilities=resp,
        loglik=loglik,
        converged=converged,
        n_iter=n_iter,
        n_states=int(n_states),
    )


__all__ = ["fit_gmm_em", "GmmFitResult"]

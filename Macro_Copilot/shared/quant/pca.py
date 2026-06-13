"""shared.quant.pca — principal-component decomposition via SVD.

The SEVENTH numeric in ``shared/quant/`` (after variance_ratio, hurst,
ou, changepoint, garch, gmm).  Pure, finance-blind, deterministic per
the package boundary rules — the L3 ``pca_decompose`` operator is the
only production consumer.

THE DECOMPOSITION (correlation PCA, design-locked).  For a feature
matrix X (rows = dates, columns = features), the columns are
z-score-standardised (subtract the column mean, divide by the column
std — so one large-scale feature cannot dominate, the right default for
the HETEROGENEOUS feature panels a finance-blind operator sees), then
the principal directions are the right singular vectors of the
standardised matrix::

    Xz = (X − μ) / σ
    U, S, Vᵀ = svd(Xz)            (economy SVD — deterministic)
    loadings_k        = Vᵀ_k                 (the k-th eigenvector)
    factor_scores_·k  = (U·S)_·k = Xz · loadingsᵀ
    eigenvalue_k      = S_k² / (n − 1)
    explained_var_k   = eigenvalue_k / Σ eigenvalues

THE SIGN CONVENTION (the determinism crux — OPR14).  An eigenvector and
its negation are both valid, so without a canonical sign the factor
scores flip across reruns.  Each component is flipped so its
LARGEST-|loading| element is POSITIVE (ties broken by the lowest column
index — ``argmax`` returns the first).  This is finance-blind (it does
NOT anchor on any "first" / "longest-tenor" feature — the rates PCA
primitive's curve-position anchor is a finance leak and is NOT reused
here).

NEAR-DEGENERATE DISCLOSURE: when two retained singular values are within
a relative tolerance, the eigenvector subspace is non-unique (the
components can rotate).  This is DISCLOSED via ``near_degenerate``
(the operator stamps it) — not refused.  A genuinely RANK-DEFICIENT
retained component (a feature that is a linear combination of the
others — its singular value is ~0) IS refused (the loading is
undefined).

Refuses degenerate input with a plain ``ValueError`` (the operator
pre-validates with its typed error): ``n_components`` out of
``[1, n_features]``; fewer than ``n_components`` rows; a non-finite
input; a zero-variance feature column; or a rank-deficient retained
component.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


# A retained component whose singular value is this small RELATIVE to
# the largest is rank-deficient (its loading is undefined) — refuse.
_RANK_TOL = 1e-8
# Two retained singular values within this relative gap are a near-tie
# (the subspace is non-unique) — disclose, do not refuse.
_TIE_TOL = 1e-6


@dataclass(frozen=True)
class PcaFitResult:
    """The result of :func:`fit_pca` (canonical sign).

    Attributes
    ----------
    factor_scores :
        ``(n_rows, K)`` factor-score projections (canonical sign).
    loadings :
        ``(K, n_features)`` eigenvectors (canonical sign).
    explained_variance_ratio :
        ``(K,)`` eigenvalue_k / Σ(all eigenvalues) — sums to 1 only
        when K == n_features (the truncation is disclosed; the
        denominator is the FULL spectrum).
    singular_values :
        ``(K,)`` the retained singular values.
    column_means, column_stds :
        ``(n_features,)`` the standardisation moments.
    n_components :
        K.
    near_degenerate :
        Whether two retained singular values are within the relative
        tie tolerance (the subspace is non-unique — disclosed).
    """

    factor_scores: np.ndarray
    loadings: np.ndarray
    explained_variance_ratio: np.ndarray
    singular_values: np.ndarray
    column_means: np.ndarray
    column_stds: np.ndarray
    n_components: int
    near_degenerate: bool


def fit_pca(X: np.ndarray, n_components: int) -> PcaFitResult:
    """Correlation-PCA decomposition of the rows of ``X``.

    Parameters
    ----------
    X :
        A 2-D ``(n_rows, n_features)`` array of FINITE floats (the
        complete-case feature rows; the caller drops NaN rows first).
    n_components :
        The number of components K to retain (``1 <= K <= n_features``).

    Returns
    -------
    PcaFitResult
        Canonical-sign loadings + factor scores.

    Raises
    ------
    ValueError
        ``n_components`` out of ``[1, n_features]``; fewer than
        ``n_components`` rows; a non-finite input; a zero-variance
        feature column; or a rank-deficient retained component.
    """
    arr = np.asarray(X, dtype=float)
    if arr.ndim != 2:
        raise ValueError("fit_pca: X must be a 2-D array.")
    n, d = arr.shape
    if n_components < 1 or n_components > d:
        raise ValueError(
            f"fit_pca: n_components must be in [1, {d}] (got "
            f"{n_components})."
        )
    if n < n_components:
        raise ValueError(
            f"fit_pca: needs at least n_components = {n_components} rows "
            f"(got {n})."
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "fit_pca: all values must be finite (drop NaN rows "
            "upstream)."
        )
    col_mean = arr.mean(axis=0)
    col_std = arr.std(axis=0)
    if np.any(col_std == 0.0):
        bad = int(np.argmin(col_std))
        raise ValueError(
            f"fit_pca: feature column {bad} has zero variance — the "
            "correlation is undefined."
        )

    xz = (arr - col_mean) / col_std
    u, s, vt = np.linalg.svd(xz, full_matrices=False)

    # Rank-deficiency in the retained subspace: a retained singular
    # value negligible relative to the largest -> undefined loading.
    if s[0] <= 0.0 or s[n_components - 1] / s[0] < _RANK_TOL:
        raise ValueError(
            "fit_pca: a retained component is rank-deficient (a feature "
            "is a linear combination of the others) — its loading is "
            "undefined.  Reduce n_components or drop a redundant "
            "feature."
        )

    eigenvalues = s ** 2 / (n - 1)
    evr = eigenvalues / eigenvalues.sum()
    loadings = vt[:n_components].copy()
    scores = (u[:, :n_components] * s[:n_components]).copy()

    # Canonical sign: the largest-|loading| element is positive (ties
    # broken by the lowest column index — argmax returns the first).
    for k in range(n_components):
        j = int(np.argmax(np.abs(loadings[k])))
        if loadings[k, j] < 0.0:
            loadings[k] = -loadings[k]
            scores[:, k] = -scores[:, k]

    near_degenerate = False
    if n_components >= 2:
        retained = s[:n_components]
        gaps = np.abs(np.diff(retained)) / (retained[:-1] + 1e-300)
        near_degenerate = bool(np.any(gaps < _TIE_TOL))

    return PcaFitResult(
        factor_scores=scores,
        loadings=loadings,
        explained_variance_ratio=evr[:n_components].copy(),
        singular_values=s[:n_components].copy(),
        column_means=col_mean,
        column_stds=col_std,
        n_components=int(n_components),
        near_degenerate=near_degenerate,
    )


__all__ = ["fit_pca", "PcaFitResult"]

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
from typing import Optional

import numpy as np


# A retained component whose singular value is this small RELATIVE to
# the largest is rank-deficient (its loading is undefined) — refuse.
_RANK_TOL = 1e-8
# Two retained singular values within this relative gap are a near-tie
# (the subspace is non-unique) — disclose, do not refuse.
_TIE_TOL = 1e-6


@dataclass(frozen=True)
class PcaSvdCore:
    """The raw, convention-free SVD decomposition of a PREPARED matrix.

    This is the single-source-of-truth finance-blind numerical core
    (P10 / plan §8.1): the economy SVD + the ``S²/(n−1)`` eigenvalue
    derivation + the FULL-spectrum variance shares + the K-truncation.
    It carries NO standardisation choice and NO sign convention — those
    are the CALLER's responsibility (correlation vs covariance PCA;
    largest-|loading| vs curve-position sign anchor), exactly mirroring
    how :func:`shared.quant.ou.fit_ou_core` returns the raw (α, β, φ)
    regression algebra and each caller layers its own gate on top.

    Attributes
    ----------
    u, s, vt :
        The economy ``np.linalg.svd(M, full_matrices=False)`` of the
        already-prepared matrix ``M`` (FULL, un-truncated).
    loadings :
        ``(K, n_features)`` — ``vt[:K]`` (a COPY; the caller may flip
        signs in place).
    factor_scores :
        ``(n_rows, K)`` — ``(u[:, :K] · s[:K])`` (a COPY).
    singular_values :
        ``(K,)`` — ``s[:K]`` (a copy).
    eigenvalues_full :
        ``(min(n_rows, n_features),)`` — ``s² / denom`` over the FULL
        spectrum (NOT truncated; the variance-share denominator).
    variance_share_full :
        ``(len(eigenvalues_full),)`` — ``eigenvalues_full / Σ`` over the
        FULL spectrum (zeros when the total variance is non-positive).
    n_components :
        K.
    """

    u: np.ndarray
    s: np.ndarray
    vt: np.ndarray
    loadings: np.ndarray
    factor_scores: np.ndarray
    singular_values: np.ndarray
    eigenvalues_full: np.ndarray
    variance_share_full: np.ndarray
    n_components: int


def pca_svd_core(
    prepared: np.ndarray, n_components: int, *, denom: float,
) -> PcaSvdCore:
    """Economy SVD + eigenvalue/variance-share derivation + truncation.

    The ONE place the principal-component SVD numerics live (P10): both
    :func:`fit_pca` (correlation PCA — z-scored input) and
    ``shared.analytics.stats.pca_yield_changes`` (covariance PCA —
    demeaned input) prepare their own matrix and delegate the
    decomposition here, so the SVD math is written exactly once.

    Parameters
    ----------
    prepared :
        The already-standardised ``(n_rows, n_features)`` matrix to
        decompose (z-scored for correlation PCA, demeaned for
        covariance PCA — the caller decides).  Must be finite.
    n_components :
        K, the number of retained components (``1 <= K <= n_features``;
        the caller validates the bounds).
    denom :
        The eigenvalue denominator (``S² / denom``).  Callers pass
        ``n_rows − 1`` for the unbiased sample convention.

    Returns
    -------
    PcaSvdCore
        The raw decomposition (no sign convention applied).
    """
    u, s, vt = np.linalg.svd(prepared, full_matrices=False)
    eigenvalues_full = (s ** 2) / denom
    total = float(eigenvalues_full.sum())
    if total > 0.0:
        variance_share_full = eigenvalues_full / total
    else:
        variance_share_full = np.zeros_like(eigenvalues_full)
    loadings = vt[:n_components].copy()
    factor_scores = (u[:, :n_components] * s[:n_components]).copy()
    return PcaSvdCore(
        u=u,
        s=s,
        vt=vt,
        loadings=loadings,
        factor_scores=factor_scores,
        singular_values=s[:n_components].copy(),
        eigenvalues_full=eigenvalues_full,
        variance_share_full=variance_share_full,
        n_components=int(n_components),
    )


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

    # Correlation PCA: z-score each column, then delegate the SVD +
    # eigenvalue / variance-share numerics to the shared core (P10 —
    # the SAME decomposition the covariance-PCA primitive uses).  The
    # standardisation above and the sign convention below are this
    # estimator's own layer on top of the convention-free core.
    xz = (arr - col_mean) / col_std
    core = pca_svd_core(xz, n_components, denom=n - 1)
    s = core.s

    # Rank-deficiency in the retained subspace: a retained singular
    # value negligible relative to the largest -> undefined loading.
    if s[0] <= 0.0 or s[n_components - 1] / s[0] < _RANK_TOL:
        raise ValueError(
            "fit_pca: a retained component is rank-deficient (a feature "
            "is a linear combination of the others) — its loading is "
            "undefined.  Reduce n_components or drop a redundant "
            "feature."
        )

    evr = core.variance_share_full
    loadings = core.loadings
    scores = core.factor_scores

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
        singular_values=core.singular_values,
        column_means=col_mean,
        column_stds=col_std,
        n_components=int(n_components),
        near_degenerate=near_degenerate,
    )


def reconstruct_residual(
    fit: PcaFitResult, X: np.ndarray, target_index: int,
) -> np.ndarray:
    """Residual of column ``target_index`` against its top-k PCA
    reconstruction, in the column's ORIGINAL units.

    The rank-k reconstruction in standardised space is
    ``X̂z = factor_scores · loadings``; un-standardising column ``j``
    (``·σ_j + μ_j``) gives the PCA-implied level, and the residual is
    ``observed − implied``.

    The residual is SIGN-INVARIANT: a principal-component sign flip
    flips both the scores and the loadings, leaving ``X̂z`` (and hence
    the residual) unchanged — so it does NOT depend on the PCA sign
    convention.

    Parameters
    ----------
    fit :
        A :class:`PcaFitResult` from :func:`fit_pca` on ``X``.
    X :
        The ORIGINAL (un-standardised) feature matrix the fit was
        computed on (``(n_rows, n_features)``).
    target_index :
        The column whose residual to compute.

    Returns
    -------
    np.ndarray
        The ``(n_rows,)`` residual of the target column in its original
        units (``observed − top-k-PCA reconstruction``).
    """
    arr = np.asarray(X, dtype=float)
    j = int(target_index)
    if j < 0 or j >= arr.shape[1]:
        raise ValueError(
            f"reconstruct_residual: target_index {j} out of range "
            f"[0, {arr.shape[1] - 1}]."
        )
    xhat_z = fit.factor_scores @ fit.loadings  # rank-k, standardised
    reconstruction = (
        xhat_z[:, j] * fit.column_stds[j] + fit.column_means[j]
    )
    return arr[:, j] - reconstruction


@dataclass(frozen=True)
class RollingPcaResult:
    """The result of :func:`rolling_pca_scores`.

    Attributes
    ----------
    scores :
        ``(n_rows, K)`` the POINT-IN-TIME factor scores — row ``t`` is
        the projection of the standardised features at ``t`` onto the
        components fit on the trailing window ``[t-W+1, t]``; the warmup
        head (rows before the first full window) is NaN.
    n_sign_flips :
        The number of cross-window sign flips applied (a factor-rotation
        diagnostic — many flips ⇒ the factor structure is rotating).
    n_windows :
        The number of full windows scored.
    near_degenerate_any :
        Whether any window had near-tied singular values (a non-unique
        subspace — disclosed, not refused).
    last_loadings, last_explained_variance_ratio, last_singular_values :
        The MOST RECENT window's fit (a representative — one-per-window
        loadings do not fit in lineage).
    """

    scores: np.ndarray
    n_sign_flips: int
    n_windows: int
    near_degenerate_any: bool
    last_loadings: np.ndarray
    last_explained_variance_ratio: np.ndarray
    last_singular_values: np.ndarray


def rolling_pca_scores(
    X: np.ndarray, window: int, n_components: int,
) -> RollingPcaResult:
    """POINT-IN-TIME rolling correlation-PCA factor scores.

    At each date ``t >= window-1`` the PCA is fit on the STRICTLY
    TRAILING window ``X[t-window+1 : t+1]`` (so the score at ``t`` uses
    ONLY data up to ``t`` — no look-ahead), and the score is the
    projection of row ``t``'s window-standardised features onto that
    window's top-``n_components`` components.  Each window's loadings
    are SIGN-ALIGNED to the previous window's (flip component ``k`` when
    ``dot(loadings_t[k], loadings_{t-1}[k]) < 0``) so the factor series
    is continuous — the cross-window sign-flip count is a
    factor-rotation diagnostic.

    Parameters
    ----------
    X :
        A 2-D ``(n_rows, n_features)`` array of FINITE floats (the
        contiguous interior feature rows; the caller refuses interior
        NaN).
    window :
        The trailing window length ``W`` (``>= n_features + 1`` so each
        window's PCA is full-rank).
    n_components :
        The number of components K to retain.

    Returns
    -------
    RollingPcaResult

    Raises
    ------
    ValueError
        ``window`` larger than the row count; or a degenerate window
        (propagated from :func:`fit_pca`).
    """
    arr = np.asarray(X, dtype=float)
    n, d = arr.shape
    if window > n:
        raise ValueError(
            f"rolling_pca_scores: window {window} exceeds the row count "
            f"{n}."
        )
    scores = np.full((n, n_components), np.nan, dtype=float)
    prev_loadings: Optional[np.ndarray] = None
    n_sign_flips = 0
    near_degenerate_any = False
    last_fit: Optional[PcaFitResult] = None
    for t in range(window - 1, n):
        win = arr[t - window + 1:t + 1]
        fit = fit_pca(win, n_components)
        loadings = fit.loadings.copy()
        if prev_loadings is not None:
            for kk in range(n_components):
                if float(np.dot(loadings[kk], prev_loadings[kk])) < 0.0:
                    loadings[kk] = -loadings[kk]
                    n_sign_flips += 1
        prev_loadings = loadings
        # Project row t (window-standardised) onto the aligned loadings.
        z_t = (arr[t] - fit.column_means) / fit.column_stds
        scores[t] = z_t @ loadings.T
        near_degenerate_any = near_degenerate_any or fit.near_degenerate
        last_fit = fit

    assert last_fit is not None  # window <= n guarantees >= 1 window
    return RollingPcaResult(
        scores=scores,
        n_sign_flips=n_sign_flips,
        n_windows=n - window + 1,
        near_degenerate_any=near_degenerate_any,
        last_loadings=last_fit.loadings,
        last_explained_variance_ratio=last_fit.explained_variance_ratio,
        last_singular_values=last_fit.singular_values,
    )


__all__ = [
    "fit_pca", "reconstruct_residual", "rolling_pca_scores",
    "pca_svd_core",
    "PcaFitResult", "RollingPcaResult", "PcaSvdCore",
]

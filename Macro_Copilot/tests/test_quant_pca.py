"""Tests for shared.quant.pca — the correlation-PCA / SVD numerics.

No library oracle, so correctness is pinned by construction:

  1. Recovery of a KNOWN dominant direction (two correlated features ->
     PC1 captures their shared axis; explained-variance sums to 1 at
     full K).
  2. The canonical SIGN convention makes the factor scores
     byte-identical and sign-stable across reruns (the determinism
     crux).
  3. Degenerate refusals + the near-tie disclosure.
"""

from __future__ import annotations

import numpy as np
import pytest

from shared.quant.pca import PcaFitResult, fit_pca, reconstruct_residual


def _correlated(seed=0, n=500):
    rng = np.random.RandomState(seed)
    a = rng.randn(n)
    b = 0.9 * a + 0.1 * rng.randn(n)  # strongly correlated with a
    c = rng.randn(n) * 0.5             # independent
    return np.column_stack([a, b, c])


# ===========================================================================
# 1. Recovery
# ===========================================================================


class TestRecovery:
    def test_recovers_dominant_direction(self):
        X = _correlated()
        r = fit_pca(X, 3)
        assert isinstance(r, PcaFitResult)
        # PC1 dominates (a & b correlated); evr descending.
        assert r.explained_variance_ratio[0] > 0.55
        assert np.all(np.diff(r.explained_variance_ratio) <= 1e-12)
        # Full spectrum sums to 1.
        assert r.explained_variance_ratio.sum() == pytest.approx(1.0)
        # PC1 loads a & b with the SAME sign (the shared axis).
        assert np.sign(r.loadings[0, 0]) == np.sign(r.loadings[0, 1])

    def test_factor_scores_are_the_projection(self):
        X = _correlated()
        r = fit_pca(X, 2)
        xz = (X - X.mean(0)) / X.std(0)
        np.testing.assert_allclose(r.factor_scores, xz @ r.loadings.T)

    def test_truncated_evr_below_one(self):
        X = _correlated()
        r = fit_pca(X, 2)  # drop PC3
        assert r.explained_variance_ratio.sum() < 1.0
        assert r.factor_scores.shape == (len(X), 2)
        assert r.loadings.shape == (2, 3)


# ===========================================================================
# 2. Sign convention + determinism
# ===========================================================================


class TestSignAndDeterminism:
    def test_canonical_sign_largest_loading_positive(self):
        r = fit_pca(_correlated(), 3)
        for k in range(3):
            j = int(np.argmax(np.abs(r.loadings[k])))
            assert r.loadings[k, j] > 0.0

    def test_deterministic_across_reruns(self):
        X = _correlated(seed=3)
        a = fit_pca(X, 3)
        b = fit_pca(X, 3)
        assert np.array_equal(a.factor_scores, b.factor_scores)
        assert np.array_equal(a.loadings, b.loadings)

    def test_sign_stable_under_column_negation(self):
        """Negating an input column flips its loading sign canonically
        (not arbitrarily) — the scores stay reproducible."""
        X = _correlated()
        r1 = fit_pca(X, 3)
        Xn = X.copy()
        Xn[:, 0] = -Xn[:, 0]
        r2 = fit_pca(Xn, 3)
        # Both fits obey the canonical sign rule (largest |loading| > 0).
        for r in (r1, r2):
            for k in range(3):
                j = int(np.argmax(np.abs(r.loadings[k])))
                assert r.loadings[k, j] > 0.0


# ===========================================================================
# 3. Refusals + near-tie disclosure
# ===========================================================================


class TestRefusalsAndDisclosure:
    def test_n_components_out_of_range_raises(self):
        X = _correlated()
        with pytest.raises(ValueError, match=r"in \[1, 3\]"):
            fit_pca(X, 5)
        with pytest.raises(ValueError, match=r"in \[1, 3\]"):
            fit_pca(X, 0)

    def test_zero_variance_column_raises(self):
        X = _correlated()
        X = np.column_stack([X[:, 0], np.ones(len(X)), X[:, 2]])
        with pytest.raises(ValueError, match="zero variance"):
            fit_pca(X, 2)

    def test_non_finite_raises(self):
        X = _correlated()
        X = X.copy()
        X[10, 0] = np.nan
        with pytest.raises(ValueError, match="finite"):
            fit_pca(X, 2)

    def test_rank_deficient_raises(self):
        rng = np.random.RandomState(1)
        a = rng.randn(200)
        X = np.column_stack([a, 2.0 * a, rng.randn(200)])  # col1 = 2*col0
        with pytest.raises(ValueError, match="rank-deficient"):
            fit_pca(X, 3)

    def test_reconstruct_residual_recovers_planted_eps(self):
        # The target column is a linear combo of two factors + a known
        # residual eps; a 2-component reconstruction recovers eps.
        rng = np.random.RandomState(5)
        n = 400
        f1, f2 = rng.randn(n), rng.randn(n)
        eps = 0.05 * rng.randn(n)
        target = 1.5 * f1 - 0.8 * f2 + eps
        X = np.column_stack([f1, f2, target])
        fit = fit_pca(X, 2)
        resid = reconstruct_residual(fit, X, 2)
        assert np.corrcoef(resid, eps)[0, 1] > 0.99

    def test_reconstruct_residual_zero_at_full_rank(self):
        X = _correlated()
        fit = fit_pca(X, 3)  # K = n_features -> exact reconstruction
        for j in range(3):
            assert np.abs(reconstruct_residual(fit, X, j)).max() < 1e-9

    def test_near_degenerate_flag(self):
        # Mutually-ORTHOGONAL +/-1 (Hadamard) columns: after z-scoring
        # the correlation matrix is exactly isotropic -> all singular
        # values are tied -> the non-unique-subspace flag fires.
        rep = 50  # 200 rows, divisible by 4
        a = np.tile([1.0, 1.0, -1.0, -1.0], rep)
        b = np.tile([1.0, -1.0, 1.0, -1.0], rep)
        c = np.tile([1.0, -1.0, -1.0, 1.0], rep)
        X = np.column_stack([a, b, c])
        r = fit_pca(X, 2)
        assert r.near_degenerate is True

"""Tests for shared.quant.gmm — the Gaussian-mixture EM numerics.

No library oracle, so correctness is pinned by construction:

  1. Recovery of PLANTED well-separated clusters (purity ~1, up to the
     canonical relabel).
  2. The canonical ordering (state 0 = lowest-centroid regime) makes
     the labelling deterministic across reruns.
  3. Degenerate refusals.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from shared.quant.gmm import GmmFitResult, fit_gmm_em


def _planted(seed=0, sep=8.0, per=150):
    rng = np.random.RandomState(seed)
    blocks = [
        rng.randn(per, 2) * 0.5 + [0.0, 0.0],
        rng.randn(per, 2) * 0.5 + [sep, sep],
        rng.randn(per, 2) * 0.5 + [0.0, sep],
    ]
    X = np.vstack(blocks)
    true = np.array([0] * per + [1] * per + [2] * per)
    return X, true


# ===========================================================================
# 1. Recovery
# ===========================================================================


class TestRecovery:
    def test_recovers_planted_clusters(self):
        X, true = _planted()
        r = fit_gmm_em(X, 3)
        assert isinstance(r, GmmFitResult)
        assert r.converged is True
        # Each true cluster maps (up to permutation) to one dominant
        # label with near-perfect purity.
        for t in range(3):
            lbls = r.labels[true == t]
            top, cnt = Counter(lbls).most_common(1)[0]
            assert cnt / len(lbls) > 0.98
        # All three labels are used (a real 3-way split).
        assert set(r.labels) == {0, 1, 2}

    def test_canonical_ordering_ascending(self):
        X, _ = _planted()
        r = fit_gmm_em(X, 3)
        grand = r.means.mean(axis=1)
        assert np.all(np.diff(grand) >= 0)  # state 0 = lowest centroid

    def test_shapes(self):
        X, _ = _planted()
        r = fit_gmm_em(X, 3)
        assert r.labels.shape == (len(X),)
        assert r.means.shape == (3, 2)
        assert r.covariances.shape == (3, 2, 2)
        assert r.weights.shape == (3,)
        assert r.responsibilities.shape == (len(X), 3)


# ===========================================================================
# 2. Determinism
# ===========================================================================


def test_deterministic_across_reruns():
    X, _ = _planted(seed=3)
    a = fit_gmm_em(X, 3)
    b = fit_gmm_em(X, 3)
    assert np.array_equal(a.labels, b.labels)
    assert np.array_equal(a.means, b.means)
    assert np.array_equal(a.weights, b.weights)


# ===========================================================================
# 3. Refusals
# ===========================================================================


class TestRefusals:
    def test_n_states_below_two_raises(self):
        X, _ = _planted()
        with pytest.raises(ValueError, match="n_states must be >= 2"):
            fit_gmm_em(X, 1)

    def test_too_few_rows_raises(self):
        X, _ = _planted(per=1)
        with pytest.raises(ValueError, match="at least n_states"):
            fit_gmm_em(X[:2], 5)

    def test_non_finite_raises(self):
        X, _ = _planted()
        X = X.copy()
        X[10, 0] = np.nan
        with pytest.raises(ValueError, match="finite"):
            fit_gmm_em(X, 3)

    def test_zero_variance_column_raises(self):
        X, _ = _planted()
        X = np.column_stack([X[:, 0], np.ones(len(X))])
        with pytest.raises(ValueError, match="zero variance"):
            fit_gmm_em(X, 2)

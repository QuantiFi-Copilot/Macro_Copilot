"""Tests for shared.quant.hmm — the Gaussian-HMM Baum-Welch numerics.

No library oracle, so correctness is pinned by construction:

  1. Recovery of a KNOWN sticky Gaussian HMM — the states AND a
     high-persistence transition matrix (the property that distinguishes
     the HMM from the iid GMM).
  2. The canonical ordering + determinism (Viterbi decode, reproducible).
  3. Degenerate refusals.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pytest

from shared.quant.hmm import HmmFitResult, fit_hmm_em


def _sticky_A(k=3, diag=0.95):
    a = np.full((k, k), (1.0 - diag) / (k - 1))
    np.fill_diagonal(a, diag)
    return a


def _sim_hmm(n=2000, sep=6.0, diag=0.95, seed=0):
    rng = np.random.RandomState(seed)
    means = np.array([[0.0, 0.0], [sep, sep], [0.0, sep]])
    a = _sticky_A(3, diag)
    states = np.zeros(n, dtype=int)
    for t in range(1, n):
        states[t] = rng.choice(3, p=a[states[t - 1]])
    X = np.array([means[states[t]] + rng.randn(2) * 0.4 for t in range(n)])
    return X, states


# ===========================================================================
# 1. Recovery
# ===========================================================================


class TestRecovery:
    def test_recovers_states_and_sticky_transitions(self):
        X, true = _sim_hmm(diag=0.95)
        r = fit_hmm_em(X, 3)
        assert isinstance(r, HmmFitResult)
        assert r.converged is True
        assert r.decode_method == "viterbi"
        # Each true state maps (up to the canonical relabel) to one
        # dominant label at high purity.
        for t in range(3):
            lbl = r.labels[true == t]
            _top, cnt = Counter(lbl).most_common(1)[0]
            assert cnt / len(lbl) > 0.95
        # The recovered transition matrix is sticky (high diagonal) —
        # the HMM property the GMM lacks.
        for s in range(3):
            assert r.transition_matrix[s, s] > 0.85
        np.testing.assert_allclose(r.transition_matrix.sum(axis=1), 1.0)
        assert r.initial_dist.sum() == pytest.approx(1.0)

    def test_temporal_smoothing_matches_true_switches(self):
        # The Viterbi path recovers the true number of regime changes
        # (a noisy iid clustering would over-switch).
        X, true = _sim_hmm(diag=0.97)
        r = fit_hmm_em(X, 3)
        true_switches = int((np.diff(true) != 0).sum())
        recovered = int((np.diff(r.labels) != 0).sum())
        assert abs(recovered - true_switches) <= max(5, true_switches // 5)

    def test_canonical_ordering_ascending(self):
        X, _ = _sim_hmm()
        r = fit_hmm_em(X, 3)
        grand = r.means.mean(axis=1)
        assert np.all(np.diff(grand) >= 0)

    def test_shapes(self):
        X, _ = _sim_hmm()
        r = fit_hmm_em(X, 3)
        assert r.labels.shape == (len(X),)
        assert r.means.shape == (3, 2)
        assert r.transition_matrix.shape == (3, 3)
        assert r.initial_dist.shape == (3,)


# ===========================================================================
# 2. Determinism
# ===========================================================================


def test_deterministic_across_reruns():
    X, _ = _sim_hmm(seed=3)
    a = fit_hmm_em(X, 3)
    b = fit_hmm_em(X, 3)
    assert np.array_equal(a.labels, b.labels)
    assert np.array_equal(a.transition_matrix, b.transition_matrix)
    assert np.array_equal(a.means, b.means)


# ===========================================================================
# 3. Refusals
# ===========================================================================


class TestRefusals:
    def test_n_states_below_two_raises(self):
        X, _ = _sim_hmm()
        with pytest.raises(ValueError, match="n_states must be >= 2"):
            fit_hmm_em(X, 1)

    def test_too_few_rows_raises(self):
        X, _ = _sim_hmm(n=2)
        with pytest.raises(ValueError, match="at least n_states"):
            fit_hmm_em(X[:2], 5)

    def test_non_finite_raises(self):
        X, _ = _sim_hmm()
        X = X.copy()
        X[10, 0] = np.nan
        with pytest.raises(ValueError, match="finite"):
            fit_hmm_em(X, 3)

    def test_zero_variance_column_raises(self):
        X, _ = _sim_hmm()
        X = np.column_stack([X[:, 0], np.ones(len(X))])
        with pytest.raises(ValueError, match="zero variance"):
            fit_hmm_em(X, 2)

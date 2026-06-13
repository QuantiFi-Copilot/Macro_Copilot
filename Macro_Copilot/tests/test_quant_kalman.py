"""Tests for shared.quant.kalman — the DLM forward filter numerics.

No library oracle (the house style); correctness is pinned by
construction:

  1. A KNOWN time-varying coefficient (a step in beta) is RECOVERED by
     the filtered path.
  2. THE causality property: with the noise scale R held fixed, the
     recursion is prefix-only — truncating the data after t leaves the
     filtered prefix byte-identical (the filter never peeks ahead).
  3. The stiff-SNR limit converges to the static OLS fit; a larger SNR
     produces a more variable path.
  4. Refusals (non-finite, under-identified, rank-deficient, degenerate
     residual, bad SNR / R).
"""

from __future__ import annotations

import numpy as np
import pytest

from shared.quant.kalman import DlmFilterResult, dlm_filter


def _tvp_step(seed=0, n=600, b_early=0.5, b_late=2.0, noise=0.05):
    """A single-regressor series whose beta STEPS at the midpoint."""
    rng = np.random.RandomState(seed)
    x = rng.randn(n)
    beta = np.where(np.arange(n) < n // 2, b_early, b_late)
    y = beta * x + noise * rng.randn(n)
    X = np.column_stack([x, np.ones(n)])  # regressor + intercept
    return y, X


# ===========================================================================
# 1. Recovery
# ===========================================================================


class TestRecovery:
    def test_recovers_known_time_varying_beta(self):
        y, X = _tvp_step()
        r = dlm_filter(y, X, signal_to_noise_ratio=0.05)
        assert isinstance(r, DlmFilterResult)
        b = r.filtered_states[:, 0]
        # Early regime ~0.5, late regime ~2.0 (away from the step + warmup).
        assert float(np.nanmean(b[100:280])) == pytest.approx(0.5, abs=0.2)
        assert float(np.nanmean(b[420:580])) == pytest.approx(2.0, abs=0.2)

    def test_anchor_is_full_sample_ols(self):
        y, X = _tvp_step()
        r = dlm_filter(y, X, 0.05)
        beta_ols, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        np.testing.assert_allclose(r.static_ols_coef, beta_ols)

    def test_stiff_snr_converges_to_static_ols(self):
        # delta -> 0 ⇒ Q -> 0 ⇒ the coefficients freeze ⇒ recursive OLS,
        # whose tail converges to the full-sample OLS fit.
        y, X = _tvp_step()
        r = dlm_filter(y, X, signal_to_noise_ratio=1e-9)
        np.testing.assert_allclose(
            r.filtered_states[-1], r.static_ols_coef, atol=1e-3,
        )

    def test_larger_snr_is_more_variable(self):
        y, X = _tvp_step()
        stiff = dlm_filter(y, X, 1e-3).filtered_states[:, 0]
        loose = dlm_filter(y, X, 1e-1).filtered_states[:, 0]
        assert np.nanstd(loose) > np.nanstd(stiff)


# ===========================================================================
# 2. Causality + determinism
# ===========================================================================


class TestCausalityAndDeterminism:
    def test_recursion_is_causal_given_fixed_R(self):
        # THE load-bearing property: hold the noise scale R fixed, then the
        # filter is prefix-only — re-running on the truncated prefix yields
        # a byte-identical filtered path.  (The full-sample auto-calibrated
        # R is the disclosed exception; here we isolate the recursion.)
        y, X = _tvp_step()
        full = dlm_filter(y, X, 0.05)
        R = full.observation_variance
        t0 = 400
        trunc = dlm_filter(
            y[:t0 + 1], X[:t0 + 1], 0.05, observation_variance=R,
        )
        np.testing.assert_array_equal(
            full.filtered_states[:t0 + 1], trunc.filtered_states,
            # the warmup head is NaN in both; equal_nan keeps them equal
        )

    def test_warmup_head_is_nan(self):
        y, X = _tvp_step()
        r = dlm_filter(y, X, 0.05)
        assert r.n_warmup == X.shape[1] - 1  # n_params - 1
        assert np.isnan(r.filtered_states[: r.n_warmup]).all()
        assert np.isfinite(r.filtered_states[r.n_warmup:]).all()

    def test_deterministic(self):
        y, X = _tvp_step(seed=3)
        a = dlm_filter(y, X, 0.05)
        b = dlm_filter(y, X, 0.05)
        np.testing.assert_array_equal(a.filtered_states, b.filtered_states)
        assert a.observation_variance == b.observation_variance


# ===========================================================================
# 3. Refusals
# ===========================================================================


class TestRefusals:
    def test_non_finite_raises(self):
        y, X = _tvp_step(n=100)
        y = y.copy()
        y[10] = np.nan
        with pytest.raises(ValueError, match="fully finite"):
            dlm_filter(y, X, 0.05)

    def test_under_identified_raises(self):
        # n_obs <= n_params
        X = np.array([[1.0, 1.0], [2.0, 1.0]])
        y = np.array([1.0, 2.0])
        with pytest.raises(ValueError, match="more observations"):
            dlm_filter(y, X, 0.05)

    def test_rank_deficient_design_raises(self):
        rng = np.random.RandomState(1)
        a = rng.randn(200)
        X = np.column_stack([a, 2.0 * a, np.ones(200)])  # col1 = 2*col0
        y = a + rng.randn(200)
        with pytest.raises(ValueError, match="rank-deficient"):
            dlm_filter(y, X, 0.05)

    def test_zero_residual_fit_raises(self):
        # A constant target against a constant column ⇒ the OLS mean fits
        # EXACTLY (5.0 - 5.0 == 0.0 in float), so the residual variance is
        # exactly zero — there is no noise scale to calibrate against.
        X = np.ones((50, 1))
        y = np.full(50, 5.0)
        with pytest.raises(ValueError, match="residual variance is zero"):
            dlm_filter(y, X, 0.05)

    def test_bad_snr_raises(self):
        y, X = _tvp_step(n=100)
        with pytest.raises(ValueError, match="signal_to_noise_ratio"):
            dlm_filter(y, X, 0.0)
        with pytest.raises(ValueError, match="signal_to_noise_ratio"):
            dlm_filter(y, X, -1.0)

    def test_explicit_observation_variance_must_be_positive(self):
        y, X = _tvp_step(n=100)
        with pytest.raises(ValueError, match="observation_variance"):
            dlm_filter(y, X, 0.05, observation_variance=0.0)

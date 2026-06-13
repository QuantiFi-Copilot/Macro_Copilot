"""Tests for shared.quant.garch — the GARCH(1,1) MLE numerics.

No library oracle, so correctness is pinned by construction:

  1. Recovery of a KNOWN simulated GARCH(1,1) (omega/alpha/beta within
     tolerance; the fitted conditional-vol path tracks the latent sigma
     at high correlation — the strong anchor).
  2. White noise (no ARCH) -> low persistence (no false clustering).
  3. Determinism (a fixed optimiser start -> byte-identical rerun).
  4. Degenerate refusals.
"""

from __future__ import annotations

import numpy as np
import pytest

from shared.quant.garch import GarchFitResult, fit_garch_11


def _sim_garch(n, omega, alpha, beta, seed):
    rng = np.random.RandomState(seed)
    z = rng.randn(n)
    eps = np.zeros(n)
    s2 = np.zeros(n)
    s2[0] = omega / (1 - alpha - beta)
    eps[0] = np.sqrt(s2[0]) * z[0]
    for t in range(1, n):
        s2[t] = omega + alpha * eps[t - 1] ** 2 + beta * s2[t - 1]
        eps[t] = np.sqrt(s2[t]) * z[t]
    return eps, np.sqrt(s2)


# ===========================================================================
# 1. Recovery of a KNOWN GARCH(1,1)
# ===========================================================================


class TestRecovery:
    def test_recovers_params_and_tracks_vol(self):
        eps, latent_vol = _sim_garch(4000, 0.05, 0.08, 0.90, seed=0)
        r = fit_garch_11(eps)
        assert isinstance(r, GarchFitResult)
        assert r.converged is True
        # MLE has finite-sample bias; persistence + beta recover well.
        assert r.persistence == pytest.approx(0.98, abs=0.06)
        assert r.beta == pytest.approx(0.90, abs=0.08)
        assert r.alpha > 0.0
        # The strong anchor: the fitted path tracks the latent sigma.
        corr = float(np.corrcoef(r.conditional_vol, latent_vol)[0, 1])
        assert corr > 0.95
        assert len(r.conditional_vol) == 4000

    def test_white_noise_low_persistence(self):
        wn = np.random.RandomState(1).randn(2000)
        r = fit_garch_11(wn)
        assert r.converged is True
        assert r.persistence < 0.9  # no strong vol clustering


# ===========================================================================
# 2. Determinism + refusals
# ===========================================================================


class TestPurityAndRefusals:
    def test_deterministic(self):
        eps, _ = _sim_garch(1500, 0.1, 0.1, 0.8, seed=3)
        a = fit_garch_11(eps)
        b = fit_garch_11(eps)
        assert a.omega == b.omega and a.alpha == b.alpha and a.beta == b.beta
        assert np.array_equal(a.conditional_vol, b.conditional_vol)

    def test_too_few_raises(self):
        with pytest.raises(ValueError, match="at least 12"):
            fit_garch_11(np.arange(5.0))

    def test_non_finite_raises(self):
        x = np.random.RandomState(2).randn(50)
        x[10] = np.nan
        with pytest.raises(ValueError, match="finite"):
            fit_garch_11(x)

    def test_constant_raises(self):
        with pytest.raises(ValueError, match="zero variance"):
            fit_garch_11(np.full(50, 3.0))

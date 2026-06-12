"""Tests for shared.quant.ou — the OU / AR(1) mean-reversion fit.

No library oracle, so correctness is pinned by construction:

  1. A hand-worked NOISELESS AR(1) anchor (exact phi/half-life/mu/R2).
  2. Recovery of a KNOWN simulated OU (phi=0.9 -> half-life 6.58).
  3. The not-mean-reverting cases (ramp/explosive/oscillatory ->
     half_life None) + the random-walk weak-fit disclosure (low R2).
  4. Degenerate refusals + determinism.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from shared.quant.ou import OuFitResult, fit_ou_ar1


# ===========================================================================
# 1. Hand-worked noiseless AR(1) anchor
# ===========================================================================


class TestHandAnchor:
    """x = [0, 1, 1.5, 1.75, 1.875] is x_t = 1 + 0.5*x_{t-1} exactly:
    a noiseless AR(1) with phi=0.5 toward mu=2.  So beta = phi-1 = -0.5,
    half_life = -ln2/ln(0.5) = 1, mu = -alpha/beta = -1/-0.5 = 2, R2 = 1.
    """

    X = np.array([0.0, 1.0, 1.5, 1.75, 1.875])

    def test_exact_fit(self):
        r = fit_ou_ar1(self.X)
        assert isinstance(r, OuFitResult)
        assert r.phi == pytest.approx(0.5, rel=1e-12)
        assert r.beta == pytest.approx(-0.5, rel=1e-12)
        assert r.half_life == pytest.approx(1.0, rel=1e-12)
        assert r.mu == pytest.approx(2.0, rel=1e-12)
        assert r.theta == pytest.approx(-math.log(0.5), rel=1e-12)
        assert r.r_squared == pytest.approx(1.0, rel=1e-9)
        assert r.is_mean_reverting is True


# ===========================================================================
# 2. Recovery of a KNOWN simulated OU
# ===========================================================================


def test_recovers_known_ou():
    """phi=0.9 -> half_life = -ln2/ln(0.9) = 6.579; recover it at large
    N (the OLS AR(1) bias is negligible far from the unit root)."""
    rng = np.random.RandomState(0)
    n, phi, mu = 10000, 0.9, 5.0
    x = np.zeros(n)
    x[0] = mu
    for i in range(1, n):
        x[i] = mu * (1 - phi) + phi * x[i - 1] + rng.randn()
    r = fit_ou_ar1(x)
    assert r.is_mean_reverting is True
    assert r.phi == pytest.approx(0.9, abs=0.02)
    assert r.half_life == pytest.approx(6.579, rel=0.12)
    assert r.mu == pytest.approx(5.0, abs=0.5)


# ===========================================================================
# 3. Not-mean-reverting + weak-fit disclosure
# ===========================================================================


class TestMeanReversionClassification:
    def test_trending_ramp_not_mean_reverting(self):
        ramp = np.arange(200, dtype=float)
        r = fit_ou_ar1(ramp)
        assert r.is_mean_reverting is False
        assert r.half_life is None
        assert r.mu is None

    def test_explosive_not_mean_reverting(self):
        rng = np.random.RandomState(1)
        n = 200
        x = np.zeros(n)
        x[0] = 1.0
        for i in range(1, n):
            x[i] = 1.05 * x[i - 1] + 0.01 * rng.randn()  # phi > 1
        r = fit_ou_ar1(x)
        assert r.is_mean_reverting is False
        assert r.half_life is None

    def test_oscillatory_not_mean_reverting(self):
        rng = np.random.RandomState(2)
        n = 500
        x = np.zeros(n)
        for i in range(1, n):
            x[i] = -0.5 * x[i - 1] + rng.randn()  # phi = -0.5 < 0
        r = fit_ou_ar1(x)
        assert r.is_mean_reverting is False  # phi <= 0
        assert r.half_life is None

    def test_random_walk_weak_fit_disclosed(self):
        """A random walk's change is unexplained by its level -> R2 ~ 0
        (the honesty disclosure), whatever the borderline phi sign."""
        rw = np.cumsum(np.random.RandomState(3).randn(3000))
        r = fit_ou_ar1(rw)
        assert r.r_squared < 0.05


# ===========================================================================
# 4. Degenerate refusals + determinism
# ===========================================================================


class TestRefusalsAndPurity:
    def test_too_few_raises(self):
        with pytest.raises(ValueError, match="at least 4"):
            fit_ou_ar1(np.array([1.0, 2.0, 3.0]))

    def test_non_finite_raises(self):
        x = np.cumsum(np.random.RandomState(4).randn(50))
        x[10] = np.nan
        with pytest.raises(ValueError, match="finite"):
            fit_ou_ar1(x)

    def test_constant_raises(self):
        with pytest.raises(ValueError, match="zero variance"):
            fit_ou_ar1(np.full(50, 3.0))

    def test_deterministic(self):
        rng = np.random.RandomState(5)
        x = np.zeros(500)
        for i in range(1, 500):
            x[i] = 0.7 * x[i - 1] + rng.randn()
        assert fit_ou_ar1(x) == fit_ou_ar1(x)  # frozen dataclass equality

"""Tests for shared.quant.ou — the OU / AR(1) mean-reversion fit.

No library oracle, so correctness is pinned by construction:

  1. A hand-worked NOISELESS AR(1) anchor (exact phi/half-life/mu/R2).
  2. Recovery of a KNOWN simulated OU (phi=0.9 -> half-life 6.58).
  3. The not-mean-reverting cases (ramp/explosive/oscillatory ->
     half_life None) + the random-walk weak-fit disclosure (low R2).
  4. Degenerate refusals + determinism.
  5. The unit-root SIGNIFICANCE gate (M2 honesty fix): the
     point-estimate sign is NOT a verdict — a pure random walk reads
     point_estimate_mean_reverting=True ~96% of the time but its unit
     root is NOT rejected; a genuine OU rejects it.
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
        assert r.point_estimate_mean_reverting is True
        # A noiseless AR(1) is overwhelmingly significant: SS_res ~ 0,
        # so SE(beta) ~ 0, df_tstat is huge-negative, the unit root is
        # rejected.
        assert r.unit_root_rejected is True
        assert r.df_tstat < 0


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
    assert r.point_estimate_mean_reverting is True
    assert r.phi == pytest.approx(0.9, abs=0.02)
    assert r.half_life == pytest.approx(6.579, rel=0.12)
    assert r.mu == pytest.approx(5.0, abs=0.5)
    # A genuine OU strongly rejects the unit-root null (this is the
    # whole point of the M2 gate — the half-life IS significant here).
    assert r.unit_root_rejected is True
    assert r.unit_root_pvalue is not None and r.unit_root_pvalue < 1e-6


# ===========================================================================
# 3. Not-mean-reverting + weak-fit disclosure
# ===========================================================================


class TestMeanReversionClassification:
    def test_trending_ramp_not_mean_reverting(self):
        ramp = np.arange(200, dtype=float)
        r = fit_ou_ar1(ramp)
        assert r.point_estimate_mean_reverting is False
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
        assert r.point_estimate_mean_reverting is False
        assert r.half_life is None

    def test_oscillatory_not_mean_reverting(self):
        rng = np.random.RandomState(2)
        n = 500
        x = np.zeros(n)
        for i in range(1, n):
            x[i] = -0.5 * x[i - 1] + rng.randn()  # phi = -0.5 < 0
        r = fit_ou_ar1(x)
        assert r.point_estimate_mean_reverting is False  # phi <= 0
        assert r.half_life is None

    def test_random_walk_weak_fit_disclosed(self):
        """A random walk's change is unexplained by its level -> R2 ~ 0
        (the honesty disclosure), whatever the borderline phi sign."""
        rw = np.cumsum(np.random.RandomState(3).randn(3000))
        r = fit_ou_ar1(rw)
        assert r.r_squared < 0.05


# ===========================================================================
# 3b. Unit-root SIGNIFICANCE gate — the M2 over-claim fix
# ===========================================================================


class TestUnitRootSignificance:
    """The M2 honesty fix: a point-estimate β<0 sign is NOT a verdict.
    A pure random walk reads point_estimate_mean_reverting=True ~96% of
    the time, but its unit root must NOT be rejected (so the operator
    refuses).  A genuine OU rejects it.  The significance stats
    (beta_std_err, df_tstat, unit_root_pvalue) are always populated on a
    well-conditioned fit."""

    def test_significance_fields_populated(self):
        rng = np.random.RandomState(0)
        n, phi, mu = 5000, 0.85, 2.0
        x = np.zeros(n)
        x[0] = mu
        for i in range(1, n):
            x[i] = mu * (1 - phi) + phi * x[i - 1] + rng.randn()
        r = fit_ou_ar1(x)
        assert r.beta_std_err is not None and r.beta_std_err > 0
        assert r.df_tstat is not None
        assert r.unit_root_pvalue is not None
        assert 0.0 <= r.unit_root_pvalue <= 1.0
        # df_tstat == beta / SE(beta) by construction.
        assert r.df_tstat == pytest.approx(r.beta / r.beta_std_err, rel=1e-9)

    def test_random_walk_unit_root_not_rejected_majority(self):
        """The headline M2 reproduction at the core: across many pure
        random walks the unit root is NOT rejected the vast majority of
        the time, even though the point-estimate sign flips to
        'mean-reverting' ~96% of the time.  Pre-fix nothing caught
        this; the significance gate is what makes the operator refuse."""
        rng = np.random.default_rng(2024)
        n_paths = 100
        point_mr = 0
        rejected = 0
        for _ in range(n_paths):
            rw = np.cumsum(rng.standard_normal(252))
            r = fit_ou_ar1(rw)
            if r.point_estimate_mean_reverting:
                point_mr += 1
            if r.unit_root_rejected:
                rejected += 1
        # The point-estimate sign is spuriously "mean-reverting" most of
        # the time (the bug the name now discloses)...
        assert point_mr >= 80, point_mr
        # ...but the unit root is rejected only near the 5% Type-I rate,
        # so the half-life is NOT confidently emitted on a random walk.
        assert rejected <= 15, rejected

    def test_genuine_ou_unit_root_rejected(self):
        rng = np.random.RandomState(11)
        n, phi, mu = 4000, 0.8, 0.0
        x = np.zeros(n)
        for i in range(1, n):
            x[i] = phi * x[i - 1] + rng.randn()
        r = fit_ou_ar1(x)
        assert r.unit_root_rejected is True
        assert r.unit_root_pvalue < 0.01

    def test_significance_is_deterministic(self):
        rng = np.random.RandomState(99)
        x = np.cumsum(rng.randn(400))
        a = fit_ou_ar1(x)
        b = fit_ou_ar1(x)
        assert a.df_tstat == b.df_tstat
        assert a.unit_root_pvalue == b.unit_root_pvalue
        assert a.unit_root_rejected == b.unit_root_rejected


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

"""Tests for shared.quant.variance_ratio — the Lo–MacKinlay numerics.

This is the FIRST test of the shared/quant package.  There is no
library to parity against (no arch/statsmodels VR test in the env), so
correctness is pinned three independent ways:

  1. A fully HAND-WORKED tiny example (X=[0,1,0,2,1], q=2) where every
     term is computed by hand → exact vr=4/27, z(M1)=-46/27, m=3.
  2. The DEFINING PROPERTIES (random-walk VR≈1; mean-reverting VR<1;
     trending VR>1) — independent of the formula's internals.
  3. The MUTUAL-CONSISTENCY identity: under homoskedasticity the
     robust (M2) variance reduces to the homoskedastic (M1) one, so
     the two z-statistics agree on iid data.
"""

from __future__ import annotations

import numpy as np
import pytest

from shared.quant.variance_ratio import (
    VarianceRatioResult,
    variance_ratio_test,
)


# ===========================================================================
# 1. Hand-worked exact anchor (no library — pure independent arithmetic)
# ===========================================================================


class TestHandWorkedExample:
    """X = [0, 1, 0, 2, 1], q = 2.

    d = [1, -1, 2, -1], mu = (1-0)/4 = 0.25,
    a = (d-mu)^2 = [0.5625, 1.5625, 3.0625, 1.5625], sum_a = 6.75,
    var_1 = 6.75/4 = 1.6875.
    qdiff = X[2:]-X[:-2] = [0, 1, 1]; m = 2*(4-2+1)*(1-2/4) = 3;
    var_q = sum(([0,1,1]-0.5)^2)/3 = 0.75/3 = 0.25.
    vr = 0.25/1.6875 = 4/27.
    M1: phi1 = 2*(2*2-1)*(2-1)/(3*2*4) = 6/24 = 0.25;
        z1 = (4/27 - 1)/sqrt(0.25) = (-23/27)/0.5 = -46/27.
    M2: delta_1 = sum(a[1:]*a[:-1]) / sum_a^2
               = (1.5625*0.5625 + 3.0625*1.5625 + 1.5625*3.0625)/45.5625
               = 10.44921875/45.5625; z2 = (4/27-1)/sqrt(delta_1).
    """

    X = np.array([0.0, 1.0, 0.0, 2.0, 1.0])

    def test_variance_ratio_and_m_exact(self):
        r = variance_ratio_test(self.X, q=2, robust=False)
        assert isinstance(r, VarianceRatioResult)
        assert r.vr == pytest.approx(4.0 / 27.0, rel=1e-12)
        assert r.m == pytest.approx(3.0, rel=1e-12)
        assert r.n_diffs == 4
        assert r.var_1 == pytest.approx(1.6875, rel=1e-12)
        assert r.var_q == pytest.approx(0.25, rel=1e-12)

    def test_homoskedastic_z_exact(self):
        r = variance_ratio_test(self.X, q=2, robust=False)
        assert r.z_statistic == pytest.approx(-46.0 / 27.0, rel=1e-12)
        assert r.robust is False

    def test_robust_z_exact(self):
        r = variance_ratio_test(self.X, q=2, robust=True)
        # delta_1 = 10.44921875 / 45.5625; z2 = (4/27 - 1)/sqrt(delta_1)
        delta_1 = 10.44921875 / 45.5625
        expected = (4.0 / 27.0 - 1.0) / np.sqrt(delta_1)
        assert r.z_statistic == pytest.approx(expected, rel=1e-12)
        assert r.z_statistic == pytest.approx(-1.778795, rel=1e-5)
        # VR(q) is identical to the homoskedastic call — only z differs.
        assert r.vr == pytest.approx(4.0 / 27.0, rel=1e-12)


# ===========================================================================
# 2. The m bias-correction formula, exactly
# ===========================================================================


@pytest.mark.parametrize("n_obs,q", [(5, 2), (50, 4), (200, 8), (100, 16)])
def test_m_matches_closed_form(n_obs, q):
    x = np.cumsum(np.random.RandomState(n_obs + q).randn(n_obs))
    r = variance_ratio_test(x, q=q)
    nd = n_obs - 1
    assert r.m == pytest.approx(q * (nd - q + 1) * (1.0 - q / nd))


# ===========================================================================
# 3. Defining properties (formula-independent)
# ===========================================================================


class TestDefiningProperties:
    def test_random_walk_vr_near_one(self):
        """Mean VR across many random walks ≈ 1 (the null)."""
        vrs = []
        for seed in range(60):
            x = np.cumsum(np.random.RandomState(seed).randn(500))
            vrs.append(variance_ratio_test(x, q=4).vr)
        assert np.mean(vrs) == pytest.approx(1.0, abs=0.05)

    def test_mean_reverting_vr_below_one(self):
        """A stationary AR(1) level (phi=0.5) is mean-reverting → VR<1,
        z significantly negative."""
        rng = np.random.RandomState(11)
        n = 3000
        x = np.zeros(n)
        for i in range(1, n):
            x[i] = 0.5 * x[i - 1] + rng.randn()
        r = variance_ratio_test(x, q=4)
        assert r.vr < 0.9
        assert r.z_statistic < -2.0
        assert r.p_value < 0.05

    def test_trending_vr_above_one(self):
        """Positively autocorrelated INCREMENTS (cumsum of AR(1), +phi)
        → long-horizon variance grows faster → VR>1, z positive."""
        rng = np.random.RandomState(13)
        n = 3000
        e = np.zeros(n)
        for i in range(1, n):
            e[i] = 0.6 * e[i - 1] + rng.randn()
        x = np.cumsum(e)
        r = variance_ratio_test(x, q=4)
        assert r.vr > 1.1
        assert r.z_statistic > 2.0
        assert r.p_value < 0.05


# ===========================================================================
# 4. Mutual-consistency identity + robust/homosk relationship
# ===========================================================================


class TestVarianceEstimators:
    def test_vr_identical_across_robust_flag(self):
        x = np.cumsum(np.random.RandomState(21).randn(400))
        r_t = variance_ratio_test(x, q=6, robust=True)
        r_f = variance_ratio_test(x, q=6, robust=False)
        assert r_t.vr == pytest.approx(r_f.vr, rel=1e-12)
        # Different standardisation → different z (in general).
        assert r_t.z_statistic != pytest.approx(r_f.z_statistic, rel=1e-9)

    def test_robust_reduces_to_homoskedastic_on_iid(self):
        """Under homoskedasticity theta → phi1, so the two z-statistics
        agree on a long iid (random-walk-increment) series."""
        x = np.cumsum(np.random.RandomState(23).randn(8000))
        r_t = variance_ratio_test(x, q=4, robust=True)
        r_f = variance_ratio_test(x, q=4, robust=False)
        assert r_t.z_statistic == pytest.approx(r_f.z_statistic, rel=0.12)


# ===========================================================================
# 5. Purity + refusals
# ===========================================================================


class TestPurityAndRefusals:
    def test_deterministic(self):
        x = np.cumsum(np.random.RandomState(31).randn(120))
        a = variance_ratio_test(x, q=5)
        b = variance_ratio_test(x, q=5)
        assert a == b  # frozen dataclass equality

    def test_q_below_two_raises(self):
        x = np.cumsum(np.random.RandomState(1).randn(50))
        with pytest.raises(ValueError, match="q must be >= 2"):
            variance_ratio_test(x, q=1)

    def test_too_few_observations_raises(self):
        with pytest.raises(ValueError, match="q \\+ 2"):
            variance_ratio_test(np.array([0.0, 1.0, 2.0]), q=4)

    def test_non_finite_input_raises(self):
        x = np.cumsum(np.random.RandomState(2).randn(50))
        x[10] = np.nan
        with pytest.raises(ValueError, match="finite"):
            variance_ratio_test(x, q=4)

    def test_constant_series_raises(self):
        with pytest.raises(ValueError, match="zero variance"):
            variance_ratio_test(np.full(50, 3.0), q=4)

    def test_perfect_ramp_raises(self):
        # Constant first differences → zero-variance increments.
        with pytest.raises(ValueError, match="zero variance"):
            variance_ratio_test(np.arange(50, dtype=float), q=4)

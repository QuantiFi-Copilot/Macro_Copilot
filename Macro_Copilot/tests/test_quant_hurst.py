"""Tests for shared.quant.hurst — the rescaled-range numerics.

The second shared/quant test.  No library oracle (no hurst/nolds in
the env), so correctness is pinned independently:

  1. Deterministic scale-math anchors (the locked scale set; a
     hand-worked single-block R/S = 3/sqrt(2); the Anis-Lloyd E[R/S]).
  2. Defining properties (iid -> H≈0.5; random-walk level -> persistent
     H>0.5; anti-persistent -> H<0.5) — formula-independent.
  3. The correction does its job: on iid the corrected H is closer to
     0.5 than the upward-biased uncorrected slope.
"""

from __future__ import annotations

import numpy as np
import pytest

from shared.quant.hurst import (
    HurstResult,
    _anis_lloyd_expected,
    _rs_for_scale,
    _scale_set,
    hurst_rs,
)


# ===========================================================================
# 1. Deterministic scale-math anchors (no library — pure arithmetic)
# ===========================================================================


class TestScaleMath:
    def test_scale_set_is_locked_geometric(self):
        # The design-locked geometric window set for N=128.
        assert _scale_set(128) == [
            8, 9, 10, 11, 12, 14, 15, 17, 19, 21,
            24, 27, 30, 33, 37, 41, 46, 51, 57, 64,
        ]
        # Monotone, within [8, N//2], a pure function of N.
        s512 = _scale_set(512)
        assert s512[0] == 8 and s512[-1] == 256
        assert s512 == sorted(set(s512))

    def test_scale_set_empty_when_too_short(self):
        assert _scale_set(15) == []  # max_window 7 < min_window 8

    def test_rs_single_block_hand_worked(self):
        # block=[1,3,2,5,4]: mean=3, dev=[-2,0,-1,2,1],
        # cumsum=[-2,-2,-3,-1,0], R=0-(-3)=3, S=sqrt(10/5)=sqrt(2),
        # R/S = 3/sqrt(2).
        rs = _rs_for_scale(np.array([1.0, 3.0, 2.0, 5.0, 4.0]), 5)
        assert rs == pytest.approx(3.0 / np.sqrt(2.0), rel=1e-12)

    def test_rs_none_on_constant_block(self):
        assert _rs_for_scale(np.full(10, 4.0), 5) is None

    def test_anis_lloyd_expected_values(self):
        # Probed reference values (Anis-Lloyd 1976 E[R/S]).
        assert _anis_lloyd_expected(8) == pytest.approx(2.4606, rel=1e-3)
        assert _anis_lloyd_expected(16) == pytest.approx(3.9094, rel=1e-3)
        assert _anis_lloyd_expected(64) == pytest.approx(8.8955, rel=1e-3)


# ===========================================================================
# 2. Defining properties (formula-independent)
# ===========================================================================


def _avg_h(make, n, seeds, attr="h"):
    vals = []
    for s in range(seeds):
        r = hurst_rs(make(np.random.RandomState(s), n))
        vals.append(getattr(r, attr))
    return float(np.mean(vals))


class TestDefiningProperties:
    def test_iid_noise_near_half(self):
        """White noise has no long memory → H ≈ 0.5 (corrected)."""
        h = _avg_h(lambda rng, n: rng.randn(n), 2000, seeds=40)
        assert h == pytest.approx(0.5, abs=0.06)

    def test_random_walk_level_persistent(self):
        """A random-walk LEVEL (cumsum of iid) is strongly persistent
        → H well above 0.5."""
        h = _avg_h(lambda rng, n: np.cumsum(rng.randn(n)), 2000, seeds=20)
        assert h > 0.75

    def test_anti_persistent_below_half(self):
        """A negative-AR(1) series is anti-persistent → H < 0.5."""
        def neg_ar1(rng, n):
            x = np.zeros(n)
            for i in range(1, n):
                x[i] = -0.6 * x[i - 1] + rng.randn()
            return x
        h = _avg_h(neg_ar1, 2000, seeds=20)
        assert h < 0.45

    def test_fit_quality_high_on_clean_power_law(self):
        r = hurst_rs(np.cumsum(np.random.RandomState(1).randn(2000)))
        assert r.r_squared > 0.95


# ===========================================================================
# 3. The Anis-Lloyd correction removes the small-sample bias
# ===========================================================================


class TestCorrection:
    def test_correction_recentres_iid_toward_half(self):
        """On iid data the uncorrected slope is biased HIGH (~0.56); the
        corrected H sits closer to 0.5."""
        h_corr = _avg_h(lambda rng, n: rng.randn(n), 256, seeds=60, attr="h")
        h_raw = _avg_h(
            lambda rng, n: rng.randn(n), 256, seeds=60, attr="h_uncorrected",
        )
        assert h_raw > 0.5  # the known upward bias
        assert abs(h_corr - 0.5) < abs(h_raw - 0.5)  # correction helps

    def test_correction_label(self):
        r = hurst_rs(np.cumsum(np.random.RandomState(2).randn(512)))
        assert r.correction == "anis_lloyd"


# ===========================================================================
# 4. Purity + refusals
# ===========================================================================


class TestPurityAndRefusals:
    def test_deterministic(self):
        x = np.cumsum(np.random.RandomState(31).randn(512))
        assert hurst_rs(x) == hurst_rs(x)  # frozen dataclass equality

    def test_result_shape(self):
        r = hurst_rs(np.cumsum(np.random.RandomState(3).randn(300)))
        assert isinstance(r, HurstResult)
        assert r.n_scales == len(r.scales) == len(r.log_rs)
        assert r.n_obs == 300

    def test_too_few_observations_raises(self):
        with pytest.raises(ValueError, match="at least 128"):
            hurst_rs(np.cumsum(np.random.RandomState(1).randn(100)))

    def test_non_finite_input_raises(self):
        x = np.cumsum(np.random.RandomState(2).randn(200))
        x[10] = np.nan
        with pytest.raises(ValueError, match="finite"):
            hurst_rs(x)

    def test_constant_series_raises(self):
        # Every block has S=0 → no usable scales.
        with pytest.raises(ValueError, match="usable scales"):
            hurst_rs(np.full(200, 3.0))

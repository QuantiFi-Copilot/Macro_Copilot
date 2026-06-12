"""Tests for shared.quant.changepoint — binary-segmentation numerics.

No library oracle, so correctness is pinned by construction:

  1. A hand-worked gain anchor ([0,0,0,10,10,10] -> split at 3, gain
     150 — the textbook SSE arithmetic).
  2. Planted recovery of KNOWN mean shifts.
  3. The honest partial/empty path (fewer breaks than requested when
     positive-gain splits are exhausted).
  4. Degenerate refusals + determinism.
"""

from __future__ import annotations

import numpy as np
import pytest

from shared.quant.changepoint import ChangepointResult, binary_segmentation


# ===========================================================================
# 1. Hand-worked gain anchor
# ===========================================================================


def test_hand_anchor():
    # [0,0,0,10,10,10]: SSE(whole)=6*25=150; split at 3 -> SSE 0 + 0;
    # gain = 150.  Means (0, 10).
    r = binary_segmentation(
        np.array([0.0, 0, 0, 10, 10, 10]), n_changepoints=1, min_size=2,
    )
    assert isinstance(r, ChangepointResult)
    assert r.breakpoints == (3,)
    assert r.gains[0] == pytest.approx(150.0, rel=1e-12)
    assert r.segment_means == (0.0, 10.0)
    assert r.n_changepoints == 1
    assert r.cost_model == "l2_mean_shift"


# ===========================================================================
# 2. Planted recovery
# ===========================================================================


def test_recovers_planted_breaks():
    rng = np.random.RandomState(0)
    x = np.concatenate([
        rng.randn(100) * 0.3,
        5.0 + rng.randn(150) * 0.3,
        -3.0 + rng.randn(150) * 0.3,
    ])
    r = binary_segmentation(x, n_changepoints=2)
    assert r.n_changepoints == 2
    # Both true breaks (100, 250) recovered within a few indices.
    assert abs(r.breakpoints[0] - 100) <= 3
    assert abs(r.breakpoints[1] - 250) <= 3
    np.testing.assert_allclose(r.segment_means, [0.0, 5.0, -3.0], atol=0.2)


# ===========================================================================
# 3. Partial / empty (honest fewer-than-requested)
# ===========================================================================


def test_fewer_breaks_than_requested_when_exhausted():
    # One real break; the constant halves admit no further positive gain.
    x = np.array([0.0] * 50 + [5.0] * 50)
    r = binary_segmentation(x, n_changepoints=3)
    assert r.n_changepoints == 1
    assert r.breakpoints == (50,)


@pytest.mark.parametrize("offset", [1e6, 1e9, 1e12, 1e15])
def test_large_mean_offset_is_numerically_stable(offset):
    """The SSE is computed on a MEAN-CENTERED series, so a large
    constant offset (an un-normalised level / notional series) does NOT
    trigger catastrophic cancellation: the obvious +10 step at index 50
    is still found, never silently lost to roundoff."""
    x = np.array([offset] * 50 + [offset + 10.0] * 50)
    r = binary_segmentation(x, n_changepoints=1)
    assert r.breakpoints == (50,)
    assert r.gains[0] == pytest.approx(2500.0, rel=1e-6)  # 100*(5)^2


# ===========================================================================
# 4. Degenerate refusals + determinism
# ===========================================================================


class TestRefusalsAndPurity:
    def test_n_changepoints_below_one_raises(self):
        with pytest.raises(ValueError, match="n_changepoints must be >= 1"):
            binary_segmentation(np.arange(50.0), n_changepoints=0)

    def test_min_size_below_two_raises(self):
        with pytest.raises(ValueError, match="min_size must be >= 2"):
            binary_segmentation(np.arange(50.0), n_changepoints=1, min_size=1)

    def test_too_few_raises(self):
        with pytest.raises(ValueError, match="at least"):
            binary_segmentation(np.arange(5.0), n_changepoints=2, min_size=2)

    def test_non_finite_raises(self):
        x = np.arange(50.0)
        x[10] = np.nan
        with pytest.raises(ValueError, match="finite"):
            binary_segmentation(x, n_changepoints=1)

    def test_constant_raises(self):
        with pytest.raises(ValueError, match="zero variance"):
            binary_segmentation(np.full(50, 3.0), n_changepoints=1)

    def test_deterministic(self):
        x = np.concatenate([np.zeros(40), np.ones(40) * 3])
        assert (
            binary_segmentation(x, n_changepoints=1)
            == binary_segmentation(x, n_changepoints=1)
        )

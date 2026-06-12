"""shared.quant.variance_ratio — Lo–MacKinlay variance-ratio test.

The FIRST real numeric in ``shared/quant/`` (the package was a bare
skeleton before this).  Pure, finance-blind, deterministic per the
package boundary rules (no domain imports, no IO, no clock, no
randomness) — the L3 ``variance_ratio`` operator is the only production
consumer; it wraps this behind a typed-artifact signature.

THE TEST (Lo & MacKinlay 1988, *Review of Financial Studies*).  For a
level series with one-period increments ``d_k = X_k − X_{k−1}``, the
variance ratio at horizon ``q`` compares the variance of OVERLAPPING
q-period differences to ``q`` times the one-period variance::

    VR(q) = var_q / var_1

Under the random-walk null VR(q) = 1.  VR > 1 ⇒ positive serial
correlation (trending / momentum); VR < 1 ⇒ negative serial
correlation (mean reversion).  The standardised statistic ``z`` (the
quantity with the asymptotic N(0,1) null) is what the operator emits;
VR(q) itself is the interpretable effect size.

DESIGN LOCKS (the canonical Lo–MacKinlay estimator — recorded by the
operator in lineage):

  - OVERLAPPING q-period differences with the unbiasedness
    bias-correction ``m = q(T−q+1)(1 − q/T)`` (``T`` = number of
    one-period increments).  Non-overlapping is a distinct, weaker
    estimator — a declared planned extension, never a silent knob.
  - The variance of ``VR(q) − 1`` is estimated two ways, selected by
    ``robust``:
      * ``robust=False`` — the HOMOSKEDASTIC asymptotic variance
        ``phi1 = 2(2q−1)(q−1) / (3q·T)`` (the M1 statistic).
      * ``robust=True``  — the HETEROSKEDASTICITY-CONSISTENT estimator
        ``theta = Σ_{j=1}^{q−1} [2(q−j)/q]² · δ_j`` with
        ``δ_j = (Σ_k a_k a_{k−j}) / (Σ_k a_k)²`` and
        ``a_k = (d_k − μ̂)²`` (the M2 statistic).
    The two are mutually consistent: under homoskedasticity ``theta``
    reduces analytically to ``phi1`` (because
    ``Σ_{j=1}^{q−1}[2(q−j)/q]² = 2(q−1)(2q−1)/(3q)`` and the iid
    expectation of ``δ_j`` is ``≈ 1/T``).  This identity is asserted in
    the unit tests.

The function refuses degenerate input with a plain ``ValueError`` (the
operator pre-validates with its typed ``VarianceRatioError`` so this is
belt-and-suspenders): ``q < 2``; fewer than ``q + 2`` observations (the
overlapping estimator is undefined — ``m`` collapses); a non-finite
input; or zero-variance increments (a constant series or a perfect
linear ramp — the denominator ``var_1`` is zero).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class VarianceRatioResult:
    """The result of :func:`variance_ratio_test`.

    Attributes
    ----------
    vr :
        The variance ratio VR(q) (1 ⇒ random walk; > 1 trending;
        < 1 mean-reverting).  The interpretable effect size.
    z_statistic :
        The standardised test statistic (M2 when ``robust`` else M1),
        asymptotically N(0, 1) under the random-walk null.  This is
        what the operator emits as its ``ScalarMetric`` value.
    p_value :
        Two-sided normal p-value, ``2 · Φ(−|z|)``.
    q :
        The aggregation horizon used.
    robust :
        Whether the heteroskedasticity-consistent variance (M2) was
        used.
    m :
        The Lo–MacKinlay overlapping-window bias-correction
        ``q(T−q+1)(1 − q/T)``.
    var_1, var_q :
        The one-period and (bias-corrected, overlapping) q-period
        variance estimates.
    n_diffs :
        The number of one-period increments, ``T = n_obs − 1``.
    """

    vr: float
    z_statistic: float
    p_value: float
    q: int
    robust: bool
    m: float
    var_1: float
    var_q: float
    n_diffs: int


def variance_ratio_test(
    values: np.ndarray,
    q: int,
    *,
    robust: bool = True,
) -> VarianceRatioResult:
    """Lo–MacKinlay variance-ratio test of a 1-D level series.

    Parameters
    ----------
    values :
        A 1-D array of FINITE floats (the level series; the caller must
        drop NaNs first — this is a pure numeric and does not impute).
    q :
        The aggregation horizon, ``q >= 2``.
    robust :
        ``True`` (default) uses the heteroskedasticity-consistent (M2)
        variance estimator; ``False`` the homoskedastic (M1) one.
        VR(q) is identical either way — only the standardisation of
        ``z`` differs.

    Returns
    -------
    VarianceRatioResult

    Raises
    ------
    ValueError
        ``q < 2``; fewer than ``q + 2`` observations; a non-finite
        input; or zero-variance increments (constant series / perfect
        ramp).
    """
    arr = np.asarray(values, dtype=float).ravel()
    n_obs = int(arr.size)
    if q < 2:
        raise ValueError(
            f"variance_ratio_test: q must be >= 2 (got {q})."
        )
    if n_obs < q + 2:
        raise ValueError(
            f"variance_ratio_test: needs at least q + 2 = {q + 2} "
            f"observations for horizon q={q} (got {n_obs}); the "
            "overlapping estimator is otherwise undefined."
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "variance_ratio_test: all values must be finite (drop NaNs "
            "upstream)."
        )

    # One-period increments and their centred squares.
    nd = n_obs - 1                       # T — number of increments
    d = np.diff(arr)                     # length nd
    mu = float(d.mean())                 # == (arr[-1] - arr[0]) / nd
    a = (d - mu) ** 2                    # centred squared increments
    sum_a = float(a.sum())
    if sum_a == 0.0:
        raise ValueError(
            "variance_ratio_test: the increments have zero variance (a "
            "constant series or a perfect linear ramp) — the variance "
            "ratio is undefined."
        )
    var_1 = sum_a / nd

    # Overlapping q-period differences, bias-corrected (design-locked).
    qdiff = arr[q:] - arr[:-q]          # length nd - q + 1
    m = q * (nd - q + 1) * (1.0 - q / nd)
    var_q = float(np.sum((qdiff - q * mu) ** 2)) / m
    vr = var_q / var_1

    # Asymptotic variance of (VR − 1): heteroskedasticity-consistent
    # (M2) or homoskedastic (M1).
    if robust:
        denom = sum_a * sum_a
        variance = 0.0
        for j in range(1, q):
            # δ_j = (Σ_k a_k a_{k−j}) / (Σ_k a_k)²  — carries the 1/T
            # scaling, so z = (VR−1)/sqrt(Σ_j w_j² δ_j) is O(1).
            delta_j = float(np.sum(a[j:] * a[:-j])) / denom
            weight = 2.0 * (q - j) / q
            variance += (weight * weight) * delta_j
    else:
        variance = 2.0 * (2.0 * q - 1.0) * (q - 1.0) / (3.0 * q * nd)

    z = (vr - 1.0) / np.sqrt(variance)
    p_value = 2.0 * float(norm.sf(abs(z)))

    return VarianceRatioResult(
        vr=float(vr),
        z_statistic=float(z),
        p_value=p_value,
        q=int(q),
        robust=bool(robust),
        m=float(m),
        var_1=float(var_1),
        var_q=float(var_q),
        n_diffs=int(nd),
    )


__all__ = ["variance_ratio_test", "VarianceRatioResult"]

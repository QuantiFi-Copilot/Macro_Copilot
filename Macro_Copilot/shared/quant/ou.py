"""shared.quant.ou — Ornstein–Uhlenbeck / mean-reversion fit.

The THIRD numeric in ``shared/quant/`` (after ``variance_ratio.py`` and
``hurst.py``).  Pure, finance-blind, deterministic per the package
boundary rules — the L3 ``fit_ou`` operator is the only production
consumer.

THE FIT (the discrete-time OU / AR(1) MLE-equivalent, design-locked).
Regress the one-period change on the lagged level::

    Δx_t = α + β · x_{t-1} + ε_t          (the estimating equation)

which is the discretisation of ``dx = θ(μ − x) dt + σ dW``.  The
mean-reversion parameters follow (with φ ≡ 1 + β the AR(1) coefficient
— this reconciles byte-for-concept with the rates ``half_life``
primitive, which reports β):

    φ          = 1 + β                     (AR(1) coefficient)
    θ          = −ln(φ)                    (mean-reversion speed / step)
    half_life  = ln(2) / θ = −ln(2)/ln(φ)  (steps to close half the gap)
    μ          = −α / β = α/(1−φ)          (the equilibrium level)
    σ_eq       = σ_ε / sqrt(1 − φ²)        (the stationary std)

MEAN-REVERSION REQUIRES ``0 < φ < 1`` (equivalently ``−1 < β < 0``):

  - φ ≥ 1 (β ≥ 0) — a unit root / trending series: NO finite half-life.
  - φ ≤ 0 (β ≤ −1) — oscillatory / explosive: no monotone reversion.

The fit's R² is the R² of the Δx regression — how much of the CHANGE
the lagged level explains (the mean-reversion signal strength); the R²
of a level-on-lag regression would be near 1 by autocorrelation and is
NOT a fit gauge.

ESTIMATION DESIGN-LOCK (the ``fit_ou`` operator stamps it in lineage):
AR(1) OLS via SVD ``lstsq`` (``estimation_method='ar1_ols'``).
Exact-MLE and Kalman-OU are declared planned extensions.

The function returns ``half_life=None`` (and the dependent μ/σ_eq=None)
when the series is NOT mean-reverting or the fit is ill-conditioned
(``φ`` within ``_MIN_DECAY`` of a unit root); the operator REFUSES on
that (a ``ScalarMetric`` has no None channel — the honesty rule).  It
raises ``ValueError`` only on degenerate input the math cannot handle
(fewer than 3 increments, non-finite, zero variance).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np


# The φ→1 ill-conditioning floor (mirrors the half_life primitive's
# min_abs_beta_for_half_life): a |β| below this is indistinguishable
# from a unit root and the half-life formula blows up.
_MIN_DECAY = 1e-6

# Minimum increments for a non-degenerate AR(1) regression (the
# operator enforces the higher family floor of 12).
_MIN_INCREMENTS = 3


@dataclass(frozen=True)
class OuFitResult:
    """The result of :func:`fit_ou_ar1`.

    Attributes
    ----------
    alpha, beta :
        The Δx-regression intercept and slope (β = φ − 1).
    phi :
        The AR(1) coefficient (1 + β).
    theta :
        The mean-reversion speed −ln(φ) per step (``None`` when not
        mean-reverting).
    half_life :
        Steps to close half the gap to equilibrium, ``ln(2)/θ``
        (``None`` when not mean-reverting or ill-conditioned — the
        operator refuses then).
    mu :
        The equilibrium level −α/β (``None`` when not mean-reverting).
    sigma_eps :
        The residual (innovation) standard deviation.
    sigma_eq :
        The stationary standard deviation σ_ε/√(1−φ²) (``None`` when
        not mean-reverting).
    r_squared :
        R² of the Δx regression — the mean-reversion signal strength.
    is_mean_reverting :
        Whether ``0 < φ < 1`` AND the fit is well-conditioned.
    n_increments :
        The number of one-period increments used (n_obs − 1).
    """

    alpha: float
    beta: float
    phi: float
    theta: Optional[float]
    half_life: Optional[float]
    mu: Optional[float]
    sigma_eps: float
    sigma_eq: Optional[float]
    r_squared: Optional[float]
    is_mean_reverting: bool
    n_increments: int


def fit_ou_ar1(values: np.ndarray) -> OuFitResult:
    """Fit a discrete OU / AR(1) mean-reversion model to a 1-D series.

    Parameters
    ----------
    values :
        A 1-D array of FINITE floats (the level series; the caller
        drops NaNs first — this is a pure numeric and does not impute).

    Returns
    -------
    OuFitResult
        ``half_life`` (and μ/θ/σ_eq) are ``None`` when the series is not
        mean-reverting (``φ ∉ (0, 1)``) or the fit is ill-conditioned.

    Raises
    ------
    ValueError
        Fewer than 3 increments; a non-finite input; or zero variance.
    """
    arr = np.asarray(values, dtype=float).ravel()
    if arr.size < _MIN_INCREMENTS + 1:
        raise ValueError(
            f"fit_ou_ar1: needs at least {_MIN_INCREMENTS + 1} "
            f"observations (got {arr.size})."
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "fit_ou_ar1: all values must be finite (drop NaNs upstream)."
        )
    if float(np.var(arr)) == 0.0:
        raise ValueError(
            "fit_ou_ar1: the input has zero variance — the AR(1) "
            "regression is degenerate on a constant series."
        )

    # Δx_t = α + β·x_{t-1} + ε  (SVD lstsq — design-locked ar1_ols).
    x_lag = arr[:-1]
    dx = np.diff(arr)
    n_inc = int(dx.size)
    design = np.column_stack([np.ones(n_inc, dtype=float), x_lag])
    coefs, _res, _rank, _sv = np.linalg.lstsq(design, dx, rcond=None)
    alpha = float(coefs[0])
    beta = float(coefs[1])
    phi = 1.0 + beta

    resid = dx - design @ coefs
    ss_res = float(np.sum(resid ** 2))
    dx_mean = float(np.mean(dx))
    ss_tot = float(np.sum((dx - dx_mean) ** 2))
    r_squared: Optional[float] = (
        1.0 - ss_res / ss_tot if ss_tot > 0.0 else None
    )
    # Residual std (innovation σ_ε), unbiased by the 2 fitted params.
    sigma_eps = (
        math.sqrt(ss_res / (n_inc - 2)) if n_inc > 2 else math.sqrt(
            ss_res / n_inc
        )
    )

    # Mean-reversion requires 0 < φ < 1 AND a well-conditioned decay.
    is_mean_reverting = (
        (beta < 0.0)
        and (0.0 < phi < 1.0)
        and (abs(beta) >= _MIN_DECAY)
    )

    theta: Optional[float] = None
    half_life: Optional[float] = None
    mu: Optional[float] = None
    sigma_eq: Optional[float] = None
    if is_mean_reverting:
        ln_phi = math.log(phi)  # negative
        theta = -ln_phi
        half_life = -math.log(2.0) / ln_phi
        mu = -alpha / beta
        sigma_eq = sigma_eps / math.sqrt(1.0 - phi * phi)
        if not math.isfinite(half_life):  # belt-and-suspenders
            is_mean_reverting = False
            theta = half_life = mu = sigma_eq = None

    return OuFitResult(
        alpha=alpha,
        beta=beta,
        phi=phi,
        theta=theta,
        half_life=half_life,
        mu=mu,
        sigma_eps=float(sigma_eps),
        sigma_eq=sigma_eq,
        r_squared=r_squared,
        is_mean_reverting=is_mean_reverting,
        n_increments=n_inc,
    )


__all__ = ["fit_ou_ar1", "OuFitResult"]

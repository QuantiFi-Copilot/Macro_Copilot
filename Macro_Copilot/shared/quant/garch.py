"""shared.quant.garch — GARCH(1,1) conditional-volatility fit.

The FIFTH numeric in ``shared/quant/`` (after variance_ratio, hurst,
ou, changepoint).  Pure, finance-blind, deterministic per the package
boundary rules — the L3 ``fit_garch`` operator is the only production
consumer.

DESCRIPTIVE, NOT A FORECAST (the scope boundary the operator enforces):
this fits the IN-SAMPLE conditional-volatility PATH σ_t over the
OBSERVED history — how volatility clustered.  It does NOT project
σ_{T+1}; the operator emits σ on the input's own dates only.

THE MODEL (Bollerslev 1986; Engle 1982 — GARCH(1,1), design-locked):

    x_t = μ + ε_t,   ε_t = σ_t · z_t,
    σ²_t = ω + α · ε²_{t-1} + β · σ²_{t-1}

with μ the sample mean (the locked constant-mean model — ESTIMATED,
not chosen), ω > 0, α ≥ 0, β ≥ 0 and the STATIONARITY constraint
α + β < 1.  Persistence = α + β (near 1 ⇒ vol shocks decay slowly).
The parameters are estimated by maximising the Gaussian log-likelihood
via ``scipy.optimize.minimize`` (SLSQP with the explicit constraints)
from a FIXED deterministic start (no random restarts → rerun-identical).

The function RETURNS the fit (including ``converged`` and the
boundary-degeneracy is visible via ``persistence``); the operator
REFUSES on non-convergence or α + β ≥ 1 (a ScalarMetric/Series path
from a failed optimiser would be garbage).  It raises ``ValueError``
only on degenerate input the math cannot start on (fewer than the
floor, non-finite, zero variance).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize


_MIN_OBS = 12

# Fixed deterministic optimiser start (textbook-typical interior point)
# and the stationarity tolerance — no randomness anywhere (OPR14).
_ALPHA0 = 0.05
_BETA0 = 0.90
_STATIONARITY_TOL = 1e-6
_LOG_2PI = math.log(2.0 * math.pi)


@dataclass(frozen=True)
class GarchFitResult:
    """The result of :func:`fit_garch_11`.

    Attributes
    ----------
    omega, alpha, beta :
        The GARCH(1,1) parameters.
    persistence :
        ``alpha + beta`` (near 1 ⇒ slowly-decaying vol clustering).
    mu :
        The locked constant mean (the sample mean of the input).
    loglik :
        The maximised Gaussian log-likelihood.
    converged :
        Whether the optimiser reported success.
    conditional_vol :
        The fitted in-sample σ_t path (same length as the input).
    n_obs :
        The number of observations.
    """

    omega: float
    alpha: float
    beta: float
    persistence: float
    mu: float
    loglik: float
    converged: bool
    conditional_vol: np.ndarray
    n_obs: int


def _conditional_var(
    omega: float, alpha: float, beta: float,
    eps2: np.ndarray, var0: float,
) -> np.ndarray:
    """The σ²_t recursion (sequential — cannot be vectorised)."""
    n = eps2.size
    sigma2 = np.empty(n, dtype=float)
    sigma2[0] = var0
    for t in range(1, n):
        sigma2[t] = omega + alpha * eps2[t - 1] + beta * sigma2[t - 1]
    return sigma2


def fit_garch_11(values: np.ndarray) -> GarchFitResult:
    """Fit a GARCH(1,1) conditional-volatility model to a 1-D series.

    Parameters
    ----------
    values :
        A 1-D array of FINITE floats (the caller drops NaNs first); the
        series is modelled AS GIVEN (difference a level to returns
        upstream — this does not difference internally).

    Returns
    -------
    GarchFitResult

    Raises
    ------
    ValueError
        Fewer than 12 observations; a non-finite input; or zero
        variance.
    """
    arr = np.asarray(values, dtype=float).ravel()
    n = int(arr.size)
    if n < _MIN_OBS:
        raise ValueError(
            f"fit_garch_11: needs at least {_MIN_OBS} observations "
            f"(got {n})."
        )
    if not np.all(np.isfinite(arr)):
        raise ValueError(
            "fit_garch_11: all values must be finite (drop NaNs "
            "upstream)."
        )
    var0 = float(np.var(arr))
    if var0 == 0.0:
        raise ValueError(
            "fit_garch_11: the input has zero variance — the GARCH "
            "likelihood is degenerate on a constant series."
        )

    mu = float(arr.mean())
    eps = arr - mu
    eps2 = eps * eps

    def _nll(params: np.ndarray) -> float:
        omega, alpha, beta = float(params[0]), float(params[1]), float(params[2])
        sigma2 = _conditional_var(omega, alpha, beta, eps2, var0)
        if not np.all(np.isfinite(sigma2)) or np.any(sigma2 <= 0.0):
            return 1e12
        return 0.5 * float(
            np.sum(_LOG_2PI + np.log(sigma2) + eps2 / sigma2)
        )

    # Fixed deterministic start: ω₀ from the unconditional-variance
    # identity ω = (1 − α − β)·var, with the start α/β.
    x0 = np.array(
        [(1.0 - _ALPHA0 - _BETA0) * var0, _ALPHA0, _BETA0], dtype=float,
    )
    bounds = [(1e-12, None), (0.0, 1.0), (0.0, 1.0)]
    # Stationarity: α + β ≤ 1 − tol (SLSQP inequality, fun >= 0).
    constraints = [{
        "type": "ineq",
        "fun": lambda p: (1.0 - _STATIONARITY_TOL) - p[1] - p[2],
    }]
    res = minimize(
        _nll, x0, method="SLSQP", bounds=bounds, constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-9},
    )
    omega, alpha, beta = float(res.x[0]), float(res.x[1]), float(res.x[2])
    converged = bool(res.success)

    sigma2 = _conditional_var(omega, alpha, beta, eps2, var0)
    conditional_vol = np.sqrt(np.maximum(sigma2, 0.0))

    return GarchFitResult(
        omega=omega,
        alpha=alpha,
        beta=beta,
        persistence=alpha + beta,
        mu=mu,
        loglik=float(-res.fun),
        converged=converged,
        conditional_vol=conditional_vol,
        n_obs=n,
    )


__all__ = ["fit_garch_11", "GarchFitResult"]

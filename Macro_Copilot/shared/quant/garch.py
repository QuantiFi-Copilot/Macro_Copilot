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
via ``scipy.optimize.minimize`` (SLSQP with the explicit constraints).

SCALE-INVARIANT, NUMERICALLY ROBUST OPTIMISATION (the C1.1 fix):
the residuals are STANDARDISED to unit variance before the optimiser
runs.  α and β are scale-invariant under this transform; ω scales by
the residual variance and is mapped back afterwards.  This is what
makes the optimisation well-conditioned regardless of the input scale.
Without it, on small-variance data (e.g. real daily yield CHANGES,
σ ≈ 0.03–0.10 in percent) the Gaussian NLL gradient is tiny and SLSQP
terminates AT ITERATION 1 — leaving x pinned at the start and yet
reporting ``success=True`` (a silently-wrong MLE at the 0.95-prior
persistence).  We additionally run a FIXED deterministic grid of
interior starts (no randomness → rerun-identical) and TREAT A FIT THAT
ENDS UNMOVED FROM ITS START AS NON-CONVERGED — a start-pinned solution
is never reported as a good optimum.

The function RETURNS the fit (including ``converged``, ``boundary_stuck``
— every start ended pinned at its start — and the boundary-degeneracy
visible via ``persistence``); the operator REFUSES on non-convergence
or α + β ≥ 1 (a ScalarMetric/Series path from a failed optimiser would
be garbage).  It raises ``ValueError`` only on degenerate input the
math cannot start on (fewer than the floor, non-finite, zero variance).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize


_MIN_OBS = 12

# Fixed deterministic optimiser start grid (textbook-typical interior
# points) and the stationarity tolerance — no randomness anywhere
# (OPR14): every start (α, β) is hard-coded, so reruns are byte-
# identical.  The first entry is the historical default; the rest are
# spread across the (α, β) simplex so a basin the default misses is
# still found.  Each start must satisfy α + β < 1 − _STATIONARITY_TOL.
_ALPHA0 = 0.05
_BETA0 = 0.90
_START_GRID: tuple[tuple[float, float], ...] = (
    (0.05, 0.90),   # the historical default (kept first for continuity)
    (0.10, 0.80),
    (0.03, 0.95),
    (0.20, 0.70),
    (0.15, 0.60),
    (0.01, 0.50),
)
_STATIONARITY_TOL = 1e-6
# A start counts as "unmoved" (hence non-converged for that start) when
# the optimiser leaves x within this absolute distance of x0.  Compared
# in the STANDARDISED space, so it is scale-free.
_UNMOVED_ATOL = 1e-8
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
        Whether the optimiser reported a genuine optimum — ``True`` only
        when at least one start both reported SUCCESS *and* MOVED off its
        start point (a start-pinned solution is never "converged").
    boundary_stuck :
        ``True`` when EVERY start ended pinned at its start point (x
        unmoved) — the diagnostic that distinguishes a degenerate /
        flat-likelihood input from a real non-convergence.  Always
        ``False`` when ``converged`` is ``True``.
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
    boundary_stuck: bool
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

    # ------------------------------------------------------------------
    # STANDARDISE to unit variance.  α, β are scale-invariant under this
    # transform; ω scales by ``scale²`` and is mapped back below.  This
    # is the load-bearing fix: on small-variance inputs the un-scaled
    # Gaussian NLL gradient is so small that SLSQP stops at iteration 1
    # (x pinned at the start, yet success=True) — a silently-wrong MLE.
    # On the standardised residuals the NLL is O(1)-scaled so ``ftol`` is
    # meaningful regardless of the input scale.
    # ------------------------------------------------------------------
    scale = math.sqrt(var0)            # var0 > 0 guaranteed above
    eps_s = eps / scale
    eps2_s = eps_s * eps_s
    var0_s = 1.0                       # variance of the standardised eps

    def _nll(params: np.ndarray) -> float:
        omega, alpha, beta = float(params[0]), float(params[1]), float(params[2])
        sigma2 = _conditional_var(omega, alpha, beta, eps2_s, var0_s)
        if not np.all(np.isfinite(sigma2)) or np.any(sigma2 <= 0.0):
            return 1e12
        return 0.5 * float(
            np.sum(_LOG_2PI + np.log(sigma2) + eps2_s / sigma2)
        )

    bounds = [(1e-12, None), (0.0, 1.0), (0.0, 1.0)]
    # Stationarity: α + β ≤ 1 − tol (SLSQP inequality, fun >= 0).
    constraints = [{
        "type": "ineq",
        "fun": lambda p: (1.0 - _STATIONARITY_TOL) - p[1] - p[2],
    }]

    # ------------------------------------------------------------------
    # DETERMINISTIC MULTI-START.  Run the fixed start grid; a start whose
    # solution ends UNMOVED from its start point is treated as NON-
    # converged for that start (reject x0-unmoved-as-success — the SLSQP
    # iteration-1 stall).  Keep only genuinely-moved successful optima;
    # pick the lowest NLL (deterministic tie-break by grid order).
    # ------------------------------------------------------------------
    best_x: np.ndarray | None = None
    best_fun = np.inf
    any_started = False
    for alpha0, beta0 in _START_GRID:
        # ω₀ in the STANDARDISED space, from the unconditional-variance
        # identity ω = (1 − α − β)·var_s (floored strictly positive).
        x0 = np.array(
            [max((1.0 - alpha0 - beta0) * var0_s, 1e-6), alpha0, beta0],
            dtype=float,
        )
        res = minimize(
            _nll, x0, method="SLSQP", bounds=bounds, constraints=constraints,
            options={"maxiter": 1000, "ftol": 1e-12},
        )
        any_started = True
        moved = not np.allclose(res.x, x0, rtol=0.0, atol=_UNMOVED_ATOL)
        if bool(res.success) and moved and float(res.fun) < best_fun:
            best_fun = float(res.fun)
            best_x = np.asarray(res.x, dtype=float).copy()

    converged = best_x is not None
    boundary_stuck = any_started and best_x is None

    if best_x is None:
        # No start produced a genuine optimum — report the (start-pinned)
        # default fit but flag it NON-converged so the caller refuses.
        omega_s, alpha, beta = (
            (1.0 - _ALPHA0 - _BETA0) * var0_s, _ALPHA0, _BETA0,
        )
        nll_s = _nll(np.array([omega_s, alpha, beta], dtype=float))
    else:
        omega_s, alpha, beta = (
            float(best_x[0]), float(best_x[1]), float(best_x[2]),
        )
        nll_s = best_fun

    # ------------------------------------------------------------------
    # DE-STANDARDISE: ω maps back by ``scale²``; α, β unchanged.  Compute
    # the σ_t path on the ORIGINAL scale (σ_s · scale) and the original-
    # scale log-likelihood (the standardised NLL differs from the
    # original-scale NLL by exactly ``n · log(scale)``).
    # ------------------------------------------------------------------
    omega = omega_s * var0  # var0 == scale²
    sigma2_s = _conditional_var(omega_s, alpha, beta, eps2_s, var0_s)
    conditional_vol = np.sqrt(np.maximum(sigma2_s, 0.0)) * scale
    loglik = float(-nll_s - n * math.log(scale))

    return GarchFitResult(
        omega=omega,
        alpha=alpha,
        beta=beta,
        persistence=alpha + beta,
        mu=mu,
        loglik=loglik,
        converged=converged,
        boundary_stuck=boundary_stuck,
        conditional_vol=conditional_vol,
        n_obs=n,
    )


__all__ = ["fit_garch_11", "GarchFitResult"]

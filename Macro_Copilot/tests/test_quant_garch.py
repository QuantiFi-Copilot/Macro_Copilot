"""Tests for shared.quant.garch — the GARCH(1,1) MLE numerics.

No library oracle, so correctness is pinned by construction:

  1. Recovery of a KNOWN simulated GARCH(1,1) (omega/alpha/beta within
     tolerance; the fitted conditional-vol path tracks the latent sigma
     at high correlation — the strong anchor).
  2. White noise (no ARCH) -> low persistence (no false clustering).
  3. Determinism (a fixed optimiser start -> byte-identical rerun).
  4. Degenerate refusals.
  5. CONVERGENCE ROBUSTNESS (the C1.1 regression): on large clean
     small-variance series (n=5000, n=10000, multi-seed) — the regime
     that pre-C1.1 left SLSQP stuck at the 0.95-prior start with
     success=True — the fit must MOVE off the start, report
     converged=True, and reach the true MLE (loglik within a small
     tolerance of an independent best-of-multistart oracle).  These are
     REAL reproductions: the oracle is an independently-coded multistart
     in original parameter space, not a re-call of fit_garch_11.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from scipy.optimize import minimize

from shared.quant.garch import GarchFitResult, fit_garch_11

_LOG_2PI = math.log(2.0 * math.pi)


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


def _independent_best_loglik(arr, n_starts=24, oracle_seed=20240611):
    """An INDEPENDENT best-of-multistart oracle for the GARCH(1,1)
    log-likelihood — coded straight in the ORIGINAL parameter space (NOT
    a call to fit_garch_11), with a wide RANDOM start grid + tight
    tolerances.  Used to prove the production fit reaches the true MLE
    (close the −8437.50-vs-−8514.38 gap the C1.1 finding flagged)."""
    arr = np.asarray(arr, dtype=float).ravel()
    mu = float(arr.mean())
    eps = arr - mu
    eps2 = eps * eps
    var0 = float(np.var(arr))

    def cond_var(o, a, b):
        s2 = np.empty(eps2.size, dtype=float)
        s2[0] = var0
        for t in range(1, eps2.size):
            s2[t] = o + a * eps2[t - 1] + b * s2[t - 1]
        return s2

    def nll(p):
        o, a, b = float(p[0]), float(p[1]), float(p[2])
        s2 = cond_var(o, a, b)
        if not np.all(np.isfinite(s2)) or np.any(s2 <= 0.0):
            return 1e12
        return 0.5 * float(np.sum(_LOG_2PI + np.log(s2) + eps2 / s2))

    bounds = [(1e-12, None), (0.0, 1.0), (0.0, 1.0)]
    cons = [{"type": "ineq", "fun": lambda p: (1.0 - 1e-6) - p[1] - p[2]}]
    rng = np.random.RandomState(oracle_seed)
    starts = [(0.05, 0.90)]
    for _ in range(n_starts - 1):
        a0 = rng.uniform(0.01, 0.30)
        b0 = rng.uniform(0.30, 1.0 - a0 - 0.01)
        starts.append((a0, b0))
    best = np.inf
    for a0, b0 in starts:
        x0 = np.array([max((1.0 - a0 - b0) * var0, 1e-12), a0, b0])
        r = minimize(nll, x0, method="SLSQP", bounds=bounds,
                     constraints=cons, options={"maxiter": 2000, "ftol": 1e-14})
        if r.success and r.fun < best:
            best = float(r.fun)
    return -best


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


# ===========================================================================
# 3. CONVERGENCE ROBUSTNESS — the C1.1 regression (B1 + M5 root cause)
# ===========================================================================


# Small-variance GARCH (σ ≈ 0.10 in percent — REALISTIC daily yield
# changes) at the start-pinned start.  Pre-C1.1, SLSQP stalled here at
# nit=1 with success=True and x pinned at the 0.05/0.90 start; the
# fitted persistence was the 0.95 prior, NOT the MLE.
_SMALL_VAR = dict(omega=2e-4, alpha=0.08, beta=0.90)
# A scale where the true alpha is FAR from the 0.05 start, so a
# stuck-at-start fit would be detectably wrong (alpha 0.08 → 0.05 is a
# 60% error; this set pushes the true alpha further).
_FAR_ALPHA = dict(omega=2e-5, alpha=0.15, beta=0.78)


class TestConvergenceRobustness:
    @pytest.mark.parametrize("n", [5000, 10000])
    @pytest.mark.parametrize("seed", [1, 2, 3, 4])
    def test_large_clean_series_converges_and_moves(self, n, seed):
        # The exact regime the C1.1 finding flagged: large, clean,
        # SMALL-variance GARCH.  No spurious refusal, x MUST move off the
        # 0.05/0.90 start, and the fit reaches the true MLE.
        eps, _ = _sim_garch(n=n, seed=seed, **_SMALL_VAR)
        r = fit_garch_11(eps)
        assert r.converged is True, "spurious non-convergence on clean GARCH"
        assert r.boundary_stuck is False
        # x MOVED off the fixed 0.05/0.90 start (the anti-stuck guard).
        moved = not (abs(r.alpha - 0.05) < 1e-6 and abs(r.beta - 0.90) < 1e-6)
        assert moved, f"fit stuck at the 0.05/0.90 start (a={r.alpha}, b={r.beta})"
        # Production reaches AT LEAST the independent best-of-multistart
        # optimum: loglik must not fall short of the oracle by more than a
        # small tolerance.  (The pre-C1.1 stuck fit fell SHORT by ~46–77;
        # production may also slightly EXCEED a weaker random oracle —
        # that is the optimiser doing its job, so the bound is one-sided.)
        best = _independent_best_loglik(eps)
        assert r.loglik >= best - 0.5, (
            f"production loglik {r.loglik:.2f} fell short of oracle "
            f"{best:.2f} — the optimiser did not reach the MLE"
        )

    @pytest.mark.parametrize("n", [5000, 10000])
    @pytest.mark.parametrize("seed", [11, 22])
    def test_true_alpha_far_from_start_is_recovered(self, n, seed):
        # True alpha 0.15 is 3x the 0.05 start: a stuck-at-start fit
        # would report alpha≈0.05 and be detectably WRONG.
        eps, _ = _sim_garch(n=n, seed=seed, **_FAR_ALPHA)
        r = fit_garch_11(eps)
        assert r.converged is True
        assert r.boundary_stuck is False
        # The fitted alpha is near the true 0.15, NOT pinned near 0.05.
        assert r.alpha == pytest.approx(0.15, abs=0.05), (
            f"alpha {r.alpha:.4f} did not move toward the true 0.15 "
            "(stuck near the 0.05 start)"
        )
        assert abs(r.alpha - 0.05) > 0.03
        best = _independent_best_loglik(eps)
        assert r.loglik >= best - 0.5, (
            f"production loglik {r.loglik:.2f} fell short of oracle {best:.2f}"
        )

    def test_no_silent_stuck_success_on_small_variance(self):
        # The headline M5 symptom: success=True with persistence pinned
        # at the 0.95 prior.  Assert that NEVER happens — a converged fit
        # must have moved off the start.
        for seed in (3, 4, 7, 9):
            eps, _ = _sim_garch(n=10000, seed=seed, **_SMALL_VAR)
            r = fit_garch_11(eps)
            if r.converged:
                pinned = (
                    abs(r.persistence - 0.95) < 1e-9
                    and abs(r.alpha - 0.05) < 1e-9
                    and abs(r.beta - 0.90) < 1e-9
                )
                assert not pinned, (
                    f"seed={seed}: converged=True but x pinned at the "
                    "0.05/0.90/0.95 start (silent stuck fit)"
                )

    def test_determinism_on_large_small_variance(self):
        # OPR14: byte-identical rerun even with the multistart grid (the
        # grid is fixed/seeded, no randomness).
        eps, _ = _sim_garch(n=8000, seed=5, **_SMALL_VAR)
        a = fit_garch_11(eps)
        b = fit_garch_11(eps)
        assert a.omega == b.omega and a.alpha == b.alpha and a.beta == b.beta
        assert a.loglik == b.loglik
        assert np.array_equal(a.conditional_vol, b.conditional_vol)

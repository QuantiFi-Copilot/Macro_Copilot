"""
stats.py — Shared statistical primitives for rates analytics.

Introduced in the v6 sprint by the ``half_life`` tool.  Lives in
``shared/analytics/`` so future tools (``cointegration_test``,
``pca_yield_curve``, ``yield_change_attribution_pca``, etc.) can
add their primitives here without re-fragmenting the substrate.

Solver / methodology lock
-------------------------
``ou_half_life`` uses ``numpy.linalg.lstsq(X, y, rcond=None)`` for the
OU AR(1) fit — same SVD-based, bit-stable solver as
``shared.analytics.regression.rolling_ols``.  The OLS standard error
is then computed via ``(X'X)^-1 · sigma^2``; this is the textbook
form that matches every introductory econometrics reference.  The
delta-method CI on half-life is a closed-form transformation of the
β CI, NOT a separate numerical procedure.

Determinism contract
--------------------
Given a fixed input series + kwargs, the output is bit-stable across
runs.  No RNG, no global state, no wallclock dependence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


__all__ = [
    "OuFitResult",
    "ou_half_life",
]


@dataclass(frozen=True)
class OuFitResult:
    """OU / AR(1) fit result for a single time series.

    The OU/AR(1) discretization is::

        Δx_t = α + β · x_{t-1} + ε_t

    All numeric outputs are floats (or None when the corresponding
    quantity is undefined).  Tools that consume this primitive turn
    it into their wire-output Pydantic shape.

    Attributes
    ----------
    alpha, beta : float
        OLS coefficient estimates.
    beta_std_err : Optional[float]
        Standard error of β from the OLS Σ²·(X'X)^-1 form.  None when
        the (X'X) matrix is singular (zero-variance regressor).
    beta_ci_lower, beta_ci_upper : Optional[float]
        Two-sided CI on β at ``confidence_level``.  None when
        ``beta_std_err`` is None.
    is_mean_reverting : bool
        Strict structural definition: ``β < 0``.  Does NOT involve a
        statistical-significance test (those would be a sibling tool).
    half_life : Optional[float]
        Half-life in series-step units (typically trading days for
        sovereign-yield series).  Defined as ``-ln(2) / ln(1 + β)``
        when AND ONLY WHEN ``-1 < β < 0`` AND
        ``|β| >= min_abs_beta_for_half_life``.  None otherwise:
          - ``β >= 0`` (random walk / divergent)
          - ``β <= -1`` (oscillating divergence: ``1+β <= 0`` makes
            ``ln(1+β)`` undefined)
          - ``|β| < min_abs_beta_for_half_life`` (numerical zero,
            half-life would be ill-conditioned)
    half_life_ci_lower, half_life_ci_upper : Optional[float]
        Delta-method CI on half-life.  None when half-life or
        beta_std_err is None.
    long_run_mean : Optional[float]
        ``-α / β`` when mean-reverting; None otherwise.
    current_value : float
        ``series.iloc[-1]`` (always defined; the series is non-empty
        by precondition).
    current_deviation : Optional[float]
        ``current_value - long_run_mean``.  None when long_run_mean
        is None.
    r_squared : Optional[float]
        In-sample R² of the OU fit.  Bounded in [0, 1] for series
        with non-zero variance in Δx; None when SS_tot == 0.
    observation_count : int
        Number of observations used in the fit (after dropping NaN).
    confidence_level_used : float
        The two-sided confidence level used for both β and half-life
        CIs.  Echoed for transparency.
    """

    alpha: float
    beta: float
    beta_std_err: Optional[float]
    beta_ci_lower: Optional[float]
    beta_ci_upper: Optional[float]
    is_mean_reverting: bool
    half_life: Optional[float]
    half_life_ci_lower: Optional[float]
    half_life_ci_upper: Optional[float]
    long_run_mean: Optional[float]
    current_value: float
    current_deviation: Optional[float]
    r_squared: Optional[float]
    observation_count: int
    confidence_level_used: float


# Z-score for two-sided confidence intervals.  Mapped from common
# levels so we don't need to depend on scipy.stats.norm just for
# this — the OU/AR(1) primitive is otherwise pure numpy.
_TWO_SIDED_Z: dict[float, float] = {
    0.80: 1.281551566,
    0.90: 1.644853627,
    0.95: 1.959963985,
    0.99: 2.575829304,
}


def _z_two_sided(confidence_level: float) -> float:
    """Return the two-sided normal-distribution z-score for the given
    confidence level (e.g., 0.95 → ~1.96).  Looked up from a small
    table of common levels; raises ValueError on uncommon values.

    Pinning a small lookup table avoids an scipy dependency for this
    primitive.  When ``cointegration_test`` lands (which DOES need
    scipy.stats), this helper can be replaced by ``scipy.stats.norm.ppf``
    if the source set grows.
    """
    key = round(confidence_level, 4)
    if key not in _TWO_SIDED_Z:
        raise ValueError(
            f"confidence_level={confidence_level} not in supported set "
            f"{sorted(_TWO_SIDED_Z.keys())}; extend _TWO_SIDED_Z if a "
            "new level is needed (or refactor to scipy.stats.norm.ppf)"
        )
    return _TWO_SIDED_Z[key]


def ou_half_life(
    series: pd.Series,
    *,
    min_observations: int,
    confidence_level: float = 0.95,
    min_abs_beta_for_half_life: float = 1e-6,
) -> OuFitResult:
    """Fit an Ornstein-Uhlenbeck / AR(1) on a series and return the
    half-life of mean reversion plus the underlying OLS coefficients.

    Parameters
    ----------
    series : pd.Series
        Date-indexed series (sovereign yield in percent, cross-market
        spread in bps, or any other 1D series).  NaNs are dropped
        BEFORE the observation count is checked.
    min_observations : int
        Minimum non-NaN row count after dropna.  ``ou_half_life``
        raises ``ValueError`` (caller turns into the controlled-error
        envelope) when the series is shorter than this threshold —
        OU SE estimates are unreliable below ~252 obs.
    confidence_level : float
        Two-sided confidence level for both the β CI and the
        delta-method half-life CI.  Default 0.95.  Must be in the
        ``_TWO_SIDED_Z`` lookup table.
    min_abs_beta_for_half_life : float
        Numerical-stability floor on |β|.  Default 1e-6.  When |β| is
        below this, the half-life formula becomes ill-conditioned
        (``-ln(2) / ln(1+β)`` blows up as β → 0); we return None for
        half-life rather than emit a meaningless huge number.

    Returns
    -------
    OuFitResult
        See dataclass docstring for the full edge-case map.

    Raises
    ------
    ValueError
        When the series has fewer than ``min_observations`` non-NaN
        rows, or when ``confidence_level`` is not in the supported
        set.

    Notes
    -----
    The discretized AR(1) is stable iff ``-1 < β < 0`` (mean reverting,
    monotone decay) — a slightly tighter range than the textbook
    ``-2 < β < 0`` because the half-life formula via
    ``-ln(2) / ln(1+β)`` requires ``1+β > 0``.  Series with
    ``-2 < β <= -1`` exhibit oscillating-but-bounded behaviour; we
    surface them as ``is_mean_reverting=True`` (β < 0) but with
    ``half_life=None`` (no closed-form formula in this primitive).
    Documented in the dataclass attributes section.
    """
    # ------------------------------------------------------------------
    # Validate + clean
    # ------------------------------------------------------------------
    if min_observations <= 0:
        raise ValueError(f"min_observations must be > 0, got {min_observations}")
    z = _z_two_sided(confidence_level)

    cleaned = series.dropna()
    n_clean = len(cleaned)
    if n_clean < min_observations:
        raise ValueError(
            f"series has {n_clean} non-NaN observations after dropna; "
            f"the OU primitive requires at least {min_observations}.  "
            "OU SE estimates are unreliable below this threshold."
        )

    # ------------------------------------------------------------------
    # Build the regression: Δx_t = α + β · x_{t-1} + ε_t
    # ------------------------------------------------------------------
    x_lag = cleaned.iloc[:-1].to_numpy(dtype=float)
    dx = np.diff(cleaned.to_numpy(dtype=float))
    n_obs = len(dx)
    # Design matrix: [1, x_lag]
    design = np.column_stack([np.ones(n_obs, dtype=float), x_lag])

    # ------------------------------------------------------------------
    # OLS fit via SVD-based lstsq (locked: numpy_lstsq_default)
    # ------------------------------------------------------------------
    coefs, _r, _rank, _sv = np.linalg.lstsq(design, dx, rcond=None)
    alpha = float(coefs[0])
    beta = float(coefs[1])

    fitted = design @ coefs
    resid = dx - fitted
    ss_res = float(np.sum(resid ** 2))
    dx_mean = float(np.mean(dx))
    ss_tot = float(np.sum((dx - dx_mean) ** 2))
    r_squared: Optional[float] = (
        1.0 - ss_res / ss_tot if ss_tot > 0 else None
    )

    # ------------------------------------------------------------------
    # Beta standard error: σ² · (X'X)^-1, bottom-right element
    # ------------------------------------------------------------------
    p = 2  # alpha + beta
    beta_std_err: Optional[float] = None
    if n_obs > p:
        sigma2 = ss_res / (n_obs - p)
        try:
            xtx_inv = np.linalg.inv(design.T @ design)
            var_beta = float(sigma2 * xtx_inv[1, 1])
            if var_beta >= 0 and math.isfinite(var_beta):
                beta_std_err = math.sqrt(var_beta)
        except np.linalg.LinAlgError:
            beta_std_err = None

    if beta_std_err is not None:
        beta_ci_lower: Optional[float] = beta - z * beta_std_err
        beta_ci_upper: Optional[float] = beta + z * beta_std_err
    else:
        beta_ci_lower = beta_ci_upper = None

    # ------------------------------------------------------------------
    # Mean-reversion test (strict structural definition: β < 0)
    # ------------------------------------------------------------------
    is_mean_reverting = beta < 0.0

    # ------------------------------------------------------------------
    # Half-life + long-run mean — only when -1 < β < 0 AND
    # |β| >= min_abs_beta_for_half_life.
    # ------------------------------------------------------------------
    one_plus_beta = 1.0 + beta
    half_life: Optional[float] = None
    long_run_mean: Optional[float] = None
    current_deviation: Optional[float] = None
    half_life_ci_lower: Optional[float] = None
    half_life_ci_upper: Optional[float] = None

    if (
        is_mean_reverting
        and abs(beta) >= min_abs_beta_for_half_life
        and 0.0 < one_plus_beta < 1.0
    ):
        ln_decay = math.log(one_plus_beta)  # negative
        half_life = -math.log(2.0) / ln_decay
        long_run_mean = -alpha / beta

        # Delta-method CI on half-life:
        # half_life = -ln(2) / ln(1+β)
        # d(half_life)/dβ = ln(2) / [(1+β) · (ln(1+β))^2]
        if beta_std_err is not None:
            d_half_d_beta = math.log(2.0) / (one_plus_beta * (ln_decay ** 2))
            half_life_se = abs(d_half_d_beta) * beta_std_err
            half_life_ci_lower = half_life - z * half_life_se
            half_life_ci_upper = half_life + z * half_life_se

    current_value = float(cleaned.iloc[-1])
    if long_run_mean is not None:
        current_deviation = current_value - long_run_mean

    return OuFitResult(
        alpha=alpha,
        beta=beta,
        beta_std_err=beta_std_err,
        beta_ci_lower=beta_ci_lower,
        beta_ci_upper=beta_ci_upper,
        is_mean_reverting=is_mean_reverting,
        half_life=half_life,
        half_life_ci_lower=half_life_ci_lower,
        half_life_ci_upper=half_life_ci_upper,
        long_run_mean=long_run_mean,
        current_value=current_value,
        current_deviation=current_deviation,
        r_squared=r_squared,
        observation_count=n_obs,
        confidence_level_used=confidence_level,
    )

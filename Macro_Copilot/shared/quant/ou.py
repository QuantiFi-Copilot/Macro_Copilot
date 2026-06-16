"""shared.quant.ou — Ornstein–Uhlenbeck / mean-reversion fit.

The THIRD numeric in ``shared/quant/`` (after ``variance_ratio.py`` and
``hurst.py``).  Pure, finance-blind, deterministic per the package
boundary rules.  Two entry points share ONE regression core
(:func:`fit_ou_core`, the single source of truth for the mean-reversion
math — P10): the L3 ``fit_ou`` operator via :func:`fit_ou_ar1` (strict
``0 < φ < 1`` POINT gate + the unit-root SIGNIFICANCE gate) and the
rates ``half_life`` primitive via ``shared.analytics.stats.ou_half_life``
(looser ``β < 0`` point gate + the SE / delta-method-CI extras).  See
:class:`OuCoreFit` for why the mean-reversion gate is the caller's to
apply.

POINT ESTIMATE vs SIGNIFICANCE (the M2 honesty fix — describe-not-
forecast / P2 / P5).  The sign of the OLS β is a POINT ESTIMATE, not a
verdict: finite-sample AR(1)-OLS bias pushes β slightly negative on a
true unit root, so a pure random walk reads ``β < 0`` (hence
``point_estimate_mean_reverting=True``) and produces a finite half-life
~96% of the time.  That half-life is a half-correct calculation —
mean reversion the data cannot support.  So the core ALSO computes the
unit-root SIGNIFICANCE: the β standard error, the Dickey–Fuller
t-statistic ``df_tstat = β / SE(β)`` (the ADF(0)/intercept statistic),
and the MacKinnon ``unit_root_pvalue`` against the unit-root null
(``H0: β = 0``, NO mean reversion).  ``unit_root_rejected`` is the
one-sided 5% verdict (the same DF/MacKinnon surface ``stationarity_adf``
uses — P13).  A random walk does NOT reject (median p ≈ 0.51); a
genuine OU does (p ≈ 1e-28).  The boolean once named ``is_mean_reverting``
is renamed ``point_estimate_mean_reverting`` to make the sign-vs-verdict
distinction un-misreadable; the ``fit_ou`` operator now refuses unless
the unit root is rejected (P5/P2 honest current-state read).

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
from statsmodels.tsa.adfvalues import mackinnonp


# The φ→1 ill-conditioning floor (mirrors the half_life primitive's
# min_abs_beta_for_half_life): a |β| below this is indistinguishable
# from a unit root and the half-life formula blows up.
_MIN_DECAY = 1e-6

# Minimum increments for a non-degenerate AR(1) regression (the
# operator enforces the higher family floor of 12).
_MIN_INCREMENTS = 3

# The unit-root SIGNIFICANCE level the operator gates on (one-sided
# Dickey–Fuller).  Reject H0 (β = 0, a unit root / random walk) at 5% —
# the conventional macro-stationarity threshold, matching the level a
# desk reads off stationarity_adf's MacKinnon p-value.
_UNIT_ROOT_ALPHA = 0.05


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
    point_estimate_mean_reverting :
        Whether the POINT ESTIMATE says ``0 < φ < 1`` AND the fit is
        well-conditioned.  This is the SIGN of the OLS estimate, NOT a
        significance verdict — a pure random walk reads ``True`` ~96% of
        the time from finite-sample bias.  Read ``unit_root_rejected``
        for the honest verdict (renamed from ``is_mean_reverting``).
    beta_std_err :
        OLS standard error of β (``√(σ²·(XᵀX)⁻¹[1,1])``); ``None`` on a
        rank-deficient design (a constant regressor) or < 3 increments.
    df_tstat :
        The Dickey–Fuller t-statistic ``β / SE(β)`` (the ADF(0) /
        intercept statistic).  More negative ⇒ stronger evidence
        against the unit-root null.  ``None`` when ``beta_std_err`` is.
    unit_root_pvalue :
        MacKinnon one-sided p-value for the unit-root null (``β = 0``,
        no mean reversion) given ``df_tstat``.  Low ⇒ reject the unit
        root ⇒ genuine mean reversion.  ``None`` when ``df_tstat`` is.
    unit_root_rejected :
        Whether the unit root is rejected at the 5% level
        (``unit_root_pvalue < 0.05``) — the SIGNIFICANCE verdict the
        operator gates the half-life on.  ``None`` when undecidable
        (no p-value).
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
    point_estimate_mean_reverting: bool
    beta_std_err: Optional[float]
    df_tstat: Optional[float]
    unit_root_pvalue: Optional[float]
    unit_root_rejected: Optional[bool]
    n_increments: int


@dataclass(frozen=True)
class OuCoreFit:
    """The gate-free OU / AR(1) Δx-regression fit — the SINGLE source
    of truth for the mean-reversion math (P10).

    Holds the raw regression (α, β, φ, residual algebra) plus the
    half-life / μ / θ / σ_eq derivation, computed under the SHARED
    half-life gate ``0 < φ < 1 AND |β| >= min_decay``, AND the shared
    unit-root significance test (β SE, DF t-stat, MacKinnon p-value).
    What it deliberately does NOT do is classify *mean reversion* by a
    POINT-ESTIMATE sign — that gate differs by caller and is layered on
    top of the raw (α, β, φ):

      - :func:`fit_ou_ar1` (the finance-blind ``fit_ou`` operator) uses
        the STRICT ``0 < φ < 1`` POINT definition.  Since ``0 < φ < 1``
        already implies ``β < 0``, that is identical to the half-life
        gate, so its ``point_estimate_mean_reverting`` is exactly
        :attr:`half_life_defined`.  The operator additionally gates the
        emitted half-life on :attr:`unit_root_rejected` (the SIGNIFICANCE
        verdict) so a random walk's spurious half-life is refused.
      - ``shared.analytics.stats.ou_half_life`` (the rates ``half_life``
        primitive) uses the LOOSER ``β < 0`` point definition, which also
        admits oscillatory ``β <= -1`` (reported mean-reverting but
        with ``half_life=None``, since the formula needs ``1 + β > 0``);
        it surfaces :attr:`unit_root_rejected` alongside so a non-
        significant half-life is disclosed, never read as confident.

    The half-life derivation AND the unit-root test are identical across
    both callers; only the reported POINT mean-reversion flag differs.

    Attributes
    ----------
    alpha, beta, phi :
        The Δx-regression intercept, slope (β = φ − 1), and AR(1)
        coefficient (φ = 1 + β).
    sigma_eps :
        Residual (innovation) std, unbiased by the 2 fitted params.
    ss_res :
        Residual sum of squares — feeds the caller's σ² for the β SE.
    r_squared :
        R² of the Δx regression (``None`` when SS_tot == 0).
    n_increments :
        The number of one-period increments used (n_obs − 1).
    xx_inv_beta :
        The ``(XᵀX)⁻¹`` bottom-right element (the β-coefficient
        covariance factor); ``None`` when the design is rank-deficient
        (e.g. a constant regressor).  Scaling it by σ² gives Var(β) —
        the (finance-aware) delta-method CI machinery stays in the
        caller, but the β SE and the unit-root test below are computed
        HERE so both callers share one significance verdict (P10).
    beta_std_err :
        OLS standard error of β = ``√(σ²·xx_inv_beta)`` with
        ``σ² = ss_res/(n−2)``; ``None`` when ``xx_inv_beta`` is ``None``
        or there are too few increments for a residual variance.
    df_tstat :
        The Dickey–Fuller t-statistic ``β / SE(β)`` (the ADF(0) /
        intercept-only statistic — the unit-root test of the SAME Δx
        regression).  ``None`` when ``beta_std_err`` is.
    unit_root_pvalue :
        MacKinnon one-sided p-value for the unit-root null (``β = 0``)
        given ``df_tstat`` (regression='c', N=1 — the same DF surface
        ``stationarity_adf`` uses).  ``None`` when ``df_tstat`` is.
    unit_root_rejected :
        ``unit_root_pvalue < 0.05`` (the 5% one-sided verdict); ``None``
        when no p-value.  This — NOT the β sign — is whether the data
        statistically supports mean reversion.
    theta, half_life, mu, sigma_eq :
        The mean-reversion speed −ln(φ), the half-life ``−ln2/ln(φ)``,
        the equilibrium ``−α/β``, and the stationary std
        ``σ_ε/√(1−φ²)`` — all populated iff :attr:`half_life_defined`,
        else ``None``.
    half_life_defined :
        Whether the shared half-life gate held AND produced a finite
        half-life (the belt-and-suspenders).
    """

    alpha: float
    beta: float
    phi: float
    sigma_eps: float
    ss_res: float
    r_squared: Optional[float]
    n_increments: int
    xx_inv_beta: Optional[float]
    beta_std_err: Optional[float]
    df_tstat: Optional[float]
    unit_root_pvalue: Optional[float]
    unit_root_rejected: Optional[bool]
    theta: Optional[float]
    half_life: Optional[float]
    mu: Optional[float]
    sigma_eq: Optional[float]
    half_life_defined: bool


def fit_ou_core(
    values: np.ndarray, *, min_decay: float = _MIN_DECAY,
) -> OuCoreFit:
    """The single-source Δx = α + β·x_{t-1} OLS + half-life derivation.

    Pure regression algebra: does NO input validation and applies NO
    *mean-reversion* classification — both are the caller's job (the
    validation differs, and the mean-reversion gate differs; see
    :class:`OuCoreFit`).  Assumes a 1-D finite array with at least one
    increment (≥ 2 elements).  On a constant series the ``(XᵀX)``
    inverse is singular and :attr:`OuCoreFit.xx_inv_beta` comes back
    ``None`` (matching the legacy stats.py SE path, which returns a
    ``None`` β standard error there rather than raising).

    Parameters
    ----------
    values :
        The 1-D level series (the caller has already dropped NaNs).
    min_decay :
        The φ→1 ill-conditioning floor on ``|β|`` for the half-life
        gate.  ``_MIN_DECAY`` for the operator; the rates primitive
        passes its configurable ``min_abs_beta_for_half_life``.

    Returns
    -------
    OuCoreFit
        The raw fit plus the half-life block (the latter populated iff
        ``0 < φ < 1 AND |β| >= min_decay`` and the result is finite).
    """
    arr = np.asarray(values, dtype=float).ravel()

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

    # (XᵀX)⁻¹ bottom-right element — the β-coefficient covariance
    # factor.  The caller scales it by σ² to get the β SE (the
    # delta-method CI is the finance-aware extra).  None when the design
    # is rank-deficient (constant regressor).
    xx_inv_beta: Optional[float] = None
    try:
        xtx_inv = np.linalg.inv(design.T @ design)
        xx_inv_beta = float(xtx_inv[1, 1])
    except np.linalg.LinAlgError:
        xx_inv_beta = None

    # Unit-root SIGNIFICANCE (the M2 honesty gate — shared by BOTH
    # callers so the verdict is computed once).  β SE = √(σ²·(XᵀX)⁻¹),
    # the Dickey–Fuller t-statistic β/SE(β) (= the ADF(0)/intercept
    # statistic of this same Δx regression), and its MacKinnon one-sided
    # p-value against the unit-root null (β = 0 ⇒ random walk, no mean
    # reversion).  Deterministic: mackinnonp evaluates a fixed
    # response-surface polynomial — no RNG, no wall-clock.
    beta_std_err: Optional[float] = None
    df_tstat: Optional[float] = None
    unit_root_pvalue: Optional[float] = None
    unit_root_rejected: Optional[bool] = None
    if xx_inv_beta is not None and n_inc > 2:
        sigma2 = ss_res / (n_inc - 2)
        var_beta = sigma2 * xx_inv_beta
        if math.isfinite(var_beta) and var_beta > 0.0:
            beta_std_err = math.sqrt(var_beta)
            df_tstat = beta / beta_std_err
            # regression='c' (intercept), N=1 (one estimated series) —
            # the same DF/MacKinnon surface stationarity_adf reads (P13).
            unit_root_pvalue = float(
                mackinnonp(df_tstat, regression="c", N=1)
            )
            unit_root_rejected = bool(unit_root_pvalue < _UNIT_ROOT_ALPHA)

    # Shared half-life gate: 0 < φ < 1 AND a well-conditioned decay.
    # (0 < φ < 1 already implies β < 0, so each caller's own
    # mean-reversion flag is layered separately — see OuCoreFit.)
    half_life_defined = (0.0 < phi < 1.0) and (abs(beta) >= min_decay)
    theta: Optional[float] = None
    half_life: Optional[float] = None
    mu: Optional[float] = None
    sigma_eq: Optional[float] = None
    if half_life_defined:
        ln_phi = math.log(phi)  # negative
        theta = -ln_phi
        half_life = -math.log(2.0) / ln_phi
        mu = -alpha / beta
        sigma_eq = sigma_eps / math.sqrt(1.0 - phi * phi)
        if not math.isfinite(half_life):  # belt-and-suspenders
            half_life_defined = False
            theta = half_life = mu = sigma_eq = None

    return OuCoreFit(
        alpha=alpha,
        beta=beta,
        phi=phi,
        sigma_eps=float(sigma_eps),
        ss_res=ss_res,
        r_squared=r_squared,
        n_increments=n_inc,
        xx_inv_beta=xx_inv_beta,
        beta_std_err=beta_std_err,
        df_tstat=df_tstat,
        unit_root_pvalue=unit_root_pvalue,
        unit_root_rejected=unit_root_rejected,
        theta=theta,
        half_life=half_life,
        mu=mu,
        sigma_eq=sigma_eq,
        half_life_defined=half_life_defined,
    )


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

    # The Δx = α + β·x_lag OLS + half-life derivation + the unit-root
    # significance test live in fit_ou_core (the single source of truth,
    # also called by the rates half_life primitive via
    # shared.analytics.stats.ou_half_life).  The operator's strict
    # POINT-ESTIMATE mean-reversion definition (0 < φ < 1 AND a
    # well-conditioned decay) is exactly the core's half-life gate, so
    # point_estimate_mean_reverting == core.half_life_defined.  The
    # unit-root VERDICT (unit_root_rejected) is the separate
    # significance read the operator gates on.
    core = fit_ou_core(arr, min_decay=_MIN_DECAY)
    return OuFitResult(
        alpha=core.alpha,
        beta=core.beta,
        phi=core.phi,
        theta=core.theta,
        half_life=core.half_life,
        mu=core.mu,
        sigma_eps=core.sigma_eps,
        sigma_eq=core.sigma_eq,
        r_squared=core.r_squared,
        point_estimate_mean_reverting=core.half_life_defined,
        beta_std_err=core.beta_std_err,
        df_tstat=core.df_tstat,
        unit_root_pvalue=core.unit_root_pvalue,
        unit_root_rejected=core.unit_root_rejected,
        n_increments=core.n_increments,
    )


__all__ = ["fit_ou_ar1", "fit_ou_core", "OuFitResult", "OuCoreFit"]

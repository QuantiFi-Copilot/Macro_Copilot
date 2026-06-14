"""
stats.py — Shared statistical primitives for rates analytics.

Introduced in the v6 sprint by the ``half_life`` tool.  Lives in
``shared/analytics/`` so future tools (``cointegration_test``,
``pca_yield_curve``, ``yield_change_attribution_pca``, etc.) can
add their primitives here without re-fragmenting the substrate.

Solver / methodology lock
-------------------------
The Δx = α + β·x_{t-1} OLS fit and the half-life / μ / φ / θ / σ
derivation are NOT implemented here: ``ou_half_life`` delegates them to
``shared.quant.ou.fit_ou_core`` — the single source of truth for the
OU / AR(1) mean-reversion math (the same finance-blind core the
``fit_ou`` operator uses), resolving the P10 duplication flagged in the
fit_ou operator review.  The core's solver is
``numpy.linalg.lstsq(X, y, rcond=None)`` — the same SVD-based,
bit-stable routine as ``shared.analytics.regression.rolling_ols``.

What stays HERE is the finance-aware machinery layered on top: the OLS
β standard error (``(X'X)^-1 · sigma^2`` — the textbook form, scaling
the core's ``(X'X)^-1`` factor by σ²), the delta-method half-life CI (a
closed-form transform of the β CI, NOT a separate numerical procedure),
the confidence-level lookup, and the LOOSER
``is_mean_reverting = β < 0`` definition the rates ``half_life``
primitive relies on (the core's strict ``0 < φ < 1`` gate governs only
whether a finite half-life is emitted, not this flag).

Determinism contract
--------------------
Given a fixed input series + kwargs, the output is bit-stable across
runs.  No RNG, no global state, no wallclock dependence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

import numpy as np
import pandas as pd

from shared.quant.ou import fit_ou_core


__all__ = [
    "OuFitResult",
    "ou_half_life",
    "PcaResult",
    "PcaComponentInfo",
    "pca_yield_changes",
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
    # Δx_t = α + β · x_{t-1} + ε_t OLS + half-life / μ derivation.
    #
    # The regression and the half-life / μ / φ / θ / σ derivation are
    # the single-source-of-truth core in shared.quant.ou (P10) — the
    # SAME math the finance-blind fit_ou operator uses.  We pass the
    # configurable |β| floor as the half-life ill-conditioning gate;
    # the core's gate (0 < φ < 1 AND |β| >= min_decay) is identical to
    # the legacy gate this primitive applied inline.  Only the
    # finance-aware extras — the β standard error, the delta-method
    # half-life CI, and the looser β<0 mean-reversion definition — stay
    # below.
    # ------------------------------------------------------------------
    core = fit_ou_core(
        cleaned.to_numpy(dtype=float),
        min_decay=min_abs_beta_for_half_life,
    )
    alpha = core.alpha
    beta = core.beta
    n_obs = core.n_increments
    r_squared = core.r_squared

    # ------------------------------------------------------------------
    # Beta standard error: σ² · (X'X)^-1, bottom-right element.  The
    # (X'X)^-1 factor comes from the shared core; σ² and the z-scaling
    # are the finance-aware extra.  (X'X)^-1 is None on a singular
    # design — same as the legacy LinAlgError path.
    # ------------------------------------------------------------------
    p = 2  # alpha + beta
    beta_std_err: Optional[float] = None
    if n_obs > p and core.xx_inv_beta is not None:
        sigma2 = core.ss_res / (n_obs - p)
        var_beta = float(sigma2 * core.xx_inv_beta)
        if var_beta >= 0 and math.isfinite(var_beta):
            beta_std_err = math.sqrt(var_beta)

    if beta_std_err is not None:
        beta_ci_lower: Optional[float] = beta - z * beta_std_err
        beta_ci_upper: Optional[float] = beta + z * beta_std_err
    else:
        beta_ci_lower = beta_ci_upper = None

    # ------------------------------------------------------------------
    # Mean-reversion test — this primitive's LOOSER structural
    # definition (β < 0).  Deliberately broader than the operator's
    # strict 0 < φ < 1 gate: it admits oscillatory β <= -1 as
    # mean-reverting, with half_life=None (the formula needs 1+β > 0).
    # See shared.quant.ou.OuCoreFit for why the gate is the caller's.
    # ------------------------------------------------------------------
    is_mean_reverting = beta < 0.0

    # ------------------------------------------------------------------
    # Half-life + long-run mean come straight from the shared core (its
    # gate 0<φ<1 ∧ |β|>=min_abs_beta is identical to the legacy one).
    # Only the delta-method CI is layered on here:
    #   half_life = -ln(2) / ln(1+β)
    #   d(half_life)/dβ = ln(2) / [(1+β) · (ln(1+β))^2]
    # ------------------------------------------------------------------
    half_life = core.half_life
    long_run_mean = core.mu
    current_deviation: Optional[float] = None
    half_life_ci_lower: Optional[float] = None
    half_life_ci_upper: Optional[float] = None

    if half_life is not None and beta_std_err is not None:
        one_plus_beta = core.phi
        ln_decay = math.log(one_plus_beta)  # negative
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


# ============================================================================
# PCA on yield-changes panel
# ============================================================================

# Locked V1 sign-anchor rule.  Documented in
# rates_agent/sovereign_bonds/tools/pca_yield_curve/config.yaml under
# planned_extensions for the alternatives ("max_abs_loading_positive",
# "first_tenor_positive").  pca_yield_changes raises ValueError on any
# other anchor name; the PCA tool's compute() converts it to a
# NotImplementedError with a pointer to planned_extensions.
_LOCKED_SIGN_ANCHOR: str = "lock_pc_long_tenor_positive"


@dataclass(frozen=True)
class PcaComponentInfo:
    """Per-component metadata returned by ``pca_yield_changes``.

    Attributes
    ----------
    component_name : str
        Lower snake-case label — ``"pc1"``, ``"pc2"``, ``"pc3"``, ...
    quality_flag : str
        One of:
          - ``"ok"`` — eigenvalue > eps; sign-anchor unambiguous.
          - ``"degenerate"`` — eigenvalue effectively zero (variance
            share below ``degenerate_variance_share_threshold``);
            loadings emitted as NaN; scores emitted as NaN.
          - ``"sign_anchor_tied"`` — loadings at the longest tenor are
            exactly zero AND ``loadings[longest] - loadings[shortest]``
            is exactly zero, so neither tie-break can pick a side; no
            flip applied; downstream interpretation may be ambiguous.
    quality_note : Optional[str]
        Human-readable detail; None when ``quality_flag == "ok"``.
    """

    component_name: str
    quality_flag: str
    quality_note: Optional[str]


@dataclass(frozen=True)
class PcaResult:
    """Output of ``pca_yield_changes``.

    All numeric outputs are bit-stable across runs given the input
    panel, the n_components / change_frequency / sign_anchor kwargs,
    and the tenor list.  The PCA solve uses ``numpy.linalg.svd``
    (LAPACK driver, deterministic across numpy ≥ 1.14).

    Attributes
    ----------
    loadings : pd.DataFrame
        Index = tenor labels (in caller-supplied order); columns =
        component names (``pc1``, ``pc2``, ...).  Values are eigen-
        vector entries for the centered yield-change covariance.
        After the sign anchor is applied, loadings at the longest
        tenor are >= 0 for non-degenerate components.  NaN columns
        for degenerate components.
    factor_scores : pd.DataFrame
        Index = trading-day dates of the change panel; columns =
        component names.  Values are the projection of each centered
        change row onto each component (i.e., the time series of
        factor levels).  NaN columns for degenerate components.
    variance_share : pd.Series
        Index = component names; values in [0, 1] summing to ≤ 1
        (will sum to exactly 1 when n_components == n_tenors and no
        component is degenerate).
    cumulative_variance_share : pd.Series
        Cumulative sum of ``variance_share`` over component_names in
        emission order.  Useful for "how much variance does the top
        K components explain" queries.
    component_metadata : List[PcaComponentInfo]
        Per-component quality flag + note.  Emitted in the same
        order as ``loadings.columns`` / ``variance_share.index``.
    n_observations_in_fit : int
        Number of trading-day rows used in the SVD (after dropping
        rows with any NaN).  Echoed for transparency / paste-into-
        downstream-tool provenance.
    n_tenors : int
        Number of tenors (columns of the input panel).
    change_frequency_used : str
        Echoes the ``change_frequency`` kwarg.
    sign_anchor_used : str
        Echoes the ``sign_anchor`` kwarg (always
        ``"lock_pc_long_tenor_positive"`` in V1).
    fit_window_start : str, fit_window_end : str
        First and last dates of the change panel (YYYY-MM-DD).
    """

    loadings: pd.DataFrame
    factor_scores: pd.DataFrame
    variance_share: pd.Series
    cumulative_variance_share: pd.Series
    component_metadata: List[PcaComponentInfo]
    n_observations_in_fit: int
    n_tenors: int
    change_frequency_used: str
    sign_anchor_used: str
    fit_window_start: str
    fit_window_end: str


def pca_yield_changes(
    panel: pd.DataFrame,
    *,
    tenors_ordered: List[str],
    n_components: int,
    change_frequency: Literal["daily", "weekly"],
    sign_anchor: str = _LOCKED_SIGN_ANCHOR,
    min_observations: int,
    degenerate_variance_share_threshold: float = 1e-12,
) -> PcaResult:
    """Run PCA on the yield-CHANGES panel of a single sovereign curve.

    Parameters
    ----------
    panel : pd.DataFrame
        Wide-format date-indexed yield panel.  Columns must be a
        superset of ``tenors_ordered``.  Values are yields (in
        percent or any consistent unit; PCA on changes is unit-
        agnostic).
    tenors_ordered : List[str]
        Tenor labels in numeric-ascending order (e.g.,
        ``["1Y", "2Y", "3Y", "5Y", "7Y", "10Y", "20Y", "30Y"]``).
        Caller is responsible for the sort — the primitive uses
        position 0 (shortest) and position -1 (longest) for the
        sign-anchor's tie-break.  The result's loadings DataFrame
        index follows this order.
    n_components : int
        Number of components to return.  Must be 1 ≤ n_components ≤
        n_tenors.
    change_frequency : "daily" | "weekly"
        Frequency at which to take yield differences before fitting
        PCA.  ``"daily"`` → ``panel.diff()``; ``"weekly"`` →
        ``panel.diff(periods=5)`` (5 trading days).
    sign_anchor : str
        V1 supports only ``"lock_pc_long_tenor_positive"``.  Other
        values raise ValueError; the consuming tool wraps that into
        the NotImplementedError honest-placeholder pattern.
    min_observations : int
        Minimum non-NaN observations in the change panel after
        dropna.  PCA on fewer than ~252 obs gives unstable factor
        loadings; raises ValueError below this threshold.
    degenerate_variance_share_threshold : float
        Variance-share floor below which a component is flagged
        ``"degenerate"`` and its loadings / scores are emitted as
        NaN.  Default 1e-12 catches numerical zeros without flagging
        small-but-real components.

    Returns
    -------
    PcaResult
        See dataclass docstring.

    Raises
    ------
    ValueError
        On any of: missing tenor in panel, invalid n_components,
        unsupported sign_anchor, change panel below
        min_observations, etc.  Caller turns into the controlled-
        error envelope.
    """
    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------
    if sign_anchor != _LOCKED_SIGN_ANCHOR:
        raise ValueError(
            f"sign_anchor={sign_anchor!r} is not supported; V1 supports "
            f"only {_LOCKED_SIGN_ANCHOR!r}.  Alternative anchors are "
            "documented under planned_extensions in the consuming "
            "tool's config.yaml; the consuming tool converts this to "
            "NotImplementedError."
        )
    if change_frequency not in ("daily", "weekly"):
        raise ValueError(
            f"change_frequency={change_frequency!r} not supported; "
            "expected 'daily' or 'weekly'."
        )
    if not tenors_ordered:
        raise ValueError("tenors_ordered must be non-empty")
    n_tenors = len(tenors_ordered)
    if n_components < 1 or n_components > n_tenors:
        raise ValueError(
            f"n_components={n_components} out of range [1, {n_tenors}]"
        )
    missing = [t for t in tenors_ordered if t not in panel.columns]
    if missing:
        raise ValueError(
            f"panel is missing tenor columns: {missing}.  "
            f"Available: {sorted(panel.columns)}."
        )

    # ------------------------------------------------------------------
    # Slice + sort + diff
    # ------------------------------------------------------------------
    sub = panel[tenors_ordered].sort_index()
    if change_frequency == "daily":
        changes = sub.diff().iloc[1:]
    else:
        changes = sub.diff(periods=5).iloc[5:]
    changes = changes.dropna(how="any")
    n_obs = int(len(changes))
    if n_obs < min_observations:
        raise ValueError(
            f"change panel has {n_obs} non-NaN rows after dropna at "
            f"frequency={change_frequency!r}; PCA primitive requires "
            f"at least {min_observations}.  Either supply a longer "
            f"input window or reduce the YAML's "
            "min_observations_for_pca."
        )

    fit_window_start = changes.index[0].strftime("%Y-%m-%d")
    fit_window_end = changes.index[-1].strftime("%Y-%m-%d")

    # ------------------------------------------------------------------
    # Center per-tenor (column means subtracted)
    # ------------------------------------------------------------------
    X = changes.to_numpy(dtype=float, copy=True)
    col_means = X.mean(axis=0)
    Xc = X - col_means

    # ------------------------------------------------------------------
    # SVD: Xc = U · diag(S) · Vt
    #   * Each column of V (= row of Vt) is a tenor-space eigenvector
    #     (loading vector).
    #   * Singular values S relate to eigenvalues via S² / (n_obs - 1).
    #   * Factor scores at each date = Xc @ V_truncated (which equals
    #     U_truncated @ diag(S_truncated)).
    # ------------------------------------------------------------------
    # full_matrices=False: economy SVD (deterministic LAPACK driver).
    U, S, Vt = np.linalg.svd(Xc, full_matrices=False)
    eigenvalues_full = (S ** 2) / max(n_obs - 1, 1)
    total_variance = float(np.sum(eigenvalues_full))

    # Truncate to n_components.
    eigenvalues = eigenvalues_full[:n_components]
    loadings = Vt[:n_components].T  # shape: [n_tenors, n_components]
    scores = U[:, :n_components] * S[:n_components]  # [n_obs, n_components]

    # ------------------------------------------------------------------
    # Detect degenerate components (variance share below threshold)
    # ------------------------------------------------------------------
    if total_variance > 0:
        full_variance_share = eigenvalues_full / total_variance
    else:
        full_variance_share = np.zeros_like(eigenvalues_full)
    component_names = [f"pc{k+1}" for k in range(n_components)]
    component_metadata: List[PcaComponentInfo] = []
    variance_share_truncated = np.zeros(n_components, dtype=float)

    longest_idx = n_tenors - 1   # tenors_ordered is asc, so last = longest
    shortest_idx = 0             # first = shortest

    for k in range(n_components):
        var_share = float(
            full_variance_share[k] if k < len(full_variance_share) else 0.0
        )
        if var_share < degenerate_variance_share_threshold:
            # Mark degenerate, suppress loadings + scores
            loadings[:, k] = np.nan
            scores[:, k] = np.nan
            variance_share_truncated[k] = 0.0
            component_metadata.append(
                PcaComponentInfo(
                    component_name=component_names[k],
                    quality_flag="degenerate",
                    quality_note=(
                        f"variance_share={var_share:.3e} < threshold "
                        f"{degenerate_variance_share_threshold:.0e}; "
                        "loadings + scores suppressed to NaN.  Likely a "
                        "rank-deficient panel (e.g., a tenor that is a "
                        "linear combination of others)."
                    ),
                )
            )
            continue

        # Apply locked sign anchor: flip so loading at longest tenor
        # is non-negative.
        long_load = loadings[longest_idx, k]
        eps = 1e-12
        flip = False
        flag = "ok"
        note: Optional[str] = None
        if long_load > eps:
            flip = False
        elif long_load < -eps:
            flip = True
        else:
            # Tie-break: longest minus shortest difference
            diff = loadings[longest_idx, k] - loadings[shortest_idx, k]
            if diff > eps:
                flip = False
            elif diff < -eps:
                flip = True
            else:
                flip = False
                flag = "sign_anchor_tied"
                note = (
                    "loadings at longest tenor exactly zero AND "
                    "(longest - shortest) exactly zero; no sign flip "
                    "applied.  Component direction is ambiguous; "
                    "downstream interpretation may need a different "
                    "anchor."
                )

        if flip:
            loadings[:, k] *= -1.0
            scores[:, k] *= -1.0

        variance_share_truncated[k] = var_share
        component_metadata.append(
            PcaComponentInfo(
                component_name=component_names[k],
                quality_flag=flag,
                quality_note=note,
            )
        )

    # ------------------------------------------------------------------
    # Assemble pandas outputs
    # ------------------------------------------------------------------
    loadings_df = pd.DataFrame(
        loadings, index=list(tenors_ordered), columns=component_names,
    )
    scores_df = pd.DataFrame(
        scores, index=changes.index, columns=component_names,
    )
    variance_share_s = pd.Series(
        variance_share_truncated, index=component_names, name="variance_share",
    )
    cumulative_share_s = variance_share_s.cumsum().rename(
        "cumulative_variance_share"
    )

    return PcaResult(
        loadings=loadings_df,
        factor_scores=scores_df,
        variance_share=variance_share_s,
        cumulative_variance_share=cumulative_share_s,
        component_metadata=component_metadata,
        n_observations_in_fit=n_obs,
        n_tenors=n_tenors,
        change_frequency_used=change_frequency,
        sign_anchor_used=sign_anchor,
        fit_window_start=fit_window_start,
        fit_window_end=fit_window_end,
    )

"""fit_ou — Ornstein–Uhlenbeck mean-reversion fit of a Series.

Finance-blind ``statistical_relationship`` operator and the FIRST A4
model-fit engine.  Fits the discrete OU / AR(1) mean-reversion model
(the math lives in ``shared/quant/ou.py``, the third module there — no
library, like variance_ratio/hurst) and emits the HALF-LIFE as a
``ScalarMetric`` in COUNT units (a half-life is a positive count of
observations — the one deliberate divergence from the RATIO-emitting A5
test siblings, whose scalars are dimensionless statistics).  The full
fit — φ, θ, α, the equilibrium μ, the stationary σ_eq, and the Δx-fit
R² (the mean-reversion signal strength) — rides in lineage.

This generalises the rates ``half_life`` primitive to ANY series (a
spread, a residual, a ratio) — finance-blind: the code would not change
for FX or equity inputs.

ONE OUTPUT (OPR2): the half-life ScalarMetric only.  The
z-to-equilibrium SERIES ((x − μ)/σ_eq over time) is a SEPARATE operator
(``ou_zscore``, a declared planned extension) — "Series and
ScalarMetric" would be two output types = two operators (the
correlation vs rolling_correlation doctrine), never a flag.

THE NOT-MEAN-REVERTING REFUSAL (OPR13 / P5 / P12 — the model-fit
honesty crux): mean reversion requires ``0 < φ < 1``.  A unit-root /
trending series (φ ≥ 1) or an oscillatory / explosive one (φ ≤ 0) has
NO finite half-life, so the operator REFUSES rather than emit a garbage
number (a ScalarMetric has no None channel — the stationarity_adf
perfect-ramp-refusal precedent).

THE SIGNIFICANCE REFUSAL (M2 / P2 / P5 — describe-not-forecast, no
half-correct calculation): the SIGN of φ is a POINT ESTIMATE, not a
verdict.  Finite-sample AR(1)-OLS bias pushes β slightly negative on a
true unit root, so a pure random walk reads ``0 < φ < 1`` and yields a
finite half-life ~96% of the time — a confident-looking number for a
series that has no mean reversion.  So the operator ALSO gates on the
Dickey–Fuller unit-root test computed in the quant core (β SE → DF
t-statistic ``β/SE(β)`` → MacKinnon p-value): the half-life is emitted
ONLY when the unit-root null is REJECTED at 5% (``unit_root_rejected``).
A random walk does not reject (median p ≈ 0.51) → REFUSED; a genuine OU
rejects (p ≈ 1e-28) → emitted.  The df_tstat / unit_root_pvalue /
beta_std_err / unit_root_rejected / unit_root_alpha all ride in lineage
(P5).  A merely-SLOW but SIGNIFICANT reversion (φ near 1, a large but
finite half-life, unit root rejected) IS emitted — the φ, R² and the
unit-root stats in lineage disclose the weakness.

DESIGN LOCK (OPR7, lineage-stamped): the AR(1) OLS estimator
(``estimation_method='ar1_ols'``).  Exact-MLE and Kalman-OU are
declared planned extensions.

NaN POLICY (A4/A5 family ruling): full-sample DESCRIPTIVE fit — NaN
rows dropped; because the AR(1) regression is LAG-BASED (x_t vs
x_{t-1}), the interior-drop count is recorded separately in lineage
(``n_interior_dropped``) so a gappy input is auditable, never silent
(the variance_ratio/stationarity_adf precedent).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind classical model fit; zero finance
    vocabulary.
  - OPR2      : ONE output artifact type — always a ``ScalarMetric``
    (the half-life; the z-series is a separate operator).
  - OPR8      : ``params: Optional[...] = None`` (zero knobs; the
    estimator lock documented + stamped).
  - OPR9      : one ``Series`` in, one ``ScalarMetric`` out.
  - OPR10     : one ``OperatorStep``; half-life + φ/θ/α/μ/σ_eq + the
    fit R² + the unit-root significance stats (df_tstat,
    unit_root_pvalue, beta_std_err, unit_root_rejected) + drop counts
    ride in params.
  - OPR11     : COUNT units (a half-life is a count of observations,
    NOT dimensionless); input units recorded in lineage.
  - OPR13     : typed ``FitOuError`` refusals: non-Series input; fewer
    than 12 finite observations; zero variance; NOT mean-reverting by
    point estimate (φ ∉ (0,1) — unit-root/trending/explosive/
    oscillatory); mean reversion NOT statistically significant (the
    Dickey–Fuller unit-root null is not rejected at 5% — a random
    walk); a non-finite half-life.
  - OPR14     : pure + rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.fit_ou.schemas import FitOuParams
from shared.quant.ou import fit_ou_ar1


_OPERATOR_NAME = "fit_ou"
# v2.0.0 (OPR14d behavioural change): the half-life is now gated on a
# unit-root SIGNIFICANCE test (Dickey–Fuller / MacKinnon p-value), not
# only the point-estimate sign of φ.  A series whose unit root cannot be
# rejected (a random walk) is REFUSED rather than emitting a spurious
# "mean-reverting" half-life (M2 / P2 / P5).  The lineage flag is also
# renamed is_mean_reverting → point_estimate_mean_reverting and the
# significance stats (df_tstat, unit_root_pvalue, beta_std_err,
# unit_root_rejected, unit_root_alpha) are added.
_OPERATOR_VERSION = "2.0.0"

# The unit-root rejection level the operator gates on (one-sided
# Dickey–Fuller).  The half-life is only emitted when the unit-root null
# is rejected at this level — the same 5% threshold the quant core and
# stationarity_adf use.
_UNIT_ROOT_ALPHA = 0.05

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# A4/A5 family floor (mirrors variance_ratio / stationarity_adf): the
# AR(1) regression needs headroom for a meaningful fit.
_FAMILY_FLOOR = 12


class FitOuError(ValueError):
    """Raised by ``fit_ou`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def fit_ou(
    series: Series,
    *,
    params: Optional[FitOuParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Half-life of the OU / AR(1) mean-reversion fit of ``series``.

    Parameters
    ----------
    series :
        The input ``Series`` (any units; the half-life is in
        observations).
    params :
        Optional ``FitOuParams`` (no fields in v1).  ``None`` is
        equivalent.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The mean-reversion half-life (steps to close half the gap to
        equilibrium) in COUNT units; φ, θ, α, the equilibrium μ, the
        stationary σ_eq, the fit R², the unit-root significance stats
        (df_tstat, unit_root_pvalue, beta_std_err, unit_root_rejected)
        and the NaN-drop audit ride in lineage.

    Raises
    ------
    FitOuError
        The input is not a ``Series``; fewer than 12 finite
        observations; zero variance; the series is NOT mean-reverting by
        point estimate (φ ∉ (0,1)); mean reversion is NOT statistically
        significant (the Dickey–Fuller unit-root null is not rejected at
        5% — a random walk); or a non-finite half-life.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params — OPR8 (no fields in v1).
    # ------------------------------------------------------------------
    if params is None:
        params = FitOuParams()

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; family floor).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise FitOuError(
            "fit_ou: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    cleaned = payload.dropna()
    n_total = int(len(payload))
    n_obs = int(len(cleaned))
    n_dropped = n_total - n_obs
    if n_obs < _FAMILY_FLOOR:
        raise FitOuError(
            f"fit_ou: needs at least {_FAMILY_FLOOR} finite "
            f"observations (got {n_obs}) — the AR(1) regression is "
            "degenerate below that."
        )

    # Interior-drop audit (A5 ruling): lag-based fit — splicing
    # non-adjacent rows is disclosed, never silent.
    finite_mask = payload.notna().to_numpy()
    first = int(np.argmax(finite_mask))
    last = len(finite_mask) - int(np.argmax(finite_mask[::-1]))
    n_interior_dropped = int((~finite_mask[first:last]).sum())

    values = cleaned.to_numpy(dtype=float)
    if float(np.var(values)) == 0.0:
        raise FitOuError(
            "fit_ou: the input has zero variance — the AR(1) "
            "regression is degenerate on a constant series."
        )

    # ------------------------------------------------------------------
    # 4. The fit (shared/quant; design-locked ar1_ols).
    # ------------------------------------------------------------------
    result = fit_ou_ar1(values)

    # The not-mean-reverting refusal (OPR13/P5): a ScalarMetric has no
    # None channel, so a POINT estimate of φ ∉ (0,1) must REFUSE rather
    # than emit garbage.
    if not result.point_estimate_mean_reverting or result.half_life is None:
        raise FitOuError(
            "fit_ou: the series is NOT mean-reverting "
            f"(phi={result.phi:.6g}; mean reversion requires "
            "0 < phi < 1).  A unit-root / trending series (phi >= 1) "
            "or an oscillatory / explosive one (phi <= 0) has no "
            "finite half-life.  Use stationarity_adf to test the "
            "unit-root null, or variance_ratio for trend-vs-revert "
            "direction."
        )

    # The SIGNIFICANCE refusal (M2 / P2 / P5 — describe-not-forecast).
    # A point estimate of β < 0 is NOT a verdict: finite-sample AR(1)-OLS
    # bias makes a pure random walk read mean-reverting ~96% of the time.
    # Refuse unless the Dickey–Fuller unit-root null is REJECTED at 5% —
    # a half-life on a series we cannot distinguish from a random walk is
    # a half-correct calculation the scope boundary forbids.
    if not result.unit_root_rejected:
        pval_txt = (
            f"{result.unit_root_pvalue:.4g}"
            if result.unit_root_pvalue is not None else "undefined"
        )
        raise FitOuError(
            "fit_ou: mean reversion is NOT statistically significant "
            f"(Dickey-Fuller unit-root p-value={pval_txt} >= "
            f"{_UNIT_ROOT_ALPHA}; df_tstat="
            f"{result.df_tstat:.4g} vs the DF critical region).  The "
            "point estimate phi="
            f"{result.phi:.6g} looks mean-reverting, but finite-sample "
            "OLS bias makes a random walk read that way ~96% of the "
            "time, so the half-life would be spurious.  The unit root "
            "cannot be rejected — this series is statistically "
            "indistinguishable from a random walk.  Use stationarity_adf "
            "for the full unit-root test, or variance_ratio for "
            "trend-vs-revert direction."
        )

    half_life = float(result.half_life)
    if not np.isfinite(half_life):
        raise FitOuError(
            "fit_ou: the fit produced a non-finite half-life "
            "(numerical failure)."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); the full OU fit rides in
    #    params.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "half_life": half_life,
        "phi": float(result.phi),
        "beta": float(result.beta),
        "theta": (
            float(result.theta) if result.theta is not None else None
        ),
        "alpha": float(result.alpha),
        "equilibrium": (
            float(result.mu) if result.mu is not None else None
        ),
        "sigma_eps": float(result.sigma_eps),
        "sigma_eq": (
            float(result.sigma_eq) if result.sigma_eq is not None else None
        ),
        "r_squared": (
            float(result.r_squared)
            if result.r_squared is not None else None
        ),
        # Point-estimate sign (NOT a verdict — renamed per M2 so a
        # consumer cannot misread it as significance).
        "point_estimate_mean_reverting": bool(
            result.point_estimate_mean_reverting
        ),
        # Unit-root SIGNIFICANCE (the M2 honesty surface, P5): the
        # half-life is emitted ONLY because this null was rejected.
        "beta_std_err": (
            float(result.beta_std_err)
            if result.beta_std_err is not None else None
        ),
        "df_tstat": (
            float(result.df_tstat)
            if result.df_tstat is not None else None
        ),
        "unit_root_pvalue": (
            float(result.unit_root_pvalue)
            if result.unit_root_pvalue is not None else None
        ),
        "unit_root_rejected": bool(result.unit_root_rejected),
        "unit_root_alpha": _UNIT_ROOT_ALPHA,
        "unit_root_test": "dickey_fuller_mackinnon",  # design-locked
        "estimation_method": "ar1_ols",  # design-locked (OPR7)
        "test_scope": "full_sample",     # descriptive disclosure (P5)
        "n_obs": n_obs,
        "n_dropped": n_dropped,
        "n_interior_dropped": n_interior_dropped,
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )

    return ScalarMetric(
        metric_key=f"fit_ou__{series.series_key}",
        value=half_life,
        units=TimeSeriesUnits.COUNT,
        lineage=series.lineage.append(step),
    )


__all__ = ["fit_ou", "FitOuError"]

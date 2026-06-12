"""variance_ratio — Lo–MacKinlay variance-ratio test of a Series.

Finance-blind ``statistical_relationship`` operator (the A5
diagnostics family — the series' relationship with its own past;
``stationarity_adf``/``ljung_box`` set the family conventions).  The
FIRST A5 test whose math has no library: the three siblings each import
a statsmodels function, but no ``arch``/statsmodels variance-ratio test
exists here, so the Lo–MacKinlay (1988) numerics live in
``shared/quant/variance_ratio.py`` (the operator wraps them behind the
typed-artifact signature).

Runs the variance-ratio test on one Series and emits the standardised
TEST STATISTIC (``z``) as a ``ScalarMetric`` in RATIO units — the A5
family's emit-the-statistic convention (ADF emits its t-stat, Ljung–Box
its Q, JB its χ²; here the z with the N(0,1) null).  The variance ratio
VR(q) itself — the interpretable effect size: 1 ⇒ random walk, > 1
trending, < 1 mean-reverting — rides in lineage alongside the p-value.

DESIGN LOCK (OPR7, lineage-stamped): OVERLAPPING q-period differences
with the Lo–MacKinlay bias-correction ``m = q(T−q+1)(1 − q/T)`` is the
single canonical estimator.  Non-overlapping is a distinct, weaker
estimator that fractures replay comparability for no analytical gain at
this surface — a declared planned extension, never a silent knob.

KNOBS (OPR8 — the first knob-bearing A5 test): ``q`` (horizon;
None→2, YAML-authoritative) and ``robust`` (the heteroskedasticity-
consistent M2 vs homoskedastic M1 standardisation; schema-default
True).  Both selections are lineage-stamped.

NaN POLICY (A5 family ruling): full-sample DESCRIPTIVE test — NaN rows
dropped; because the q-period differences are LAG-BASED, the
interior-drop count is recorded separately in lineage
(``n_interior_dropped``) so a gappy input is auditable, never silent
(the stationarity_adf/ljung_box precedent).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind classical test; zero finance
    vocabulary.
  - OPR2      : ONE output artifact type — always a ``ScalarMetric``
    (the z-statistic; never VR for some inputs and z for others).
  - OPR8      : ``params: Optional[...] = None``; ``q=None`` resolves
    to ``2`` at runtime (lineage-stamped, never silent); ``robust``
    is an explicit typed knob.
  - OPR9      : one ``Series`` in, one ``ScalarMetric`` out.
  - OPR10     : one ``OperatorStep``; the z-statistic + VR(q) +
    p-value + resolved q + robust flag + the locked overlapping/bias
    spec + drop counts ride in params.
  - OPR11     : RATIO units (a standardised statistic is
    dimensionless); input units recorded in lineage.
  - OPR13     : typed ``VarianceRatioError`` refusals: non-Series
    input; fewer than ``max(12, q + 2)`` finite observations (the A5
    family floor OR the horizon floor — whichever binds); zero
    variance (constant series); zero first-difference variance (a
    perfect ramp — the VR denominator is degenerate); a non-finite
    statistic.
  - OPR14     : pure + rerun-deterministic; the RESOLVED q is what
    rides in lineage (params q=None and an explicit equal value hash
    identically).
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
from shared.operators.variance_ratio.schemas import VarianceRatioParams
from shared.quant.variance_ratio import variance_ratio_test


_OPERATOR_NAME = "variance_ratio"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# The minimal-horizon default (A5 family ruling), applied when q is
# omitted: the smallest valid aggregation horizon — the most basic
# random-walk check.  The caller pins a longer horizon explicitly.
_DEFAULT_Q = 2

# A5 family floor (mirrors stationarity_adf's 12) OR'd with the horizon
# floor q + 2 (below which the overlapping estimator collapses — m <= 0).
_FAMILY_FLOOR = 12


class VarianceRatioError(ValueError):
    """Raised by ``variance_ratio`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def variance_ratio(
    series: Series,
    *,
    params: Optional[VarianceRatioParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Lo–MacKinlay variance-ratio z-statistic of ``series``.

    Parameters
    ----------
    series :
        The input ``Series`` (any units; the statistic is
        dimensionless).
    params :
        Optional ``VarianceRatioParams``.  When ``None`` (or when
        ``q=None``) the horizon resolves to ``2``; ``robust`` defaults
        to ``True``.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The standardised test statistic ``z`` (asymptotically N(0,1)
        under the random-walk null) in RATIO units; the variance ratio
        VR(q), the p-value, the resolved horizon, the robust flag and
        the NaN-drop audit ride in lineage.

    Raises
    ------
    VarianceRatioError
        The input is not a ``Series``; fewer than ``max(12, q + 2)``
        finite observations; zero variance (constant series) or zero
        first-difference variance (perfect ramp); or a non-finite
        statistic.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params from config when omitted — OPR8 (q is
    #    YAML-authoritative; robust is the schema default).
    # ------------------------------------------------------------------
    if params is None:
        raw_q = config.default_value("q")
        params = VarianceRatioParams(
            q=int(raw_q) if raw_q is not None else None,
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; family floor).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise VarianceRatioError(
            "variance_ratio: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    # Resolve the horizon: explicit value, or the minimal-horizon
    # default.  Only the RESOLVED value rides in lineage (OPR14 — the
    # ljung_box lags None→rule precedent): an auto-resolved horizon and
    # an explicit equal value produce byte-identical outputs.
    q = int(params.q) if params.q is not None else _DEFAULT_Q
    if q < 2:  # defensive — schema enforces ge=2, but a resolved q must hold too
        raise VarianceRatioError(
            f"variance_ratio: the horizon q must be >= 2 (resolved {q})."
        )
    robust = bool(params.robust)

    payload = series.payload
    cleaned = payload.dropna()
    n_total = int(len(payload))
    n_obs = int(len(cleaned))
    n_dropped = n_total - n_obs

    min_obs = max(_FAMILY_FLOOR, q + 2)
    if n_obs < min_obs:
        raise VarianceRatioError(
            f"variance_ratio: needs at least max(12, q + 2) = {min_obs} "
            f"finite observations for horizon q={q} (got {n_obs}).  "
            "Lower q or widen the input lookback."
        )

    # Interior-drop audit (A5 ruling): lag-based test — splicing
    # non-adjacent rows is disclosed, never silent.
    finite_mask = payload.notna().to_numpy()
    first = int(np.argmax(finite_mask))
    last = len(finite_mask) - int(np.argmax(finite_mask[::-1]))
    n_interior_dropped = int((~finite_mask[first:last]).sum())

    values = cleaned.to_numpy(dtype=float)
    if float(np.var(values)) == 0.0:
        raise VarianceRatioError(
            "variance_ratio: the input has zero variance — the "
            "variance ratio is undefined on a constant series."
        )
    # Degenerate-denominator invariant (OPR14 — the stationarity_adf
    # perfect-ramp precedent): constant first differences (a perfect
    # linear ramp) make the one-period variance zero, so VR's
    # denominator collapses and the statistic would be float noise.
    if float(np.var(np.diff(values))) == 0.0:
        raise VarianceRatioError(
            "variance_ratio: the input's first differences have zero "
            "variance (a perfect linear ramp) — the variance ratio's "
            "denominator is degenerate and its statistic would be "
            "float noise."
        )

    # ------------------------------------------------------------------
    # 4. The test (shared/quant; design-locked overlapping+bias spec).
    # ------------------------------------------------------------------
    result = variance_ratio_test(values, q, robust=robust)
    stat = float(result.z_statistic)
    if not np.isfinite(stat):
        raise VarianceRatioError(
            "variance_ratio: the test produced a non-finite statistic "
            "(numerical failure)."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10).  The RESOLVED q rides in
    #    params (OPR14: q=None and an explicit equal value hash
    #    identically); VR(q) is the effect size in lineage.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "test_statistic": stat,
        "variance_ratio": float(result.vr),
        "p_value": (
            float(result.p_value) if np.isfinite(result.p_value) else None
        ),
        "q": q,
        "robust": robust,
        "overlapping": True,      # design-locked (OPR7)
        "bias_corrected": True,   # design-locked (OPR7)
        "m": float(result.m),
        "var_1": float(result.var_1),
        "var_q": float(result.var_q),
        "test_scope": "full_sample",  # descriptive disclosure (P5)
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
        metric_key=f"variance_ratio__{series.series_key}",
        value=stat,
        units=TimeSeriesUnits.RATIO,
        lineage=series.lineage.append(step),
    )


__all__ = ["variance_ratio", "VarianceRatioError"]

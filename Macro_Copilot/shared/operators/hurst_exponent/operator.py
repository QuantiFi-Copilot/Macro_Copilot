"""hurst_exponent — the Hurst exponent of a Series via rescaled-range.

Finance-blind ``statistical_relationship`` operator (the A5
diagnostics family — the series' relationship with its own past;
stationarity_adf/variance_ratio are the trending-vs-mean-reverting
siblings).  Like variance_ratio, the math has no library in this env,
so the classical R/S numerics live in ``shared/quant/hurst.py`` (the
second module there); the operator wraps them.

Emits the Anis–Lloyd-corrected HURST EXPONENT ``H`` as a
``ScalarMetric`` in RATIO units.  Unlike the test siblings (ADF/Q/JB/VR
emit a standardised test STATISTIC with a null distribution), classical
R/S has no clean per-call null — the diagnostic IS the estimate, so H
itself is emitted:

  - H ≈ 0.5 — no long memory (random-walk-like increments).
  - H > 0.5 — PERSISTENT / long memory (trending).
  - H < 0.5 — ANTI-PERSISTENT (mean-reverting).

OPERATES ON THE SERIES AS GIVEN — it does NOT difference internally
(stamped ``differenced=False`` in lineage).  To characterise a
price/level series for mean-reversion, difference it first (the
``series_arithmetic(diff) -> hurst_exponent`` pattern, exactly as
ljung_box/normality_test document) so a random walk maps to H ≈ 0.5.

DESIGN LOCKS (OPR7, lineage-stamped): (1) classical rescaled-range
(Hurst 1951 / Mandelbrot–Wallis 1969) — DFA / aggregated-variance /
periodogram are declared planned extensions, never silent knobs.
(2) the Anis–Lloyd (1976) / Peters (1994) small-sample correction —
uncorrected R/S overestimates H at finite N (a bias straddling the
H=0.5 decision boundary), so the canonical estimate subtracts the
theoretical expected R/S under the iid null; the raw slope rides
alongside as ``h_uncorrected``.  (3) a geometric scale set from
min_window=8 to N//2 — a caller-chosen scale rule is a planned
extension.

NaN POLICY (A5 family ruling): full-sample DESCRIPTIVE estimate — NaN
rows dropped; because R/S accumulates deviations within CONTIGUOUS
windows (order-sensitive), the interior-drop count is recorded
separately in lineage (``n_interior_dropped``) so a gappy input is
auditable, never silent (the stationarity_adf/variance_ratio
precedent).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind classical estimator; zero finance
    vocabulary.
  - OPR2      : ONE output artifact type — always a ``ScalarMetric``
    (the Hurst exponent H).
  - OPR8      : ``params: Optional[...] = None`` (zero knobs; the
    estimator/correction/scale-set locks documented + stamped).
  - OPR9      : one ``Series`` in, one ``ScalarMetric`` out.
  - OPR10     : one ``OperatorStep``; H + the uncorrected slope + the
    fit R² + the scale set + per-scale log(R/S) + the locks + drop
    counts ride in params.
  - OPR11     : RATIO units (a scaling exponent is dimensionless);
    input units recorded in lineage.
  - OPR13     : typed ``HurstExponentError`` refusals: non-Series
    input; fewer than 128 finite observations (R/S is meaningless and
    sharply biased below that); zero variance (constant series —
    every block's S is zero); too few usable scales; a non-finite or
    out-of-band H (a collapsed fit — refused, never silently clamped).
  - OPR14     : pure + rerun-deterministic; the scale set is a pure
    function of N.
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
from shared.operators.hurst_exponent.schemas import HurstExponentParams
from shared.quant.hurst import hurst_rs


_OPERATOR_NAME = "hurst_exponent"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# Family floor: rescaled-range analysis is meaningless and sharply
# biased below this (mirrors the shared/quant floor — far above ADF's
# 12 / VR's max(12, q+2); R/S needs several distinct scales each with
# several windows).
_MIN_OBS = 128

# A collapsed log-log fit can produce a wild slope; H outside this
# generous band means the estimate is float noise — refuse (never
# silently clamp, OPR13/P5).
_H_MIN = -0.5
_H_MAX = 1.5


class HurstExponentError(ValueError):
    """Raised by ``hurst_exponent`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def hurst_exponent(
    series: Series,
    *,
    params: Optional[HurstExponentParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Anis–Lloyd-corrected Hurst exponent of ``series`` via R/S.

    Parameters
    ----------
    series :
        The input ``Series`` (any units; the exponent is
        dimensionless).
    params :
        Optional ``HurstExponentParams`` (no fields in v1).  ``None``
        is equivalent.
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The Hurst exponent H (≈ 0.5 random-walk-like; > 0.5 persistent;
        < 0.5 mean-reverting) in RATIO units; the uncorrected slope,
        the fit R², the scale set, the per-scale log(R/S) and the
        NaN-drop audit ride in lineage.

    Raises
    ------
    HurstExponentError
        The input is not a ``Series``; fewer than 128 finite
        observations; zero variance; too few usable scales; or a
        non-finite / out-of-band H.
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
        params = HurstExponentParams()

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; family floor).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise HurstExponentError(
            "hurst_exponent: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    payload = series.payload
    cleaned = payload.dropna()
    n_total = int(len(payload))
    n_obs = int(len(cleaned))
    n_dropped = n_total - n_obs
    if n_obs < _MIN_OBS:
        raise HurstExponentError(
            f"hurst_exponent: needs at least {_MIN_OBS} finite "
            f"observations (got {n_obs}) — rescaled-range analysis is "
            "meaningless and sharply biased on shorter series."
        )

    # Interior-drop audit (A5 ruling): R/S accumulates within
    # contiguous windows — splicing non-adjacent rows is disclosed.
    finite_mask = payload.notna().to_numpy()
    first = int(np.argmax(finite_mask))
    last = len(finite_mask) - int(np.argmax(finite_mask[::-1]))
    n_interior_dropped = int((~finite_mask[first:last]).sum())

    values = cleaned.to_numpy(dtype=float)
    if float(np.var(values)) == 0.0:
        raise HurstExponentError(
            "hurst_exponent: the input has zero variance — the "
            "rescaled range is undefined on a constant series."
        )

    # ------------------------------------------------------------------
    # 4. The estimate (shared/quant; design-locked classical R/S +
    #    Anis–Lloyd correction + geometric scales).
    # ------------------------------------------------------------------
    try:
        result = hurst_rs(values)
    except ValueError as exc:  # too-few-scales / degenerate — typed refusal
        raise HurstExponentError(f"hurst_exponent: {exc}") from exc

    h = float(result.h)
    if not np.isfinite(h) or not (_H_MIN <= h <= _H_MAX):
        raise HurstExponentError(
            "hurst_exponent: the estimate "
            f"({h}) is non-finite or outside the sane band "
            f"[{_H_MIN}, {_H_MAX}] — the log-log fit collapsed "
            "(degenerate input); the value would be float noise."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10); the estimate, the
    #    uncorrected slope, the fit quality and the scale trail ride in
    #    params with the design-locks NAMED.
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "test_statistic": h,  # the emitted value (the Hurst exponent)
        "hurst_exponent": h,
        "h_uncorrected": float(result.h_uncorrected),
        "r_squared": float(result.r_squared),
        "estimator": "classical_rs",      # design-locked (OPR7)
        "correction": result.correction,  # "anis_lloyd" (OPR7)
        "differenced": False,             # operates on the series as given
        "n_scales": int(result.n_scales),
        "scales": list(result.scales),
        "log_rs": list(result.log_rs),
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
        metric_key=f"hurst_exponent__{series.series_key}",
        value=h,
        units=TimeSeriesUnits.RATIO,
        lineage=series.lineage.append(step),
    )


__all__ = ["hurst_exponent", "HurstExponentError"]

"""beta — full-sample OLS slope of one Series on another.

Finance-blind ``statistical_relationship`` operator.  Fits a single
full-sample OLS line lhs = α + β·rhs (+ ε) over the overlapping
non-NaN observations and emits the slope β as ONE ``ScalarMetric`` —
the single sensitivity number ("how much does lhs move per unit of
rhs").  The scalar counterpart of the ``rolling_regression`` beta path
(per OPR2 a scalar-emitting method is its own operator), and the
coefficient-flavoured sibling of ``regression_residual`` (which emits
the residual Series from the same fit).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method ("beta" is the
    standard statistics name for an OLS slope coefficient); zero
    finance vocabulary or math.
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``ScalarMetric``.  The windowed coefficient paths are the
    separate ``rolling_regression`` operator.
  - OPR4      : NOT a trivial composition — no existing chain produces
    a full-sample slope ScalarMetric (``rolling_regression`` emits
    windowed Series paths; ``regression_residual`` records β only as
    lineage metadata, not as an artifact).
  - OPR8      : ``params: Optional[BetaParams] = None``; every default
    resolves from ``config.yaml`` when omitted; schema defaults mirror
    the YAML.
  - OPR9      : two ``Series`` in, one ``ScalarMetric`` out (closed
    family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    the rhs chain rides in ``auxiliary_lineages``; the fitted
    α / R² / n_obs (and β itself) are recorded as lineage diagnostics.
  - OPR11     : strict-by-default on frequency + missingness.
    Unit-INVARIANT across inputs (β absorbs the regressor's units, so
    regressing PERCENT on BPS is meaningful); the slope's semantic
    dimension is lhs_units / rhs_units, which the closed enum cannot
    express — output tagged ``RATIO`` with both inputs' true units
    recorded in lineage (the ``rolling_regression``-beta precedent).
  - OPR13     : every recoverable failure raises ``BetaError`` (a
    ``ValueError`` subclass): non-Series inputs, index mismatch,
    strict-mode metadata mismatch, insufficient overlap, zero-variance
    rhs (undefined slope), ill-conditioned design matrix, or a
    non-finite result.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic (same inputs → same ``head_hash``).

NOT commutative: β(lhs, rhs) is the slope of lhs on rhs; swapping the
arms fits a different line.

Composition contract: ``beta`` does NOT align.  The two Series must
already share an identical ``DatetimeIndex`` — align upstream with
``align_series``.
"""

from __future__ import annotations

import json
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
from shared.operators.beta.schemas import BetaParams


_OPERATOR_NAME = "beta"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class BetaError(ValueError):
    """Raised by ``beta`` on a recoverable user-facing failure (a
    ``ValueError`` subclass, OPR13)."""


def beta(
    lhs: Series,
    rhs: Series,
    *,
    params: Optional[BetaParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Fit lhs = α + β·rhs over the overlap and return β.

    Parameters
    ----------
    lhs :
        Dependent ``Series`` — the y in y = α + βx + ε.  NOT
        interchangeable with ``rhs`` (swapping fits a different line).
    rhs :
        The single explanatory ``Series``.  Must share an identical
        ``DatetimeIndex`` with ``lhs`` (align upstream with
        ``align_series``).
    params :
        Optional ``BetaParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The slope β, tagged ``RATIO`` (its semantic dimension —
        lhs_units / rhs_units — is recorded in lineage), with the
        fitted α, R² and n_obs recorded as lineage diagnostics.

    Raises
    ------
    BetaError
        Inputs are not ``Series``; indexes differ; frequency or
        missingness mismatch under strict mode; fewer than
        ``min_periods`` overlapping non-NaN observations; the rhs has
        zero variance over the overlap (undefined slope); the design
        matrix is ill-conditioned; or the result is non-finite.
    """
    # ------------------------------------------------------------------
    # 1. Load config + identity check — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params from config when omitted — OPR8.
    # ------------------------------------------------------------------
    if params is None:
        params = BetaParams(
            add_constant=bool(config.default_value("add_constant")),
            min_periods=int(config.default_value("min_periods")),
            condition_number_threshold=float(
                config.default_value("condition_number_threshold")
            ),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 metadata).
    # ------------------------------------------------------------------
    if not isinstance(lhs, Series) or not isinstance(rhs, Series):
        raise BetaError(
            "beta: both inputs must be Series artifacts; got "
            f"lhs={type(lhs).__name__}, rhs={type(rhs).__name__}."
        )

    # beta does NOT align — that is align_series's job.
    if not lhs.payload.index.equals(rhs.payload.index):
        raise BetaError(
            "beta: lhs and rhs Series must share an identical "
            "DatetimeIndex.  Align upstream with align_series first "
            f"(lhs.len={len(lhs.payload)}, rhs.len={len(rhs.payload)})."
        )

    # Frequency — strict by default (OPR11).
    if params.require_matching_frequency and lhs.frequency != rhs.frequency:
        raise BetaError(
            f"beta: incompatible frequencies lhs={lhs.frequency!r} vs "
            f"rhs={rhs.frequency!r}.  Pass require_matching_frequency="
            "False to opt into a mixed-frequency fit explicitly."
        )

    # Missingness — strict by default (OPR11).
    if params.require_matching_missingness:
        lhs_sig = json.dumps(
            lhs.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        rhs_sig = json.dumps(
            rhs.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        if lhs_sig != rhs_sig:
            raise BetaError(
                "beta: incompatible missingness policies lhs vs rhs.  "
                "Pass require_matching_missingness=False to opt into "
                "mixed policies explicitly."
            )

    # NOTE (OPR11): unit-INVARIANT across the inputs — β absorbs the
    # rhs's units (its semantic dimension is lhs_units / rhs_units).
    # Both units are recorded in lineage; the output tag is RATIO.

    # ------------------------------------------------------------------
    # 4. Fit on the overlapping non-NaN pairs.
    # ------------------------------------------------------------------
    y_full = lhs.payload.to_numpy(dtype=float)
    x_full = rhs.payload.to_numpy(dtype=float)
    mask = np.isfinite(y_full) & np.isfinite(x_full)
    n_obs = int(mask.sum())
    if n_obs < params.min_periods:
        raise BetaError(
            f"beta: only {n_obs} overlapping non-NaN observation(s); "
            f"min_periods={params.min_periods} required."
        )

    x = x_full[mask]
    y = y_full[mask]

    if np.nanmax(x) - np.nanmin(x) == 0.0:
        raise BetaError(
            "beta: rhs has zero variance over the overlap — the "
            "regression slope is undefined.  Provide a non-constant "
            "regressor."
        )

    if params.add_constant:
        design = np.column_stack([np.ones_like(x), x])
    else:
        design = x.reshape(-1, 1)

    cond = float(np.linalg.cond(design))
    if cond > params.condition_number_threshold:
        raise BetaError(
            f"beta: design-matrix condition number {cond:.3e} exceeds "
            f"the threshold {params.condition_number_threshold:.1e}; "
            "the fit is numerically meaningless (near-constant rhs?)."
        )

    coef, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    if params.add_constant:
        alpha = float(coef[0])
        slope = float(coef[1])
    else:
        alpha = 0.0
        slope = float(coef[0])

    if not np.isfinite(slope):
        raise BetaError(
            "beta: the fitted slope is non-finite; the inputs are "
            "numerically degenerate."
        )

    fitted = alpha + slope * x
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = (1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan")

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep; rhs chain in auxiliary_lineages;
    #    fitted-line diagnostics recorded; params sanitised (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "add_constant": params.add_constant,
        "min_periods": params.min_periods,
        "condition_number_threshold": params.condition_number_threshold,
        "alpha": alpha,
        "beta": slope,
        "r_squared": r_squared,  # NaN → None via the sanitiser
        "n_obs": n_obs,
        "lhs_units": lhs.units.value,
        "rhs_units": rhs.units.value,
        "lhs_series_key": lhs.series_key,
        "rhs_series_key": rhs.series_key,
        "require_matching_frequency": params.require_matching_frequency,
        "require_matching_missingness": params.require_matching_missingness,
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(lhs.lineage.head_hash, rhs.lineage.head_hash),
        auxiliary_lineages=(rhs.lineage,),
    )
    out_lineage = lhs.lineage.append(op_step)

    return ScalarMetric(
        metric_key=f"beta__{lhs.series_key}__{rhs.series_key}",
        value=slope,
        units=TimeSeriesUnits.RATIO,
        lineage=out_lineage,
    )


__all__ = ["beta", "BetaError"]

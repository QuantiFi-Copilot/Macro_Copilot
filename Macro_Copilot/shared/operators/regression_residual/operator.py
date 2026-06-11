"""regression_residual — full-sample OLS residual of one Series on another.

Finance-blind ``statistical_relationship`` operator.  Fits a single
full-sample OLS line lhs = α + β·rhs (+ ε) over the overlapping
non-NaN observations and emits the residual series
``lhs_t − (α + β·rhs_t)`` — the per-date distance from the fitted
line.  The full-sample counterpart of ``rolling_regression`` for the
single number-pair case, returning the residual (the quantity research
DAGs consume) rather than the coefficient paths.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; zero finance
    vocabulary or math.
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``Series`` of residuals.
  - OPR8      : ``params: Optional[RegressionResidualParams] = None``;
    every default resolves from ``config.yaml`` when omitted; schema
    defaults mirror the YAML.
  - OPR9      : two ``Series`` in, one ``Series`` out (closed family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    the rhs chain rides in ``auxiliary_lineages``; params pass through
    ``sanitize_params_for_lineage``; the fitted α / β / R² / n_obs are
    recorded as lineage diagnostics so the fitted line is auditable.
  - OPR11     : strict-by-default on frequency + missingness.
    Unit-INVARIANT across inputs (β absorbs the regressor's units, so
    regressing PERCENT on BPS is meaningful — like ``correlation``,
    no same-unit requirement); the residual lives in the LHS's units,
    so the output unit is an honest LHS passthrough (the
    ``rolling_regression`` alpha-in-lhs-units precedent), with both
    inputs' units recorded in lineage.
  - OPR13     : every recoverable failure raises
    ``RegressionResidualError`` (a ``ValueError`` subclass): non-Series
    inputs, index mismatch, strict-mode metadata mismatch, fewer than
    ``min_periods`` overlapping observations, a zero-variance rhs
    (undefined slope), an ill-conditioned design matrix, or a
    non-finite residual (overflow).
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic (same inputs → same ``head_hash``).

NOT commutative: swapping lhs and rhs fits a different line.  ``lhs``
is the dependent series (the quantity whose richness/cheapness vs the
fitted line you want); ``rhs`` is the single explanatory series.

Composition contract: ``regression_residual`` does NOT align.  The two
Series must already share an identical ``DatetimeIndex`` — align
upstream with ``align_series``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.missingness import CombinedMissingnessV1
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.regression_residual.schemas import (
    RegressionResidualParams,
)


_OPERATOR_NAME = "regression_residual"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class RegressionResidualError(ValueError):
    """Raised by ``regression_residual`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def regression_residual(
    lhs: Series,
    rhs: Series,
    *,
    params: Optional[RegressionResidualParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Fit lhs = α + β·rhs over the overlap and return the residuals.

    Parameters
    ----------
    lhs :
        Dependent ``Series`` — the quantity whose distance from the
        fitted line the residual measures.  The output lives in THIS
        series's units.
    rhs :
        The single explanatory ``Series``.  NOT interchangeable with
        ``lhs`` (swapping fits a different line).  Must share an
        identical ``DatetimeIndex`` with ``lhs`` (align upstream with
        ``align_series``).
    params :
        Optional ``RegressionResidualParams``.  When ``None`` every
        field is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        Residuals on the shared input index (NaN exactly where either
        input is NaN), in ``lhs.units``; frequency inherited when both
        inputs agree (else ``None``); missingness propagated (or
        ``CombinedMissingnessV1`` under a lenient opt-out); lineage
        extended by one ``OperatorStep`` recording α, β, R², n_obs.

    Raises
    ------
    RegressionResidualError
        Inputs are not ``Series``; indexes differ; frequency or
        missingness mismatch under strict mode; fewer than
        ``min_periods`` overlapping non-NaN observations; the rhs has
        zero variance over the overlap (undefined slope); the design
        matrix is ill-conditioned; or a residual overflows the float
        range.
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
        params = RegressionResidualParams(
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
        raise RegressionResidualError(
            "regression_residual: both inputs must be Series artifacts; "
            f"got lhs={type(lhs).__name__}, rhs={type(rhs).__name__}."
        )

    # regression_residual does NOT align — that is align_series's job.
    if not lhs.payload.index.equals(rhs.payload.index):
        raise RegressionResidualError(
            "regression_residual: lhs and rhs Series must share an "
            "identical DatetimeIndex.  Align upstream with align_series "
            f"first (lhs.len={len(lhs.payload)}, "
            f"rhs.len={len(rhs.payload)})."
        )

    # Frequency — strict by default (OPR11).
    if params.require_matching_frequency and lhs.frequency != rhs.frequency:
        raise RegressionResidualError(
            f"regression_residual: incompatible frequencies "
            f"lhs={lhs.frequency!r} vs rhs={rhs.frequency!r}.  Pass "
            "require_matching_frequency=False to opt into a "
            "mixed-frequency regression explicitly."
        )

    # Missingness — strict by default (OPR11).  JSON-canonical compare
    # handles flat AND nested policies (matches the sibling operators).
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
            raise RegressionResidualError(
                "regression_residual: incompatible missingness policies "
                "lhs vs rhs.  Pass require_matching_missingness=False to "
                "opt into mixed policies explicitly."
            )

    # NOTE (OPR11): unit-INVARIANT across the inputs — β absorbs the
    # rhs's units, so a mixed-unit regression is meaningful.  Both
    # units are recorded in lineage; the residual is in lhs.units.

    # ------------------------------------------------------------------
    # 4. Fit on the overlapping non-NaN pairs.
    # ------------------------------------------------------------------
    y_full = lhs.payload.to_numpy(dtype=float)
    x_full = rhs.payload.to_numpy(dtype=float)
    mask = np.isfinite(y_full) & np.isfinite(x_full)
    n_obs = int(mask.sum())
    if n_obs < params.min_periods:
        raise RegressionResidualError(
            f"regression_residual: only {n_obs} overlapping non-NaN "
            f"observation(s); min_periods={params.min_periods} required."
        )

    x = x_full[mask]
    y = y_full[mask]

    # Zero-variance rhs ⇒ undefined slope ⇒ typed refusal (the
    # regression line is not identified; silently emitting lhs − ȳ
    # would be a different computation under this operator's name).
    if np.nanmax(x) - np.nanmin(x) == 0.0:
        raise RegressionResidualError(
            "regression_residual: rhs has zero variance over the "
            "overlap — the regression slope is undefined.  Provide a "
            "non-constant regressor."
        )

    if params.add_constant:
        design = np.column_stack([np.ones_like(x), x])
    else:
        design = x.reshape(-1, 1)

    cond = float(np.linalg.cond(design))
    if cond > params.condition_number_threshold:
        raise RegressionResidualError(
            f"regression_residual: design-matrix condition number "
            f"{cond:.3e} exceeds the threshold "
            f"{params.condition_number_threshold:.1e}; the fit is "
            "numerically meaningless (near-constant rhs?)."
        )

    coef, _, _, _ = np.linalg.lstsq(design, y, rcond=None)
    if params.add_constant:
        alpha = float(coef[0])
        beta = float(coef[1])
    else:
        alpha = 0.0
        beta = float(coef[0])

    # R² on the fitted overlap (guard the zero-variance-lhs case: SS_tot
    # = 0 makes R² undefined → record None via the lineage sanitiser).
    fitted_masked = alpha + beta * x
    ss_res = float(np.sum((y - fitted_masked) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r_squared = (1.0 - ss_res / ss_tot) if ss_tot > 0.0 else float("nan")

    # Residuals on the FULL shared index: NaN exactly where either
    # input is NaN (pairwise missingness), values elsewhere.
    residual_values = y_full - (alpha + beta * x_full)
    residual_values[~mask] = np.nan
    if np.isinf(residual_values[mask]).any():
        raise RegressionResidualError(
            "regression_residual: a residual overflows the float "
            "range; the inputs are numerically degenerate."
        )

    result = lhs.payload.copy()
    result[:] = residual_values
    result.name = f"residual__{lhs.series_key}__{rhs.series_key}"

    # ------------------------------------------------------------------
    # 5. Combine missingness honestly when policies disagree under a
    #    lenient opt-out (OPR11); preserve frequency only on agreement.
    # ------------------------------------------------------------------
    if lhs.missingness_policy == rhs.missingness_policy:
        out_missingness = lhs.missingness_policy
    else:
        out_missingness = CombinedMissingnessV1(
            components=(lhs.missingness_policy, rhs.missingness_policy),
        )

    out_frequency = lhs.frequency if lhs.frequency == rhs.frequency else None

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep; rhs chain in auxiliary_lineages;
    #    fitted-line diagnostics recorded; params sanitised (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "add_constant": params.add_constant,
        "min_periods": params.min_periods,
        "condition_number_threshold": params.condition_number_threshold,
        "alpha": alpha,
        "beta": beta,
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

    return Series(
        series_key=f"residual__{lhs.series_key}__{rhs.series_key}",
        payload=result,
        units=lhs.units,
        frequency=out_frequency,
        missingness_policy=out_missingness,
        lineage=out_lineage,
    )


__all__ = ["regression_residual", "RegressionResidualError"]

"""covariance — full-sample covariance between two typed Series.

Finance-blind ``statistical_relationship`` operator (sibling of the
``correlation`` v2.0 reference).  Computes the single full-sample
covariance number between two index-aligned Series; the rolling variant
is a SEPARATE operator (``rolling_covariance``) per OPR2.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; zero finance
    vocabulary or math.
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``ScalarMetric``.
  - OPR8      : ``params: Optional[CovarianceParams] = None``; ``ddof``
    and ``min_periods`` resolve from ``config.yaml`` when omitted.
  - OPR9      : two ``Series`` in, one ``ScalarMetric`` out (closed
    family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    the right operand's chain rides in ``auxiliary_lineages``; params
    pass through ``sanitize_params_for_lineage``.
  - OPR11     : strict-by-default on units + frequency + missingness.
    Unlike ``correlation`` (unit-invariant), a covariance is
    unit-BEARING: its semantic unit is the PRODUCT of the two inputs'
    units, which the closed ``TimeSeriesUnits`` enum cannot express.
    Following the live precedent (``rolling_regression`` beta,
    ``cointegration`` ADF stat), the output is tagged ``RATIO`` and
    BOTH inputs' units are recorded in lineage params — the honest,
    auditable record of the true dimension.  Mixed-unit inputs are
    refused by default (``require_matching_units=True``); the explicit
    opt-out is recorded in lineage.  Same-unit reconciliation belongs
    upstream in ``convert_units`` (the sole unit-transition site).
  - OPR13     : every recoverable failure raises ``CovarianceError`` (a
    ``ValueError`` subclass).  NOTE the deliberate divergence from
    ``correlation``: a zero-variance (constant) input is NOT an error —
    the covariance with a constant series is mathematically defined and
    equals 0.0.  Only a non-finite result (overflow) is refused.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic (same inputs → same ``head_hash``).

Composition contract: ``covariance`` does NOT align.  The two Series
must already share an identical ``DatetimeIndex`` — align upstream with
``align_series``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import ScalarMetric, Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.covariance.schemas import CovarianceParams


_OPERATOR_NAME = "covariance"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class CovarianceError(ValueError):
    """Raised by ``covariance`` on a recoverable user-facing failure.

    Subclass of ``ValueError`` (OPR13) so the rest of the substrate's
    ``except ValueError`` handling continues to work; the transport
    boundary converts it to the user-facing error envelope.
    """


def covariance(
    left: Series,
    right: Series,
    *,
    params: Optional[CovarianceParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Compute the full-sample covariance between ``left`` and ``right``.

    Parameters
    ----------
    left, right :
        The two ``Series`` artifacts.  They MUST share an identical
        ``DatetimeIndex`` (align upstream with ``align_series``) and,
        by default, the same units (reconcile upstream with
        ``convert_units``).
    params :
        Optional ``CovarianceParams``.  When ``None`` every YAML-backed
        field is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The covariance, tagged ``RATIO`` (the closed enum cannot
        express a product unit; both inputs' units are recorded in
        lineage params — see the module docstring), carrying the
        lineage of both inputs plus this operator step.

    Raises
    ------
    CovarianceError
        Inputs are not ``Series``; indexes differ; units mismatch under
        strict mode; frequency or missingness mismatch under strict
        mode; fewer than ``min_periods`` overlapping non-NaN
        observations; or the computed value is non-finite (overflow).
    """
    # ------------------------------------------------------------------
    # 1. Load config + check identity (name AND version) — OPR12.
    # ------------------------------------------------------------------
    if config is None:
        config = load_operator_config(_CONFIG_PATH)
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # ------------------------------------------------------------------
    # 2. Resolve params from config when omitted — OPR8.
    # ------------------------------------------------------------------
    if params is None:
        params = CovarianceParams(
            ddof=int(config.default_value("ddof")),
            min_periods=int(config.default_value("min_periods")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 metadata).
    # ------------------------------------------------------------------
    if not isinstance(left, Series) or not isinstance(right, Series):
        raise CovarianceError(
            "covariance: both inputs must be Series artifacts; got "
            f"left={type(left).__name__}, right={type(right).__name__}."
        )

    # covariance does NOT align — that is align_series's job.
    if not left.payload.index.equals(right.payload.index):
        raise CovarianceError(
            "covariance: left and right Series must share an identical "
            "DatetimeIndex.  Align upstream with align_series first "
            f"(left.len={len(left.payload)}, right.len={len(right.payload)})."
        )

    # Units — strict by default (OPR11).  A covariance is unit-BEARING
    # (semantic unit = left.units × right.units), so mixed-unit inputs
    # produce a number whose dimension is easy to misread.  Reconcile
    # upstream with convert_units, or opt out explicitly (recorded in
    # lineage).
    if params.require_matching_units and left.units != right.units:
        raise CovarianceError(
            f"covariance: mismatched units left={left.units.value!r} vs "
            f"right={right.units.value!r}.  A covariance's unit is the "
            "product of its inputs' units; insert convert_units upstream "
            "(the sole unit-transition site) or pass "
            "require_matching_units=False to opt into a mixed-unit "
            "covariance explicitly."
        )

    # Frequency — strict by default (OPR11).
    if params.require_matching_frequency and left.frequency != right.frequency:
        raise CovarianceError(
            f"covariance: incompatible frequencies left={left.frequency!r} "
            f"vs right={right.frequency!r}.  Pass "
            "require_matching_frequency=False to opt into mixed-frequency "
            "covariance explicitly."
        )

    # Missingness — strict by default (OPR11).  JSON-canonical compare
    # handles flat AND nested policies (matches correlation /
    # align_series / series_arithmetic).
    if params.require_matching_missingness:
        left_sig = json.dumps(
            left.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        right_sig = json.dumps(
            right.missingness_policy.model_dump(mode="json"),
            sort_keys=True, separators=(",", ":"),
        )
        if left_sig != right_sig:
            raise CovarianceError(
                "covariance: incompatible missingness policies left vs "
                "right.  Pass require_matching_missingness=False to opt "
                "into mixed policies explicitly."
            )

    # ------------------------------------------------------------------
    # 4. Compute on the overlapping non-NaN pairs.
    # ------------------------------------------------------------------
    pair = pd.concat(
        [left.payload, right.payload], axis=1, keys=["left", "right"]
    ).dropna()
    n_obs = int(len(pair))
    if n_obs < params.min_periods:
        raise CovarianceError(
            f"covariance: only {n_obs} overlapping non-NaN "
            f"observation(s); min_periods={params.min_periods} required."
        )

    # NOTE: unlike correlation, a zero-variance (constant) input is NOT
    # degenerate here — cov(X, c) = 0.0 is mathematically defined.
    value = float(pair["left"].cov(pair["right"], ddof=params.ddof))
    if not np.isfinite(value):
        # Overflow guard: ±Inf is forbidden in any payload (ART11); a
        # non-finite covariance is a typed refusal, never a non-finite
        # ScalarMetric.
        raise CovarianceError(
            "covariance: computed a non-finite value over the overlap; "
            "the inputs overflow the float range."
        )

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep; right's chain in auxiliary_lineages;
    #    params sanitised (finite-or-None) before hashing (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "ddof": params.ddof,
        "min_periods": params.min_periods,
        "n_obs": n_obs,
        # OPR11/ART8 honesty: the output is tagged RATIO because the
        # closed enum has no product unit; the TRUE dimension is
        # left_units × right_units, recorded here for provenance.
        "left_units": left.units.value,
        "right_units": right.units.value,
        "require_matching_units": params.require_matching_units,
        "require_matching_frequency": params.require_matching_frequency,
        "require_matching_missingness": params.require_matching_missingness,
    }
    op_step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(left.lineage.head_hash, right.lineage.head_hash),
        auxiliary_lineages=(right.lineage,),
    )
    out_lineage = left.lineage.append(op_step)

    return ScalarMetric(
        metric_key=f"cov__{left.series_key}__{right.series_key}",
        value=value,
        units=TimeSeriesUnits.RATIO,
        lineage=out_lineage,
    )


__all__ = ["covariance", "CovarianceError"]

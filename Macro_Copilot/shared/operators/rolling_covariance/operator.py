"""rolling_covariance — windowed covariance between two Series.

Finance-blind ``statistical_relationship`` operator: the SEPARATE-
operator counterpart of ``covariance`` (emits a Series of trailing-
window covariances instead of a single ScalarMetric, per OPR2), and
the covariance-flavoured sibling of ``rolling_correlation``.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; zero finance
    vocabulary or math.
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``Series`` of rolling covariances.  The full-sample
    number is the separate ``covariance`` operator.
  - OPR8      : ``params: Optional[RollingCovarianceParams] = None``;
    every default resolves from ``config.yaml`` when omitted; schema
    defaults mirror the YAML.
  - OPR9      : two ``Series`` in, one ``Series`` out (closed family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    the right operand's chain rides in ``auxiliary_lineages``; params
    pass through ``sanitize_params_for_lineage``.
  - OPR11     : strict-by-default on units + frequency + missingness.
    Unit-BEARING (family doctrine set by ``covariance``): the semantic
    unit is the PRODUCT of the inputs' units, which the closed
    ``TimeSeriesUnits`` enum cannot express — output tagged ``RATIO``
    with both inputs' true units recorded in lineage params; mixed
    units refused by default with a lineage-recorded explicit opt-out;
    ``convert_units`` is the upstream remedy.  A lenient missingness
    opt-out emits an honest ``CombinedMissingnessV1``.
  - OPR13     : every recoverable failure raises
    ``RollingCovarianceError`` (a ``ValueError`` subclass); an all-NaN
    output is a typed refusal, never an empty payload, and an
    overflowing (±Inf) window is a typed refusal too — unlike a
    bounded correlation, a covariance CAN overflow on legal finite
    inputs, so the producing operation refuses (matching the
    full-sample ``covariance`` sibling) rather than silently scrubbing
    to NaN.  NOTE the deliberate divergence from
    ``rolling_correlation``: a constant (zero-variance) window is NOT
    degenerate here — its covariance is a legitimate 0.0.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic (same inputs → same ``head_hash``).

Composition contract: ``rolling_covariance`` does NOT align.  The two
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
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.rolling_covariance.schemas import RollingCovarianceParams


_OPERATOR_NAME = "rolling_covariance"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class RollingCovarianceError(ValueError):
    """Raised by ``rolling_covariance`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def rolling_covariance(
    left: Series,
    right: Series,
    *,
    params: Optional[RollingCovarianceParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Compute the rolling covariance between ``left`` and ``right``.

    Parameters
    ----------
    left, right :
        The two ``Series`` artifacts.  They MUST share an identical
        ``DatetimeIndex`` (align upstream with ``align_series``) and,
        by default, the same units (reconcile upstream with
        ``convert_units`` — a covariance's unit is the product of its
        inputs' units).
    params :
        Optional ``RollingCovarianceParams``.  When ``None`` every
        field is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        A new ``Series`` aligned to the shared input index, payload =
        trailing-window covariances, units ``RATIO`` (the closed enum
        has no product unit; both inputs' true units are recorded in
        lineage), frequency inherited (when both inputs agree, else
        ``None``), missingness either propagated (when policies agree)
        or wrapped as ``CombinedMissingnessV1`` under a lenient
        opt-out, lineage extended by one ``OperatorStep``.

    Raises
    ------
    RollingCovarianceError
        Inputs are not ``Series``; indexes differ; units mismatch
        under strict mode; frequency or missingness mismatch under
        strict mode; a window value overflows the float range; or the
        entire output is ``NaN``.
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
        raw_min_periods = config.default_value("min_periods")
        params = RollingCovarianceParams(
            window=int(config.default_value("window")),
            min_periods=(
                int(raw_min_periods) if raw_min_periods is not None else None
            ),
            ddof=int(config.default_value("ddof")),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O; OPR11 metadata).
    # ------------------------------------------------------------------
    if not isinstance(left, Series) or not isinstance(right, Series):
        raise RollingCovarianceError(
            "rolling_covariance: both inputs must be Series artifacts; "
            f"got left={type(left).__name__}, right={type(right).__name__}."
        )

    # rolling_covariance does NOT align — that is align_series's job.
    if not left.payload.index.equals(right.payload.index):
        raise RollingCovarianceError(
            "rolling_covariance: left and right Series must share an "
            "identical DatetimeIndex.  Align upstream with align_series "
            f"first (left.len={len(left.payload)}, "
            f"right.len={len(right.payload)})."
        )

    # Units — strict by default (OPR11; family doctrine set by
    # ``covariance``): unit-BEARING, so mixed-unit inputs are refused
    # unless explicitly opted into (recorded in lineage).
    if params.require_matching_units and left.units != right.units:
        raise RollingCovarianceError(
            f"rolling_covariance: mismatched units "
            f"left={left.units.value!r} vs right={right.units.value!r}.  "
            "A covariance's unit is the product of its inputs' units; "
            "insert convert_units upstream (the sole unit-transition "
            "site) or pass require_matching_units=False to opt into a "
            "mixed-unit covariance explicitly."
        )

    # Frequency — strict by default (OPR11).
    if params.require_matching_frequency and left.frequency != right.frequency:
        raise RollingCovarianceError(
            f"rolling_covariance: incompatible frequencies "
            f"left={left.frequency!r} vs right={right.frequency!r}.  "
            "Pass require_matching_frequency=False to opt into "
            "mixed-frequency covariance explicitly."
        )

    # Missingness — strict by default (OPR11).  JSON-canonical compare
    # handles flat AND nested policies (matches the sibling operators).
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
            raise RollingCovarianceError(
                "rolling_covariance: incompatible missingness policies "
                "left vs right.  Pass require_matching_missingness=False "
                "to opt into mixed policies explicitly."
            )

    # ------------------------------------------------------------------
    # 4. Compute the rolling covariance.
    # ------------------------------------------------------------------
    window = int(params.window)
    min_periods = (
        int(params.min_periods) if params.min_periods is not None else window
    )

    # pandas Rolling.cov computes the pairwise sample covariance over
    # each trailing window, honouring min_periods and ddof.  A constant
    # window on either arm legitimately yields 0.0 (NOT NaN) — the
    # deliberate divergence from rolling_correlation, where a zero-
    # variance window makes the coefficient undefined.
    result = left.payload.rolling(
        window=window, min_periods=min_periods,
    ).cov(right.payload, ddof=params.ddof)

    # Overflow guard (OPR13 / decided edge-case table): unlike a
    # correlation (bounded in [-1, 1]), a covariance CAN overflow on
    # legal finite inputs.  ±Inf is forbidden in any payload (ART11),
    # and pandas' streaming accumulators NaN-poison an overflowing
    # window — which would silently mislabel numerical failure as
    # warmup missingness.  Every output NaN must be explained by
    # insufficient finite pairs in its window; an UNEXPLAINED NaN (or
    # a literal ±Inf) is a numerical failure and the producing
    # operation refuses, matching the full-sample ``covariance``
    # sibling.
    if np.isinf(result.to_numpy()).any():
        raise RollingCovarianceError(
            "rolling_covariance: computed a non-finite (overflowing) "
            "window value; the inputs overflow the float range."
        )
    finite_pairs = (left.payload.notna() & right.payload.notna()).astype(float)
    pairs_in_window = finite_pairs.rolling(window=window, min_periods=1).sum()
    unexplained_nan = (pairs_in_window >= min_periods) & result.isna()
    if bool(unexplained_nan.any()):
        raise RollingCovarianceError(
            "rolling_covariance: a window with enough finite "
            "observations produced a non-finite value — the inputs "
            "overflow the float range (numerical failure, not "
            "missingness)."
        )
    result = result.astype(float)
    result.name = f"rolling_cov__{left.series_key}__{right.series_key}"

    # ------------------------------------------------------------------
    # 5. Refuse an all-NaN output (typed refusal — OPR13/OPR14).
    # ------------------------------------------------------------------
    n_obs = int(len(left.payload))
    n_finite = int(result.notna().sum())
    if n_finite == 0:
        raise RollingCovarianceError(
            f"rolling_covariance: produced an all-NaN output ({n_obs} "
            f"input rows, window={window}, min_periods={min_periods}, "
            f"ddof={params.ddof}).  Increase the input lookback or lower "
            "window/min_periods."
        )

    # ------------------------------------------------------------------
    # 6. Combine missingness honestly when policies disagree under a
    #    lenient opt-out (OPR11); preserve frequency only on agreement.
    # ------------------------------------------------------------------
    if left.missingness_policy == right.missingness_policy:
        out_missingness = left.missingness_policy
    else:
        out_missingness = CombinedMissingnessV1(
            components=(left.missingness_policy, right.missingness_policy),
        )

    out_frequency = left.frequency if left.frequency == right.frequency else None

    # ------------------------------------------------------------------
    # 7. Lineage — one OperatorStep; right's chain in auxiliary_lineages;
    #    params sanitised before hashing (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "window": window,
        "min_periods": min_periods,
        "ddof": params.ddof,
        "n_obs": n_obs,
        "n_finite_output": n_finite,
        # OPR11/ART8 honesty: output tagged RATIO because the closed
        # enum has no product unit; the TRUE dimension is
        # left_units × right_units, recorded here for provenance.
        "left_units": left.units.value,
        "right_units": right.units.value,
        "left_series_key": left.series_key,
        "right_series_key": right.series_key,
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

    return Series(
        series_key=f"rolling_cov__{left.series_key}__{right.series_key}",
        payload=result,
        units=TimeSeriesUnits.RATIO,
        frequency=out_frequency,
        missingness_policy=out_missingness,
        lineage=out_lineage,
    )


__all__ = ["rolling_covariance", "RollingCovarianceError"]

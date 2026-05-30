"""correlation — full-sample correlation between two typed Series.

The canonical v2.0 reference operator (ADR 0016).  Owns the
``statistical_relationship`` method family.  Finance-blind: it computes
a dimensionless statistic and runs unchanged on rates, FX, equities, or
temperature series.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; zero finance
    vocabulary or math.
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``ScalarMetric`` (rolling correlation is a SEPARATE
    operator emitting a Series, never a ``window`` flag here).
  - OPR8      : ``params: Optional[CorrelationParams] = None``; every
    default resolves from ``config.yaml`` when omitted; ``method`` is a
    switchable variant with ``kendall`` declared-but-unbuilt → clean
    ``NotImplementedError`` (never a silent fallback).
  - OPR9      : two ``Series`` in, one ``ScalarMetric`` out (closed
    family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    the right operand's chain rides in ``auxiliary_lineages``; params
    pass through ``sanitize_params_for_lineage``.
  - OPR11     : strict-by-default on frequency + missingness;
    unit-INVARIANT by design (a correlation is dimensionless, so no
    same-unit requirement — both inputs' units are recorded in lineage
    for provenance).  Output units = RATIO.
  - OPR13     : every recoverable failure raises ``CorrelationError`` (a
    ``ValueError`` subclass); an undefined coefficient (insufficient
    overlap / zero variance) is a typed refusal, never a NaN/Inf value.
  - OPR12/14  : config name+version identity checked; pure + rerun-
    deterministic (same inputs → same ``head_hash``).

Composition contract: ``correlation`` does NOT align.  The two Series
must already share an identical ``DatetimeIndex`` — align upstream with
``align_series``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

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
from shared.operators.correlation.schemas import (
    CorrelationMethod,
    CorrelationParams,
)


_OPERATOR_NAME = "correlation"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"

# Methods implemented in v1.  ``kendall`` is declared in the schema's
# closed set but NOT here → the operator refuses it cleanly (OPR8).
_IMPLEMENTED_METHODS: Tuple[CorrelationMethod, ...] = ("pearson", "spearman")


class CorrelationError(ValueError):
    """Raised by ``correlation`` on a recoverable user-facing failure.

    Subclass of ``ValueError`` (OPR13) so the rest of the substrate's
    ``except ValueError`` handling continues to work; the transport
    boundary converts it to the user-facing error envelope.
    """


def correlation(
    left: Series,
    right: Series,
    *,
    params: Optional[CorrelationParams] = None,
    config: Optional[OperatorConfig] = None,
) -> ScalarMetric:
    """Compute the full-sample correlation between ``left`` and ``right``.

    Parameters
    ----------
    left, right :
        The two ``Series`` artifacts.  They MUST share an identical
        ``DatetimeIndex`` (align upstream with ``align_series``).
    params :
        Optional ``CorrelationParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    ScalarMetric
        The correlation coefficient in ``RATIO`` units (in [-1, 1]),
        carrying the lineage of both inputs plus this operator step.

    Raises
    ------
    CorrelationError
        Inputs are not ``Series``; indexes differ; frequency or
        missingness mismatch under strict mode; fewer than
        ``min_periods`` overlapping non-NaN observations; or either
        series has zero variance over the overlap (undefined
        coefficient).
    NotImplementedError
        ``method`` is declared in the valid set but not yet built
        (``kendall``).
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
        params = CorrelationParams(
            method=config.default_value("method"),
            min_periods=int(config.default_value("min_periods")),
        )

    # ------------------------------------------------------------------
    # 3. Honest refusal for a declared-but-unbuilt method — OPR8.
    # ------------------------------------------------------------------
    if params.method not in _IMPLEMENTED_METHODS:
        raise NotImplementedError(
            f"correlation: method={params.method!r} is declared in the "
            "valid set but not yet implemented; see config.yaml "
            "methodology.planned_extensions.  Implemented methods: "
            f"{list(_IMPLEMENTED_METHODS)}."
        )

    # ------------------------------------------------------------------
    # 4. Structural-input validation (OPR9 typed I/O; OPR11 metadata).
    # ------------------------------------------------------------------
    if not isinstance(left, Series) or not isinstance(right, Series):
        raise CorrelationError(
            "correlation: both inputs must be Series artifacts; got "
            f"left={type(left).__name__}, right={type(right).__name__}."
        )

    # correlation does NOT align — that is align_series's job.
    if not left.payload.index.equals(right.payload.index):
        raise CorrelationError(
            "correlation: left and right Series must share an identical "
            "DatetimeIndex.  Align upstream with align_series first "
            f"(left.len={len(left.payload)}, right.len={len(right.payload)})."
        )

    # Frequency — strict by default (OPR11).  In production both tags
    # are derived at the adapter; today they may be None, in which case
    # None == None passes.
    if params.require_matching_frequency and left.frequency != right.frequency:
        raise CorrelationError(
            f"correlation: incompatible frequencies left={left.frequency!r} "
            f"vs right={right.frequency!r}.  Pass "
            "require_matching_frequency=False to opt into mixed-frequency "
            "correlation explicitly."
        )

    # Missingness — strict by default (OPR11).  JSON-canonical compare
    # handles flat AND nested policies (matches align_series /
    # series_arithmetic).
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
            raise CorrelationError(
                "correlation: incompatible missingness policies left vs "
                "right.  Pass require_matching_missingness=False to opt "
                "into mixed policies explicitly."
            )

    # NOTE (OPR11): correlation imposes NO same-unit requirement — a
    # correlation coefficient is dimensionless and scale-invariant, so
    # correlating a BPS series with a PERCENT series is meaningful.
    # Both units are recorded in lineage params for provenance, and the
    # output unit is RATIO.

    # ------------------------------------------------------------------
    # 5. Compute on the overlapping non-NaN pairs.
    # ------------------------------------------------------------------
    pair = pd.concat(
        [left.payload, right.payload], axis=1, keys=["left", "right"]
    ).dropna()
    n_obs = int(len(pair))
    if n_obs < params.min_periods:
        raise CorrelationError(
            f"correlation: only {n_obs} overlapping non-NaN "
            f"observation(s); min_periods={params.min_periods} required."
        )

    left_col = pair["left"]
    right_col = pair["right"]

    # Zero variance → undefined coefficient → typed refusal (never NaN).
    if left_col.nunique() <= 1 or right_col.nunique() <= 1:
        raise CorrelationError(
            "correlation: undefined — at least one series has zero "
            "variance over the overlapping window (a correlation "
            "coefficient is undefined when a series is constant)."
        )

    coefficient = float(left_col.corr(right_col, method=params.method))
    if not np.isfinite(coefficient):
        # Defensive: the zero-variance guard above should catch the
        # degenerate cases; this keeps a non-finite value from ever
        # reaching the (finite-only) ScalarMetric payload.
        raise CorrelationError(
            "correlation: computed a non-finite coefficient over the "
            "overlap; the inputs are degenerate."
        )

    # ------------------------------------------------------------------
    # 6. Lineage — one OperatorStep; right's chain in auxiliary_lineages;
    #    params sanitised (finite-or-None) before hashing (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "method": params.method,
        "min_periods": params.min_periods,
        "n_obs": n_obs,
        "left_units": left.units.value,
        "right_units": right.units.value,
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
        metric_key=f"corr__{left.series_key}__{right.series_key}",
        value=coefficient,
        units=TimeSeriesUnits.RATIO,
        lineage=out_lineage,
    )


__all__ = ["correlation", "CorrelationError"]

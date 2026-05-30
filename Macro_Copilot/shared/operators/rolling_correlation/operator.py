"""rolling_correlation — windowed correlation between two Series.

A v2.0 finance-blind operator (ADR 0016) in the
``statistical_relationship`` method family.  The SEPARATE-operator
counterpart of ``correlation``: emits a Series of rolling coefficients
instead of a single ScalarMetric, per OPR2 (constant output type — a
window flag would change the output shape, so it ships as its own
operator).

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind statistical method; zero finance
    vocabulary or math.
  - OPR2      : ONE output artifact type for every parameter value —
    always a ``Series`` of rolling coefficients.  A full-sample
    correlation is the separate ``correlation`` operator.
  - OPR8      : ``params: Optional[RollingCorrelationParams] = None``;
    every default resolves from ``config.yaml`` when omitted; schema
    defaults mirror the YAML.  ``method='kendall'`` is declared in
    the closed set but not yet implemented — clean
    ``NotImplementedError`` (OPR8 honest refusal, same as
    ``correlation``).
  - OPR9      : two ``Series`` in, one ``Series`` out (closed family).
  - OPR10     : appends exactly one ``OperatorStep`` via ``.build``;
    the right operand's chain rides in ``auxiliary_lineages``; params
    pass through ``sanitize_params_for_lineage``.
  - OPR11     : strict-by-default on frequency + missingness;
    unit-INVARIANT by design.  Output units = RATIO.  A lenient
    missingness opt-out emits an honest ``CombinedMissingnessV1``
    (matches the rolling_regression / align_series discipline).
  - OPR13     : every recoverable failure raises
    ``RollingCorrelationError`` (a ``ValueError`` subclass); an
    all-NaN output is a typed refusal, never an empty payload.
  - OPR12/14  : config name+version identity checked; pure +
    rerun-deterministic (same inputs → same ``head_hash``).

Composition contract: ``rolling_correlation`` does NOT align.  The two
Series must already share an identical ``DatetimeIndex`` — align
upstream with ``align_series``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.missingness import CombinedMissingnessV1
from shared.artifacts.types import Series
from shared.artifacts.units import TimeSeriesUnits
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.rolling_correlation.schemas import (
    RollingCorrelationMethod,
    RollingCorrelationParams,
)


_OPERATOR_NAME = "rolling_correlation"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Implemented in v1: pearson + spearman.  kendall is declared in the
# schema's closed set but NOT here — the operator refuses it cleanly
# (OPR8).  Mirrors ``correlation``.
_IMPLEMENTED_METHODS: Tuple[RollingCorrelationMethod, ...] = (
    "pearson", "spearman",
)


class RollingCorrelationError(ValueError):
    """Raised by ``rolling_correlation`` on a recoverable user-facing
    failure (a ``ValueError`` subclass, OPR13)."""


def rolling_correlation(
    left: Series,
    right: Series,
    *,
    params: Optional[RollingCorrelationParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Compute the rolling correlation between ``left`` and ``right``.

    Parameters
    ----------
    left, right :
        The two ``Series`` artifacts.  They MUST share an identical
        ``DatetimeIndex`` (align upstream with ``align_series``).
    params :
        Optional ``RollingCorrelationParams``.  When ``None`` every
        field is resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        A new ``Series`` aligned to the shared input index, payload =
        rolling correlation coefficients in ``[-1, 1]``, units
        ``RATIO``, frequency inherited (when both inputs agree, else
        ``None``), missingness either propagated (when policies agree)
        or wrapped as ``CombinedMissingnessV1`` under a lenient opt-
        out, lineage extended by one ``OperatorStep``.

    Raises
    ------
    RollingCorrelationError
        Inputs are not ``Series``; indexes differ; frequency or
        missingness mismatch under strict mode; entire output is
        ``NaN``.
    NotImplementedError
        ``method`` is declared in the valid set but not yet built
        (``kendall``).
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
        params = RollingCorrelationParams(
            method=config.default_value("method"),
            window=int(config.default_value("window")),
            min_periods=(
                int(raw_min_periods) if raw_min_periods is not None else None
            ),
        )

    # ------------------------------------------------------------------
    # 3. Honest refusal for a declared-but-unbuilt method — OPR8.
    # ------------------------------------------------------------------
    if params.method not in _IMPLEMENTED_METHODS:
        raise NotImplementedError(
            f"rolling_correlation: method={params.method!r} is declared "
            "in the valid set but not yet implemented; see config.yaml "
            "methodology.planned_extensions.  Implemented methods: "
            f"{list(_IMPLEMENTED_METHODS)}."
        )

    # ------------------------------------------------------------------
    # 4. Structural-input validation (OPR9 typed I/O; OPR11 metadata).
    # ------------------------------------------------------------------
    if not isinstance(left, Series) or not isinstance(right, Series):
        raise RollingCorrelationError(
            "rolling_correlation: both inputs must be Series artifacts; "
            f"got left={type(left).__name__}, right={type(right).__name__}."
        )

    # rolling_correlation does NOT align — that is align_series's job.
    if not left.payload.index.equals(right.payload.index):
        raise RollingCorrelationError(
            "rolling_correlation: left and right Series must share an "
            "identical DatetimeIndex.  Align upstream with align_series "
            f"first (left.len={len(left.payload)}, "
            f"right.len={len(right.payload)})."
        )

    # Frequency — strict by default (OPR11).  Copy the exact compare
    # from ``correlation``.
    if params.require_matching_frequency and left.frequency != right.frequency:
        raise RollingCorrelationError(
            f"rolling_correlation: incompatible frequencies "
            f"left={left.frequency!r} vs right={right.frequency!r}.  "
            "Pass require_matching_frequency=False to opt into "
            "mixed-frequency correlation explicitly."
        )

    # Missingness — strict by default (OPR11).  JSON-canonical compare
    # handles flat AND nested policies (matches align_series /
    # series_arithmetic / correlation / rolling_regression).
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
            raise RollingCorrelationError(
                "rolling_correlation: incompatible missingness policies "
                "left vs right.  Pass require_matching_missingness=False "
                "to opt into mixed policies explicitly."
            )

    # NOTE (OPR11): unit-INVARIANT by design — a correlation is
    # dimensionless and scale-invariant, so correlating a BPS series
    # with a PERCENT series is meaningful.  Both units are recorded in
    # lineage params for provenance; the output unit is RATIO.

    # ------------------------------------------------------------------
    # 5. Compute the rolling correlation.
    # ------------------------------------------------------------------
    window = int(params.window)
    min_periods = (
        int(params.min_periods) if params.min_periods is not None else window
    )

    if params.method == "pearson":
        # pandas Series.rolling.corr is Pearson and respects min_periods.
        result = left.payload.rolling(
            window=window, min_periods=min_periods,
        ).corr(right.payload)
    else:
        # spearman — pandas does not have a vectorised rolling-Spearman,
        # so compute element-by-element using pandas' Series.corr on
        # each window slice (clear-over-clever; correctness first).
        # Iteration cost is O(n) windows × O(window) inside each — for
        # typical macro analytics (a few thousand observations, window
        # ≈ 60–252) this is sub-second.
        n = len(left.payload)
        left_arr = left.payload.to_numpy(dtype=float)
        right_arr = right.payload.to_numpy(dtype=float)
        out = np.full(n, np.nan, dtype=float)
        for t in range(window - 1, n):
            ls = left_arr[t - window + 1:t + 1]
            rs = right_arr[t - window + 1:t + 1]
            mask = np.isfinite(ls) & np.isfinite(rs)
            if mask.sum() < min_periods:
                continue
            # zero variance on either side ⇒ undefined ⇒ NaN
            ls_m = ls[mask]
            rs_m = rs[mask]
            if (ls_m.max() - ls_m.min() == 0.0) or (
                rs_m.max() - rs_m.min() == 0.0
            ):
                continue
            out[t] = float(
                pd.Series(ls_m).corr(pd.Series(rs_m), method="spearman"),
            )
        result = pd.Series(out, index=left.payload.index, dtype=float)

    # Guard against ±Inf landing in the payload (the Series validator
    # rejects them).  In practice rolling-corr produces NaN for
    # zero-variance windows; this is a defensive scrub.
    result = result.replace([np.inf, -np.inf], np.nan)
    result = result.astype(float)
    result.name = f"rolling_corr__{left.series_key}__{right.series_key}"

    # ------------------------------------------------------------------
    # 6. Refuse an all-NaN output (typed refusal — OPR13/OPR14).
    # ------------------------------------------------------------------
    n_obs = int(len(left.payload))
    n_finite = int(result.notna().sum())
    if n_finite == 0:
        raise RollingCorrelationError(
            f"rolling_correlation: produced an all-NaN output ({n_obs} "
            f"input rows, window={window}, min_periods={min_periods}, "
            f"method={params.method!r}).  Increase the input lookback, "
            "lower window/min_periods, or check for a constant series."
        )

    # ------------------------------------------------------------------
    # 7. Combine missingness honestly when policies disagree under a
    #    lenient opt-out (OPR11 / M5).  When the strict guard above
    #    accepted the inputs, they either agree (use that policy) or
    #    were intentionally allowed to differ (emit a combined record).
    # ------------------------------------------------------------------
    if left.missingness_policy == right.missingness_policy:
        out_missingness = left.missingness_policy
    else:
        out_missingness = CombinedMissingnessV1(
            components=(left.missingness_policy, right.missingness_policy),
        )

    # Frequency: when both inputs agree (and the strict guard let them
    # through, or both are None), preserve.  Otherwise None.
    out_frequency = left.frequency if left.frequency == right.frequency else None

    # ------------------------------------------------------------------
    # 8. Lineage — one OperatorStep; right's chain in auxiliary_lineages;
    #    params sanitised before hashing (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "method": params.method,
        "window": window,
        "min_periods": min_periods,
        "n_obs": n_obs,
        "n_finite_output": n_finite,
        "left_units": left.units.value,
        "right_units": right.units.value,
        "left_series_key": left.series_key,
        "right_series_key": right.series_key,
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
        series_key=f"rolling_corr__{left.series_key}__{right.series_key}",
        payload=result,
        units=TimeSeriesUnits.RATIO,
        frequency=out_frequency,
        missingness_policy=out_missingness,
        lineage=out_lineage,
    )


__all__ = ["rolling_correlation", "RollingCorrelationError"]

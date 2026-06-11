"""cumulative — running sum/max/min of a Series.

Finance-blind ``single_series_transform`` operator.  Computes the
running reduction of one Series from its first row — sum (the running
total of e.g. period changes), max (the running peak) or min (the
running trough) — emitting a Series in the input's units.

``product`` is DELIBERATELY EXCLUDED: cumprod of a unit-bearing series
is dimensionally dishonest (unit^n; only meaningful on dimensionless
growth factors) — declared in ``planned_extensions``.

Contract highlights (cite by OPR-number):

  - OPR1/OPR6 : one finance-blind structural method (running
    reduction); zero finance vocabulary or math.
  - OPR2      : ONE output artifact type — always a ``Series``; units
    passthrough for every statistic.
  - OPR8      : ``params: Optional[CumulativeParams] = None``;
    defaults resolve from ``config.yaml``.
  - OPR9      : one ``Series`` in, one ``Series`` out.
  - OPR10     : one ``OperatorStep`` appended to the input's chain.
  - OPR11     : units passthrough (a running sum/peak/trough of BPS
    is BPS).
  - OPR13     : typed ``CumulativeError`` refusals: non-Series input,
    an overflowing (±Inf) running sum (an UNBOUNDED accumulator — the
    cross_sectional_statistic full-accumulation precedent: raise,
    never scrub), or an all-NaN output.  NaN positions stay NaN and
    accumulation continues over the non-NaN values (pandas skipna
    semantics — documented).
  - OPR14     : pure + rerun-deterministic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.cumulative.schemas import CumulativeParams


_OPERATOR_NAME = "cumulative"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


class CumulativeError(ValueError):
    """Raised by ``cumulative`` on a recoverable user-facing failure
    (a ``ValueError`` subclass, OPR13)."""


def cumulative(
    series: Series,
    *,
    params: Optional[CumulativeParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Running sum/max/min of ``series`` from its first row.

    Parameters
    ----------
    series :
        The input ``Series``.
    params :
        Optional ``CumulativeParams``.  When ``None`` every field is
        resolved from the bundled ``config.yaml`` (OPR8).
    config :
        Optional ``OperatorConfig``.  When ``None`` the bundled
        ``config.yaml`` is loaded (process-cached).

    Returns
    -------
    Series
        The running statistic on the input index (NaN positions stay
        NaN; accumulation continues over non-NaN values), in the
        input's units; frequency and missingness policy pass through;
        lineage extended by one ``OperatorStep``.

    Raises
    ------
    CumulativeError
        The input is not a ``Series``; the running sum overflows the
        float range; or the output is all-NaN.
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
        params = CumulativeParams(
            statistic=config.default_value("statistic"),
        )

    # ------------------------------------------------------------------
    # 3. Structural-input validation (OPR9 typed I/O).
    # ------------------------------------------------------------------
    if not isinstance(series, Series):
        raise CumulativeError(
            "cumulative: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    # ------------------------------------------------------------------
    # 4. The running reduction (pandas skipna semantics: NaN positions
    #    stay NaN; accumulation continues over non-NaN values).
    # ------------------------------------------------------------------
    if params.statistic == "sum":
        result = series.payload.cumsum()
    elif params.statistic == "max":
        result = series.payload.cummax()
    elif params.statistic == "min":
        result = series.payload.cummin()
    else:
        # Defensive (the schema's Literal closes the set): a future
        # expansion landing without a branch must refuse (OPR8).
        raise NotImplementedError(
            f"cumulative: statistic={params.statistic!r} is declared "
            "but not implemented; see config.yaml "
            "methodology.planned_extensions."
        )

    result = result.astype(float)

    # Overflow honesty: the running SUM is an UNBOUNDED accumulator —
    # extreme finite inputs can overflow to ±Inf.  Full-accumulation
    # reductions raise (the cross_sectional_statistic precedent),
    # never scrub: an overflowing total is numerical failure, not
    # missingness.  (max/min are bounded by the inputs and cannot
    # overflow, but the check is uniform and free.)
    if np.isinf(result.to_numpy(dtype=float)).any():
        raise CumulativeError(
            "cumulative: the running statistic overflows the float "
            "range (numerical failure, not missingness)."
        )

    n_finite = int(np.isfinite(result.to_numpy(dtype=float)).sum())
    if n_finite == 0:
        raise CumulativeError(
            f"cumulative: produced an all-NaN output "
            f"({len(series.payload)} input rows, "
            f"statistic={params.statistic!r}).  The input has no "
            "non-NaN values."
        )
    result.name = f"cum{params.statistic}__{series.series_key}"

    # ------------------------------------------------------------------
    # 5. Lineage — one OperatorStep (OPR10).
    # ------------------------------------------------------------------
    step_params: Dict[str, Any] = {
        "statistic": params.statistic,
        "n_obs": int(len(series.payload)),
        "n_finite_output": n_finite,
        "input_units": series.units.value,
        "input_series_key": series.series_key,
    }
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage(step_params),
        input_hashes=(series.lineage.head_hash,),
    )
    out_lineage = series.lineage.append(step)

    return Series(
        series_key=f"cum{params.statistic}__{series.series_key}",
        payload=result,
        units=series.units,
        frequency=series.frequency,
        missingness_policy=series.missingness_policy,
        lineage=out_lineage,
    )


__all__ = ["cumulative", "CumulativeError"]

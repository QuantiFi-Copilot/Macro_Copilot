"""summarize_series — collapse a Series to a 1-row summary at a sentinel date.

Closes the "compare across regimes" gap in the
``regime_conditioned_relationship`` archetype.  See module docstring
in __init__.py for the rationale.

Design lock
-----------
The sentinel date is ``pd.Timestamp("1900-01-01")``.  Hard-coded —
NOT a parameter, NOT configurable.  Two reasons:

  1. Both per-regime summaries in a regime-conditioned-relationship
     workflow MUST share the same sentinel for the downstream
     ``series_arithmetic.subtract`` to have a non-empty intersection.
     A configurable sentinel would invite mismatch bugs.
  2. The sentinel is semantically meaningless ("this is a scalar
     summary, the date does not matter").  ``1900-01-01`` is far
     enough from any real-world rates data that a reader cannot
     mistake it for a real observation.

When the ``ScalarMetric`` artifact lands (build plan v5 / R5,
deferred), this sentinel will go away — ``ScalarMetric`` is a
proper scalar type with no date.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import Series
from shared.config.operator_config import (
    OperatorConfig,
    OperatorConfigError,
    _check_config_identity,
    load_operator_config,
)
from shared.operators.summarize_series.schemas import SummarizeSeriesParams


_OPERATOR_NAME = "summarize_series"
_OPERATOR_VERSION = "1.0.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Hard-coded sentinel date.  See module docstring for the design lock.
SUMMARY_SENTINEL_DATE: pd.Timestamp = pd.Timestamp("1900-01-01")


class SummarizeSeriesError(ValueError):
    """Raised by ``summarize_series`` on a recoverable user-facing
    failure (e.g. all-NaN input)."""


def _compute_statistic(values: pd.Series, statistic: str, ddof: int = 1) -> float:
    if statistic == "mean":
        return float(values.mean())
    if statistic == "median":
        return float(values.median())
    if statistic == "std":
        return float(values.std(ddof=ddof))
    if statistic == "sum":
        return float(values.sum())
    if statistic == "count":
        return float(len(values))
    raise SummarizeSeriesError(
        f"summarize_series: unsupported statistic={statistic!r}."
    )


def _compute_dispersion(
    values: pd.Series, dispersion: str, ddof: int = 1,
) -> Optional[float]:
    if dispersion == "none":
        return None
    if dispersion == "std":
        return float(values.std(ddof=ddof))
    if dispersion == "mad":
        # Median absolute deviation about the median — pandas does not
        # ship a vectorised MAD anymore, so compute it explicitly.
        med = float(values.median())
        return float((values - med).abs().median())
    raise SummarizeSeriesError(
        f"summarize_series: unsupported dispersion={dispersion!r}."
    )


def summarize_series(
    series: Series,
    params: Optional[SummarizeSeriesParams] = None,
    config: Optional[OperatorConfig] = None,
) -> Series:
    """Collapse ``series`` to a 1-row summary Series at the hard-coded
    sentinel date.

    Returns
    -------
    Series
        Single-row payload at ``SUMMARY_SENTINEL_DATE`` with the
        chosen statistic.  Units are inherited from the input.
        Lineage extends the input chain with this operator step;
        the dispersion value, n_observations, and n_dropped are
        recorded in step.params for diagnostic recovery.
    """
    if config is None:
        config = load_operator_config(_CONFIG_PATH)

    # Config identity (OPR12) — name AND version.
    _check_config_identity(config, _OPERATOR_NAME, _OPERATOR_VERSION)

    # Typed input guard (OPR13 / ERR-8): a non-Series input would otherwise
    # raise a raw AttributeError on ``series.payload`` below.
    if not isinstance(series, Series):
        raise SummarizeSeriesError(
            f"summarize_series: input must be a Series artifact; got "
            f"{type(series).__name__}."
        )

    if params is None:
        params = SummarizeSeriesParams(
            statistic=config.default_value("statistic"),
            dispersion=config.default_value("dispersion"),
        )

    n_total = int(len(series.payload))
    cleaned = series.payload.dropna()
    n_used = int(len(cleaned))
    n_dropped = n_total - n_used
    if n_used == 0:
        raise SummarizeSeriesError(
            f"summarize_series: input has 0 finite observations "
            f"after dropna ({n_total} total, all NaN).  Cannot "
            "compute a meaningful summary."
        )
    # ``count`` is well-defined even when the std would not be (n=1
    # → std=NaN); the dispersion guard below handles that case.
    if params.statistic in ("std",) and n_used < 2:
        raise SummarizeSeriesError(
            f"summarize_series: statistic={params.statistic!r} "
            f"requires >=2 observations; got n={n_used}."
        )

    central = _compute_statistic(cleaned, params.statistic, params.ddof)
    # OPR14(b) / ERR-3: refuse to emit a non-finite central value into the
    # payload (e.g. a sum that overflowed) with the operator's own typed
    # error, rather than letting the artifact layer reject it as a bare
    # ValidationError.
    if not math.isfinite(central):
        raise SummarizeSeriesError(
            f"summarize_series: statistic={params.statistic!r} produced a "
            f"non-finite value ({central!r})."
        )
    if (
        params.dispersion in ("std", "mad")
        and n_used < 2
    ):
        # Dispersion undefined on n<2 — record None in lineage rather
        # than raising (None is JSON-canonical; the central tendency is
        # still valid for statistic=mean/median/sum/count).  Per OPR10 a
        # non-finite computed float must become None before it reaches
        # the lineage hasher (which rejects NaN/Inf) — this was the
        # default-path crash the audit flagged.
        dispersion_value: Optional[float] = None
    else:
        dispersion_value = _compute_dispersion(cleaned, params.dispersion, params.ddof)

    payload = pd.Series(
        [central],
        index=pd.DatetimeIndex([SUMMARY_SENTINEL_DATE]),
        name=series.payload.name,
        dtype=float,
    )

    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage({
            "statistic": params.statistic,
            "dispersion": params.dispersion,
            "ddof": params.ddof,
            "central_value": central,
            "dispersion_value": dispersion_value,
            "n_observations": n_used,
            "n_dropped": n_dropped,
            "sentinel_date": SUMMARY_SENTINEL_DATE.strftime("%Y-%m-%d"),
        }),
        input_hashes=(series.lineage.head_hash,),
    )

    return Series(
        series_key=series.series_key,
        payload=payload,
        units=series.units,
        # Frequency on a 1-row sentinel is meaningless — emit None so
        # downstream operators don't pretend the summary has a real
        # business-day cadence.
        frequency=None,
        missingness_policy=series.missingness_policy,
        lineage=series.lineage.append(step),
    )


__all__ = [
    "summarize_series",
    "SummarizeSeriesError",
    "SUMMARY_SENTINEL_DATE",
]

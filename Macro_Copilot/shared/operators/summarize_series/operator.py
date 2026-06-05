"""summarize_series — collapse a Series to ONE scalar summary statistic.

Emits a ``ScalarMetric`` (mean / median / std / sum / count / last /
first) carrying the input's units.  This is the canonical "what is the
average / std / current value of X" path, and its terminal
``ScalarMetric`` is what the L4.5 CoverageGate expects for single-number
queries.

History — the sentinel-date Series (removed)
--------------------------------------------
Before the ``ScalarMetric`` closed-family type existed, this operator
emitted a single-row ``Series`` at the fake sentinel date
``1900-01-01`` (``SUMMARY_SENTINEL_DATE``, kept below only for the
paused regime template's import) because there was no scalar artifact
type.  The ``regime_conditioned_relationship`` archetype wired two such
summaries into ``series_arithmetic.subtract`` at that shared sentinel.
That template is on development pause and the open-DAG lane never
invokes it (``DISABLE_TEMPLATE_ROUTER=1``); its sentinel-Series
dependency is intentionally not preserved.  ``ScalarMetric`` is a
proper scalar with no date, so the sentinel is gone from the output.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from shared.artifacts.lineage import OperatorStep, sanitize_params_for_lineage
from shared.artifacts.types import ScalarMetric, Series
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
    if statistic == "last":
        # Latest non-NaN observation — the "current value" of the series.
        # ``values`` is already dropna'd by the caller, so iloc[-1] is the
        # most recent finite value.
        return float(values.iloc[-1])
    if statistic == "first":
        return float(values.iloc[0])
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
) -> ScalarMetric:
    """Collapse ``series`` to ONE scalar summary statistic.

    Returns
    -------
    ScalarMetric
        A single finite scalar (the chosen ``statistic`` — mean /
        median / std / sum / count / last / first) carrying the
        input's units and a ``metric_key`` equal to the statistic
        name.  Lineage extends the input chain with this operator
        step; the dispersion value, n_observations, and n_dropped
        are recorded in step.params for diagnostic recovery.

    Migration note (was: 1-row sentinel-date Series)
    ------------------------------------------------
    Before the ScalarMetric closed-family type was wired end-to-end,
    this operator emitted a single-row ``Series`` at the fake sentinel
    date ``1900-01-01`` because no scalar artifact type existed.  The
    L4.5 CoverageGate (correctly) refused every "what is the average /
    std of X" query because a Series is not a scalar answer.  Now that
    ``ScalarMetric`` is admitted and wired through the executor, store,
    and frontend (proven by the correlation/cointegration operators),
    this operator emits a real ``ScalarMetric``.  The old
    ``regime_conditioned_relationship`` template wired two of these into
    ``series_arithmetic.subtract`` at the shared sentinel date — that
    template is on development pause and the open-DAG lane never invokes
    it (``DISABLE_TEMPLATE_ROUTER=1``); its sentinel-Series dependency is
    intentionally not preserved here.
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
        }),
        input_hashes=(series.lineage.head_hash,),
    )

    return ScalarMetric(
        metric_key=params.statistic,
        value=central,
        units=series.units,
        lineage=series.lineage.append(step),
    )


__all__ = [
    "summarize_series",
    "SummarizeSeriesError",
    "SUMMARY_SENTINEL_DATE",
]

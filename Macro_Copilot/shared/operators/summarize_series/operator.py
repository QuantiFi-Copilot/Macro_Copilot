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
# 1.1.0 (Track-A): statistic set extended with 'quantile' (+ its q
# param) — a behavioural expansion, hence the minor bump (OPR14d; the
# rolling_statistic 1.1.0 precedent).  The same bump also brings the
# lineage ddof field in line with the OPR14 meaningless-param doctrine
# (nulled when neither statistic nor dispersion is 'std').
_OPERATOR_VERSION = "1.1.0"

_CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


# Hard-coded sentinel date.  See module docstring for the design lock.
SUMMARY_SENTINEL_DATE: pd.Timestamp = pd.Timestamp("1900-01-01")


class SummarizeSeriesError(ValueError):
    """Raised by ``summarize_series`` on a recoverable user-facing
    failure (e.g. all-NaN input)."""


def _compute_statistic(
    values: pd.Series, statistic: str, ddof: int = 1, q: float = 0.5,
) -> float:
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
    if statistic == "quantile":
        # 1.1.0: the q-th full-sample empirical quantile — LINEAR
        # INTERPOLATION between order statistics (Hyndman–Fan type 7,
        # the pandas default; named, not silently inherited — OPR7).
        # Dimensionful, in the input's units (a quantile of a BPS
        # series is a BPS number, NOT a ratio).
        return float(values.quantile(q))
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
        median / std / sum / count / last / first / quantile) carrying
        the input's units and a ``metric_key`` equal to the statistic
        name — EXCEPT ``statistic='quantile'``, whose ``metric_key`` is
        ``quantile_<q>`` (e.g. ``quantile_0.05``) so the scalar is
        self-describing about the level it reports.  Lineage extends
        the input chain with this operator step; the dispersion value,
        n_observations, and n_dropped are recorded in step.params for
        diagnostic recovery.

        ``statistic='count'`` is special: it is well-defined on an
        empty / all-NaN input and returns a legitimate ScalarMetric
        of 0.0 (FM-6 — "how many matching days" must be able to
        answer "0").  Every other statistic refuses on zero finite
        observations.

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
            q=float(config.default_value("q")),
        )

    n_total = int(len(series.payload))
    cleaned = series.payload.dropna()
    n_used = int(len(cleaned))
    n_dropped = n_total - n_used
    # FM-6 count-of-zero: ``count`` is the ONE statistic that is
    # well-defined on an empty / all-NaN input — "how many matching
    # observations" legitimately answers 0 (e.g. an apply_mask
    # subsample whose mask never fired).  It therefore bypasses the
    # zero-finite-observations refusal below and flows through the
    # normal path (_compute_statistic on the empty ``cleaned`` returns
    # 0.0; dispersion on n<2 is recorded as None per OPR10).  Every
    # other statistic keeps the refusal byte-identical.
    if n_used == 0 and params.statistic != "count":
        # R3 diagnostics: an EMPTY input (n_total==0) almost always means
        # an upstream apply_mask / threshold_events selected ZERO rows (the
        # FM-6 zero-match contract returns an empty Series).  The single
        # most common cause is a UNITS mismatch on the threshold — rates
        # series are PERCENT-denominated (a 2s10s spread is ~0.40, i.e.
        # ~40 bps), so a threshold expressed as the bare bps number (e.g.
        # 50 for "50 bps") matches nothing; it must be 0.50 in the series'
        # percent units, or the series must be convert_units'd to bps
        # first.  An all-NaN-but-non-empty input (n_total>0) instead points
        # to data gaps over the window.  The substring "0 finite
        # observations" is load-bearing: the pipeline's recompose
        # remediation keys on it.
        if n_total == 0:
            hint = (
                "  The input series is EMPTY — an upstream apply_mask / "
                "threshold_events matched ZERO rows.  Most often this is a "
                "bps-vs-percent UNITS mismatch on the threshold: rate "
                "LEVELS are percent while spread/breakeven tools emit bps "
                "and a subtracted-levels spread is percent, so a raw "
                "threshold can be off by 100x.  The robust fix is to "
                "convert_units the series to bps immediately before "
                "threshold_events and use the bps number.  Also check the "
                "rule direction (above/below) and sign."
            )
        else:
            hint = "  Cannot compute a meaningful summary."
        raise SummarizeSeriesError(
            f"summarize_series: input has 0 finite observations "
            f"after dropna ({n_total} total, all NaN).{hint}"
        )
    # ``count`` is well-defined even when the std would not be (n=1
    # → std=NaN); the dispersion guard below handles that case.
    if params.statistic in ("std",) and n_used < 2:
        raise SummarizeSeriesError(
            f"summarize_series: statistic={params.statistic!r} "
            f"requires >=2 observations; got n={n_used}."
        )

    central = _compute_statistic(
        cleaned, params.statistic, params.ddof, params.q,
    )
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

    # OPR14 meaningless-param normalisation (brought in line under the
    # 1.1.0 bump): ddof is consumed only when statistic or dispersion
    # is 'std'; q only by statistic='quantile'.  Null when ignored so
    # identity tracks content.
    ddof_for_lineage = (
        int(params.ddof)
        if (params.statistic == "std" or params.dispersion == "std")
        else None
    )
    q_for_lineage = (
        float(params.q) if params.statistic == "quantile" else None
    )
    step = OperatorStep.build(
        name=_OPERATOR_NAME,
        version=_OPERATOR_VERSION,
        params=sanitize_params_for_lineage({
            "statistic": params.statistic,
            "dispersion": params.dispersion,
            "ddof": ddof_for_lineage,
            "q": q_for_lineage,
            "central_value": central,
            "dispersion_value": dispersion_value,
            "n_observations": n_used,
            "n_dropped": n_dropped,
        }),
        input_hashes=(series.lineage.head_hash,),
    )

    # metric_key carries the q level for quantiles so the scalar is
    # self-describing ("quantile_0.05"), the statistic name otherwise.
    metric_key = (
        f"quantile_{params.q:g}"
        if params.statistic == "quantile"
        else params.statistic
    )
    return ScalarMetric(
        metric_key=metric_key,
        value=central,
        units=series.units,
        lineage=series.lineage.append(step),
    )


__all__ = [
    "summarize_series",
    "SummarizeSeriesError",
    "SUMMARY_SENTINEL_DATE",
]

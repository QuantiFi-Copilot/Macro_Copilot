"""Pydantic parameter schema for summarize_series.

Consequential parameters:

  - ``statistic``   ∈ {mean, median, std, sum, count, last, first,
                       quantile}
        the aggregator computed over the input series's payload.
        Default ``mean`` — the canonical central-tendency summary.
        ``last`` returns the latest finite observation (the "current
        value" of the series); ``first`` returns the earliest.
        ``quantile`` (added in 1.1.0 per the OPR4 extend-don't-add
        ruling — the rolling_statistic 1.1.0 precedent) returns the
        ``q``-th full-sample empirical quantile (linear interpolation
        between order statistics — Hyndman–Fan type 7, the pandas
        default) in the input's units — the dimensionful
        tail/percentile read.
  - ``q``           ∈ (0, 1): the quantile level, consumed ONLY by
        ``statistic='quantile'`` (nulled in lineage otherwise, the
        ddof doctrine).  0.5 = the median; tail levels like 0.05 /
        0.95 are the desk's downside/upside percentile reads.
  - ``dispersion``  ∈ {std, mad, none}
        the dispersion statistic recorded in the operator's lineage
        alongside the central tendency.  Default ``std`` — same
        always-report-dispersion discipline ``conditional_aggregate``
        enforces.  ``none`` opts out (the lineage records that no
        dispersion was computed); ``mad`` is the robust alternative.

The output is a ``ScalarMetric`` — a single finite scalar with
``metric_key`` equal to the chosen statistic and units inherited 1:1
from the input.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SummarizeSeriesParams(BaseModel):
    """Parameters for ``summarize_series``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statistic: Literal[
        "mean", "median", "std", "sum", "count", "last", "first",
        "quantile",
    ] = Field(
        default="mean",
        description=(
            "Aggregator computed over the input series's payload to "
            "form the scalar summary value.  Default ``mean`` matches "
            "the canonical desk summary.  ``last`` = latest finite "
            "observation (the 'current value' of the series); ``first`` "
            "= earliest finite observation; ``quantile`` = the q-th "
            "full-sample empirical quantile (type-7 linear "
            "interpolation, in the input's units — set ``q``)."
        ),
    )
    q: float = Field(
        default=0.5, gt=0.0, lt=1.0,
        description=(
            "Quantile level in (0, 1), consumed ONLY by "
            "statistic='quantile' (nulled in lineage otherwise).  "
            "0.5 = the median; 0.05 / 0.95 are the canonical "
            "downside/upside tail reads."
        ),
    )
    dispersion: Literal["std", "mad", "none"] = Field(
        default="std",
        description=(
            "Dispersion statistic recorded in lineage alongside the "
            "central tendency.  Default ``std`` matches "
            "``conditional_aggregate``'s always-report-dispersion "
            "discipline.  ``none`` records nothing (rare; only when "
            "the caller has a methodology reason to suppress); ``mad`` "
            "(median-absolute-deviation) is the robust alternative "
            "for heavy-tailed distributions."
        ),
    )
    ddof: int = Field(
        default=1, ge=0, le=1,
        description=(
            "Delta degrees of freedom for the std statistic/dispersion "
            "(OPR7).  Default 1 = sample std; 0 = population std."
        ),
    )


__all__ = ["SummarizeSeriesParams"]

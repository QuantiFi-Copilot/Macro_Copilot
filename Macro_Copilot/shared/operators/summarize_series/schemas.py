"""Pydantic parameter schema for summarize_series.

Two consequential parameters:

  - ``statistic``   ∈ {mean, median, std, sum, count, latest}
        the aggregator computed over the input series's payload.
        Default ``mean`` — the canonical central-tendency summary.
        ``latest`` returns the LAST non-NaN observation — the desk's
        "as-of date snapshot" reading; added in PR-A8 follow-up so
        cross-sectional screens can deliver a true snapshot value
        rather than a window-average proxy (Codex F1: PR #201).
  - ``dispersion``  ∈ {std, mad, none}
        the dispersion statistic recorded in the operator's lineage
        alongside the central tendency.  Default ``std`` — same
        always-report-dispersion discipline ``conditional_aggregate``
        enforces.  ``none`` opts out (the lineage records that no
        dispersion was computed); ``mad`` is the robust alternative.

The output is always a single-row Series with payload
``{SUMMARY_SENTINEL_DATE: statistic_value}`` and units inherited
1:1 from the input.  The sentinel date is ``pd.Timestamp("1900-01-01")``
(hard-coded; see operator module docstring).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SummarizeSeriesParams(BaseModel):
    """Parameters for ``summarize_series``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    statistic: Literal[
        "mean", "median", "std", "sum", "count", "latest",
    ] = Field(
        default="mean",
        description=(
            "Aggregator computed over the input series's payload to "
            "form the 1-row summary value.  Default ``mean`` matches "
            "the canonical desk summary for per-regime relationship "
            "analyses (mean β over the regime sample).  ``latest`` "
            "returns the LAST non-NaN observation — the desk's "
            "snapshot-at-as-of-date reading; used by the V1 "
            "``cross_sectional_screen`` template to deliver true "
            "snapshot member values rather than a window-mean proxy."
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


__all__ = ["SummarizeSeriesParams"]

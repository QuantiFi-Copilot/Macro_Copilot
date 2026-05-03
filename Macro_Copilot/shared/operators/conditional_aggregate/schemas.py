"""Pydantic parameter schema for conditional_aggregate.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``conditional_aggregate``
exposes:

  - ``aggregator``  ∈ {mean, median, count, std}
  - ``dispersion``  ∈ {none, std} — dispersion estimate reported
                                     alongside the central tendency
  - ``min_n``       int >= 1 — when the effective N at any offset is
                   below this, the per-offset ``low_n`` flag in the
                   output metadata is True (the operator does NOT
                   suppress the value; the caller / template / UI
                   decides how to react)
  - ``ddof``        int >= 0 — degrees of freedom for std (default 1
                   = sample std)

Phase 1A surface is intentionally minimal (build plan v5 — strip to
the Q1 minimum).  The dispersion knob is REQUIRED to be reported
alongside the central tendency by design — a conditional mean
without dispersion is not a useful answer; the operator refuses to
be a one-number-output by default.

Deferred per planned_extensions:

  - ``aggregator`` ∈ {sum, min, max, quantile}
  - ``dispersion`` ∈ {iqr, mad, bootstrap_ci}
  - ``weighting`` (overlap-de-weighted aggregation)
  - ``MaskedSeries`` input overload (Q2 trigger)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


Aggregator = Literal["mean", "median", "count", "std"]
DispersionKind = Literal["none", "std"]


class ConditionalAggregateParams(BaseModel):
    """Parameters for ``conditional_aggregate`` (Phase 1A surface).

    ``aggregator`` and ``dispersion`` default to ``None`` so the YAML
    is authoritative for those defaults at runtime — same pattern as
    ``threshold_events`` and ``event_windows`` post the Codex P2
    follow-ups (PR #53, PR #55).  The operator resolves ``None`` →
    ``config.default_value(...)`` and re-validates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    aggregator: Optional[Aggregator] = None
    dispersion: Optional[DispersionKind] = None
    min_n: int = Field(default=5, ge=1)
    ddof: int = Field(default=1, ge=0)


__all__ = [
    "ConditionalAggregateParams",
    "Aggregator",
    "DispersionKind",
]

"""Pydantic parameter schema for conditional_aggregate.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``conditional_aggregate``
exposes (Phase 1A — STRICT v5 minimum):

  - ``aggregator``  ∈ {mean, median, count, std}
  - ``dispersion``  ∈ {none, std}
  - ``min_n``       int >= 1 — when the per-offset effective N is
                    below this, the per-offset ``low_n`` flag in the
                    output's lineage metadata is True (the operator
                    does NOT suppress the value; the caller / template
                    / UI decides how to react)

The dispersion knob is REQUIRED to be reported alongside the central
tendency by design — a conditional mean without dispersion is not a
useful answer; the operator refuses to be a one-number-output by
default.

Cross-field constraints:
  - ``aggregator='count'`` REJECTS ``dispersion='std'``: the
    artifact's ``units`` field cannot represent a coherent unit
    for a values column in COUNT and a dispersion column in the
    panel's original units (BPS / PERCENT / ...).  Caller must
    pair count with dispersion='none' explicitly.

Deferred per planned_extensions:

  - ``aggregator`` ∈ {sum, min, max, quantile}
  - ``dispersion`` ∈ {iqr, mad, bootstrap_ci}
  - ``ddof`` knob for std (currently fixed at 1 — sample std,
    Bessel's correction; matches the rest of shared.analytics)
  - ``weighting`` (overlap-de-weighted aggregation)
  - ``MaskedSeries`` input overload (Q2 trigger)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


Aggregator = Literal["mean", "median", "count", "std"]
DispersionKind = Literal["none", "std"]


class ConditionalAggregateParams(BaseModel):
    """Parameters for ``conditional_aggregate`` (Phase 1A surface).

    ``aggregator`` and ``dispersion`` default to ``None`` so the YAML
    is authoritative for those defaults at runtime — same pattern as
    ``threshold_events`` and ``event_windows`` post the Codex
    follow-ups (PR #53, PR #55).  The operator resolves ``None`` →
    ``config.default_value(...)`` and re-validates.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    aggregator: Optional[Aggregator] = None
    dispersion: Optional[DispersionKind] = None
    min_n: int = Field(default=5, ge=1)

    @model_validator(mode="after")
    def _validate_count_dispersion_combo(self) -> "ConditionalAggregateParams":
        # When BOTH fields are concrete (i.e., not waiting for
        # config-resolution), enforce the cross-field constraint.
        # The operator re-validates after resolving defaults so the
        # check fires on the resolved combo too.
        if self.aggregator == "count" and self.dispersion == "std":
            raise ValueError(
                "conditional_aggregate: aggregator='count' with "
                "dispersion='std' is not a coherent combination — the "
                "values column would be in COUNT units while the "
                "dispersion column would be in the panel's original "
                "units, and the artifact has only one ``units`` field.  "
                "Pair count with dispersion='none', or pick a different "
                "aggregator."
            )
        return self


__all__ = [
    "ConditionalAggregateParams",
    "Aggregator",
    "DispersionKind",
]

"""Pydantic parameter schema for threshold_events.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``threshold_events`` exposes:

  - ``rule``              ∈ {abs_above, above, below}
  - ``threshold``         scalar applied to the chosen basis
  - ``threshold_basis``   ∈ {raw_value, rolling_zscore}
  - ``rolling_window``    Optional[int]  — required when basis is
                          ``rolling_zscore``
  - ``min_periods``       Optional[int]  — minimum observations before
                          a rolling z-score is defined; defaults to
                          rolling_window when None
  - ``look_ahead_safe``   bool — load-bearing knob (see below)

The ``look_ahead_safe`` knob is the operator's most important
exposure.  When True (default), the rolling stats used for the
threshold at time ``t`` are computed using only data up to ``t-1`` —
i.e. shifted by one period.  This is the standard event-study
hygiene: an event at time ``t`` cannot have been "predicted" by stats
that included observation ``t`` itself.  Pass False to opt into
in-sample contamination explicitly; the choice is recorded in
lineage either way, so the workflow's methodology summary always
shows what hygiene was applied.

Phase 1A surface is intentionally minimal (build plan v5 — strip to
the Q1 minimum).  Deferred per the operator's planned_extensions:

  - ``between`` / ``outside`` / ``abs_below``
  - ``expanding_zscore`` / ``rolling_quantile`` bases
  - ``event_dedup`` policies (consecutive_collapse / min_gap_days)
  - multi-series fan-out (use the future ``map`` meta-operator)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


ThresholdRule = Literal["abs_above", "above", "below"]
ThresholdBasis = Literal["raw_value", "rolling_zscore"]


class ThresholdEventsParams(BaseModel):
    """Parameters for ``threshold_events`` (Phase 1A surface)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: ThresholdRule
    threshold: float
    threshold_basis: ThresholdBasis = "raw_value"
    rolling_window: Optional[int] = Field(default=None, ge=2)
    min_periods: Optional[int] = Field(default=None, ge=1)
    look_ahead_safe: bool = True

    @model_validator(mode="after")
    def _validate_basis_args(self) -> "ThresholdEventsParams":
        if self.threshold_basis == "rolling_zscore":
            if self.rolling_window is None:
                raise ValueError(
                    "threshold_basis='rolling_zscore' requires "
                    "rolling_window to be set."
                )
            if self.min_periods is not None and self.min_periods > self.rolling_window:
                raise ValueError(
                    f"min_periods={self.min_periods} cannot exceed "
                    f"rolling_window={self.rolling_window}."
                )
        else:
            # raw_value basis: rolling args are irrelevant (and would
            # silently mislead lineage if accepted).
            if self.rolling_window is not None or self.min_periods is not None:
                raise ValueError(
                    "threshold_basis='raw_value' does not use "
                    "rolling_window or min_periods; omit them."
                )
        # rule='abs_above' implies threshold >= 0 (a negative absolute-
        # value threshold catches everything; almost certainly a bug).
        if self.rule == "abs_above" and self.threshold < 0:
            raise ValueError(
                f"rule='abs_above' with threshold={self.threshold} < 0 "
                "would match every observation; pass a non-negative "
                "threshold or pick a different rule."
            )
        return self


__all__ = [
    "ThresholdEventsParams",
    "ThresholdRule",
    "ThresholdBasis",
]

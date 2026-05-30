"""Pydantic parameter schema for event_windows.

Per the operator architecture doc: consequential method variants are
exposed as caller-controlled parameters.  ``event_windows`` exposes:

  - ``pre_window``                 int >= 0 — days BEFORE the event
                                   (negative offsets in the window)
  - ``post_window``                int >= 0 — days AFTER the event
                                   (positive offsets)
  - ``inclusive_event_day``        bool — does the event day itself
                                   (offset 0) count as a window cell
  - ``incomplete_window_policy``   ∈ {drop, pad_nan} — how to treat
                                   events whose window falls off the
                                   edge of the target series
  - ``units_basis``                ∈ {raw, level_change} — what
                                   each cell value MEANS
  - ``require_matching_frequency`` bool — frequency-tag agreement
                                   between events.mask and target
  - ``require_matching_missingness`` bool — OPR11 uniformity flag
                                   (vacuous: an EventSet is a boolean
                                   mask with no missingness regime)

Phase 1A surface is intentionally minimal (build plan v5 — strip to
the Q1 minimum).  Per the v5 plan + R3, structural metadata is
load-bearing: the operator checks index identity AND frequency
compatibility between ``events`` and ``target``.

The ``units_basis="level_change"`` knob is the operator's only
honest unit transition: a percent target's level-change is in
basis points (×100), and the lineage records the transition.
That trade-off was made in v5 because the conversion is
methodology-driven, not arithmetic — it cannot live in the
strict ``series_arithmetic`` operator without coupling unit
algebra to operation semantics.

Deferred per planned_extensions:

  - ``overlap_policy`` (drop / merge overlapping windows; v1 keeps
    them and records overlap stats in lineage)
  - ``log_return`` / ``pct_change`` units bases
  - ``keep_partial`` policy (in-between drop + pad_nan)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


IncompleteWindowPolicy = Literal["drop", "pad_nan"]
UnitsBasis = Literal["raw", "level_change"]


class EventWindowsParams(BaseModel):
    """Parameters for ``event_windows`` (Phase 1A surface).

    ``incomplete_window_policy`` and ``units_basis`` default to None so
    the YAML is authoritative for those defaults at runtime — the
    operator resolves None → ``config.default_value(...)`` and
    re-validates.  Same pattern as ``threshold_events`` after the
    Codex P2 follow-up (PR #53).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pre_window: int = Field(default=0, ge=0)
    post_window: int = Field(default=0, ge=0)
    inclusive_event_day: bool = True
    incomplete_window_policy: Optional[IncompleteWindowPolicy] = None
    units_basis: Optional[UnitsBasis] = None
    require_matching_frequency: bool = True
    # OPR11 uniformity flag.  An EventSet is a boolean mask with no
    # missingness regime, so there is no second policy to compare against
    # — the flag is exposed strict-by-default for symmetry with the other
    # multi-artifact operators, but the check is vacuous (the windowed
    # output's values come from the target Series).
    require_matching_missingness: bool = True

    @model_validator(mode="after")
    def _validate_window(self) -> "EventWindowsParams":
        # A degenerate window (pre=0, post=0, inclusive=False) would
        # produce zero-width panels — almost certainly a bug.
        if (
            self.pre_window == 0
            and self.post_window == 0
            and not self.inclusive_event_day
        ):
            raise ValueError(
                "event_windows: pre_window=0, post_window=0, and "
                "inclusive_event_day=False produces a zero-width "
                "window.  Set at least one offset > 0 or include "
                "the event day."
            )
        return self


__all__ = [
    "EventWindowsParams",
    "IncompleteWindowPolicy",
    "UnitsBasis",
]

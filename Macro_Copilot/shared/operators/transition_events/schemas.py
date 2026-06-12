"""transition_events — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``transition_events`` deliberately has ZERO knobs in v1 (the
Warden's minimal-surface ruling): every change of the discrete label
between ADJACENT finite rows is an event; filtering (only certain
from→to pairs, ignoring certain labels) is declared in
``methodology.planned_extensions``, never a silent default.  The empty
frozen params model keeps the OPR8 ``params: Optional[...] = None``
surface uniform across the toolbox (the streak precedent).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class TransitionEventsParams(BaseModel):
    """Parameters for the ``transition_events`` operator (none in v1 —
    every adjacent-row label change is an event; transition filtering
    is a declared planned extension)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["TransitionEventsParams"]

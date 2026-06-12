"""streak — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field.  ``streak`` deliberately has ZERO knobs in v1: the input is a
SIGN-able Series (threshold/condition logic composes UPSTREAM — e.g.
``series_arithmetic(subtract level)`` for "days above level"), the
sign rule (>0 positive run, <0 negative run, ==0 boundary) and the
NaN policy (break — a gap never silently extends a run) are
DESIGN-LOCKED and lineage-stamped, not parameters.  An empty frozen
params model keeps the OPR8 ``params: Optional[...] = None`` surface
uniform across the toolbox.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StreakParams(BaseModel):
    """Parameters for the ``streak`` operator (none in v1 — the sign
    rule and NaN policy are design-locked; condition logic composes
    upstream)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


__all__ = ["StreakParams"]

"""top_n — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

Single-input operator (one SeriesSet): no ``require_matching_*`` flags;
the within-set same-units requirement is a hard mathematical
precondition of selecting by value, enforced in code with no opt-out
(the ``cross_sectional_rank`` doctrine).

Tie-breaking is DESIGN-LOCKED to pandas ``rank(method='first')`` —
deterministic position-order tie resolution (OPR14: identical inputs
must select identical members).  It is documented here and recorded in
lineage rather than exposed as a knob: a non-deterministic or
caller-varied tie rule would break replay identity for no analytical
gain.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TopNMode = Literal["top", "bottom"]


class TopNParams(BaseModel):
    """Parameters for the ``top_n`` operator.

    Variants:
      - ``n``           — how many members to keep at each date.
      - ``mode``        — top (largest values kept — the default) |
                          bottom (smallest values kept).
      - ``min_members`` — minimum non-NaN members at a date for that
                          date's selection to be made; dates below the
                          floor emit NaN for every member.  When the
                          date has fewer than ``n`` (but at least
                          ``min_members``) valid members, ALL of them
                          are kept (documented, not an error).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int = Field(default=5, ge=1)
    mode: TopNMode = "top"
    min_members: int = Field(default=2, ge=2)


__all__ = ["TopNParams", "TopNMode"]

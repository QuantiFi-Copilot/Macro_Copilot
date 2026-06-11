"""weighted_combination — parameter schema.

Per OPR8 the operator exposes ``params: Optional[...] = None``, but the
``weights`` mapping is the basket definition itself — a per-call input
with NO meaningful default (the ``select_from_series_set.series_key``
precedent): when ``params`` is omitted the operator refuses with a
typed error naming the required field, and the YAML ``defaults:`` block
is explicitly empty.

Single-input operator (one SeriesSet): no ``require_matching_*`` flags;
the same-units requirement across the NAMED members is a hard
mathematical precondition of a weighted sum, enforced in code with no
opt-out.
"""

from __future__ import annotations

from typing import Dict

from pydantic import BaseModel, ConfigDict, Field


class WeightedCombinationParams(BaseModel):
    """Parameters for the ``weighted_combination`` operator.

    Fields:
      - ``weights`` — the basket definition: an explicit
        ``member_key → weight`` mapping.  Keys NAME the participating
        members (members absent from the mapping are excluded); every
        named key must exist in the input set; weights must be finite
        and non-zero (to exclude a member, omit its key); NEGATIVE
        weights are first-class (relative-value legs); at least 2
        members must be named (a 1-key combination is plain scaling —
        use series_arithmetic multiply with a scalar literal).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    weights: Dict[str, float] = Field(..., min_length=2)


__all__ = ["WeightedCombinationParams"]

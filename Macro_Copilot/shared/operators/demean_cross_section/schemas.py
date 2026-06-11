"""demean_cross_section — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

Single-input operator (one SeriesSet): no ``require_matching_*`` flags;
the within-set same-units requirement is a hard mathematical
precondition of subtracting a cross-member mean, enforced in code with
no opt-out (the ``cross_sectional_rank`` doctrine).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DemeanCrossSectionParams(BaseModel):
    """Parameters for the ``demean_cross_section`` operator.

    Variants:
      - ``min_members`` — minimum non-NaN members at a date for that
                          date's demeaned values to be emitted; dates
                          below the floor emit NaN for every member.

    Mean centering only in v1; robust (median) centering is declared
    in ``methodology.planned_extensions`` (the zscore sibling's
    robust-variant pattern).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    min_members: int = Field(default=2, ge=2)


__all__ = ["DemeanCrossSectionParams"]

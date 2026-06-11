"""cross_sectional_zscore — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

Single-input operator (one SeriesSet): no ``require_matching_*`` flags;
the within-set same-units requirement is a hard mathematical
precondition of cross-member standardisation, enforced in code with no
opt-out (the ``cross_sectional_rank`` doctrine).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CrossSectionalZscoreParams(BaseModel):
    """Parameters for the ``cross_sectional_zscore`` operator.

    Variants:
      - ``ddof``        — denominator degrees-of-freedom delta for the
                          per-date cross-sectional standard deviation:
                          1 (sample, Bessel-corrected — the default) or
                          0 (population).
      - ``min_members`` — minimum non-NaN members at a date for that
                          date's z-scores to be emitted; dates below
                          the floor emit NaN for every member.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ddof: int = Field(default=1, ge=0, le=1)
    min_members: int = Field(default=2, ge=2)


__all__ = ["CrossSectionalZscoreParams"]

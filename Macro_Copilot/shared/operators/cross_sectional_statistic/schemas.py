"""cross_sectional_statistic — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

Single-input operator (one SeriesSet): no ``require_matching_*`` flags;
the within-set same-units requirement is a hard mathematical
precondition of any cross-member summary, enforced in code with no
opt-out (the ``cross_sectional_rank`` doctrine).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# One cohesive reduction family (OPR2): every statistic reduces the
# per-date cross-section of member VALUES to one number in the members'
# common unit.  ``count`` is deliberately excluded — it is a statistic
# of PRESENCE (COUNT units), not of the values, and would break the
# family's uniform unit-passthrough algebra.
CrossSectionalStatistic = Literal["mean", "median", "std", "min", "max", "sum"]


class CrossSectionalStatisticParams(BaseModel):
    """Parameters for the ``cross_sectional_statistic`` operator.

    Variants:
      - ``statistic``   — the per-date reduction across the non-NaN
                          members: mean | median | std | min | max |
                          sum.  ``std`` is the cross-sectional
                          dispersion gauge.
      - ``ddof``        — denominator degrees-of-freedom delta for
                          ``statistic='std'`` (1 = sample, the desk
                          default; 0 = population).  Ignored — and
                          nulled in lineage params per OPR14 — for
                          every other statistic.
      - ``min_members`` — minimum non-NaN members at a date for that
                          date's summary to be emitted; dates below
                          the floor emit NaN.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statistic: CrossSectionalStatistic = "mean"
    ddof: int = Field(default=1, ge=0, le=1)
    min_members: int = Field(default=2, ge=2)


__all__ = ["CrossSectionalStatisticParams", "CrossSectionalStatistic"]

"""cumulative — parameter schema.

Per OPR8: every consequential method variant is an explicit typed
field; each field's schema default mirrors the ``config.yaml`` default.
A test asserts the two never diverge (OPR8).

``product`` is DELIBERATELY EXCLUDED from the statistic set: the
cumulative product of a unit-bearing series is dimensionally dishonest
(unit^n grows without bound under OPR11's unit algebra; it is only
meaningful on dimensionless growth factors).  Declared in
``methodology.planned_extensions`` with the unit question documented.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


# One cohesive running-reduction family (OPR2): every statistic carries
# the input's unit through unchanged.
CumulativeStatistic = Literal["sum", "max", "min"]


class CumulativeParams(BaseModel):
    """Parameters for the ``cumulative`` operator.

    Variants:
      - ``statistic`` — the running reduction from the first row:
        sum (running total) | max (running peak) | min (running
        trough).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statistic: CumulativeStatistic = "sum"


__all__ = ["CumulativeParams", "CumulativeStatistic"]

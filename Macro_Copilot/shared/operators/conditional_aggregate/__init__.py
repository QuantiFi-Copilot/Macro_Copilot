"""shared.operators.conditional_aggregate — reduce a WindowedPanel to per-offset stats.

Operator 5 of the Phase 1A milestone (build plan v5) — the FINAL
operator.  Sits in the ``aggregation`` method family.  Closes the
event-study skeleton end-to-end: with this operator merged, Q1
runs from fetch → adapter → align → arithmetic → threshold →
event_windows → conditional_aggregate, and the workflow-level
methodology summary can be derived programmatically from the
output artifact's lineage.

Public surface (Phase 1A — minimum for Q1):

  - ``conditional_aggregate``       the operator itself
  - ``ConditionalAggregateParams``  typed parameter object
  - ``CONFIG_PATH``                 path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.conditional_aggregate.operator import conditional_aggregate
from shared.operators.conditional_aggregate.schemas import (
    ConditionalAggregateParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "conditional_aggregate",
    "ConditionalAggregateParams",
    "CONFIG_PATH",
]

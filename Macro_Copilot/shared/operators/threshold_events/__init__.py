"""shared.operators.threshold_events — convert Series → EventSet via a threshold.

Operator 3 of the Phase 1A milestone (build plan v5).  Sits in the
``masking`` method family.  See:

  - docs/architecture/operator_architecture.md
  - this folder's config.yaml for the methodology defaults
  - operator.py for the pure compute() function

Public surface (Phase 1A — minimum for Q1 / regime detection):

  - ``threshold_events``       the operator itself
  - ``ThresholdEventsParams``  typed parameter object
  - ``CONFIG_PATH``            path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.threshold_events.operator import threshold_events, ThresholdEventsError
from shared.operators.threshold_events.schemas import ThresholdEventsParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "threshold_events",
    "ThresholdEventsError",
    "ThresholdEventsParams",
    "CONFIG_PATH",
]

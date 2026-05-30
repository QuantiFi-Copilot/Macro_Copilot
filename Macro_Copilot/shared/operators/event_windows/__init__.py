"""shared.operators.event_windows — extract WindowedPanel from EventSet + Series.

Operator 4 of the Phase 1A milestone (build plan v5).  Sits in the
``windowing`` method family.  See:

  - docs/architecture/operator_architecture.md
  - this folder's config.yaml for the methodology defaults
  - operator.py for the pure compute() function

Public surface (Phase 1A — minimum for Q1 event-study):

  - ``event_windows``        the operator itself
  - ``EventWindowsParams``   typed parameter object
  - ``CONFIG_PATH``          path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.event_windows.operator import event_windows, EventWindowsError
from shared.operators.event_windows.schemas import EventWindowsParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "event_windows",
    "EventWindowsError",
    "EventWindowsParams",
    "CONFIG_PATH",
]

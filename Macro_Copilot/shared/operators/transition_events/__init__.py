"""shared.operators.transition_events — public API.

Stable re-exports so callers can ``from shared.operators.
transition_events import transition_events`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.transition_events.operator import (
    transition_events,
    TransitionEventsError,
)
from shared.operators.transition_events.schemas import TransitionEventsParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "transition_events",
    "TransitionEventsError",
    "TransitionEventsParams",
    "CONFIG_PATH",
]

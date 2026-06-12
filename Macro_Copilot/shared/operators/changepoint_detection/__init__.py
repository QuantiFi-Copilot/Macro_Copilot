"""shared.operators.changepoint_detection — public API.

Stable re-exports so callers can ``from shared.operators.
changepoint_detection import changepoint_detection`` without reaching
into submodules.  The four exports (CONFIG_PATH, the operator, the
Params, the Error) are the OPR16 ``__init__``-export contract every
operator satisfies.
"""

from pathlib import Path

from shared.operators.changepoint_detection.operator import (
    changepoint_detection,
    ChangepointDetectionError,
)
from shared.operators.changepoint_detection.schemas import (
    ChangepointDetectionParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "changepoint_detection",
    "ChangepointDetectionError",
    "ChangepointDetectionParams",
    "CONFIG_PATH",
]

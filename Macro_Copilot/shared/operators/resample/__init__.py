"""shared.operators.resample — public API.

Stable re-exports so callers can ``from shared.operators.resample
import resample`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.resample.operator import resample, ResampleError
from shared.operators.resample.schemas import (
    ResampleFrequency,
    ResampleMethod,
    ResampleParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "resample",
    "ResampleError",
    "ResampleParams",
    "ResampleFrequency",
    "ResampleMethod",
    "CONFIG_PATH",
]

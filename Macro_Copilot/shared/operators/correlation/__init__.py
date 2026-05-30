"""shared.operators.correlation — public API.

Stable re-exports so callers can ``from shared.operators.correlation
import correlation`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.correlation.operator import correlation, CorrelationError
from shared.operators.correlation.schemas import (
    CorrelationMethod,
    CorrelationParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "correlation",
    "CorrelationError",
    "CorrelationParams",
    "CorrelationMethod",
    "CONFIG_PATH",
]

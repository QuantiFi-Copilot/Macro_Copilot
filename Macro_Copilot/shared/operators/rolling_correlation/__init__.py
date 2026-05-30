"""shared.operators.rolling_correlation — public API.

Stable re-exports so callers can ``from shared.operators.rolling_correlation
import rolling_correlation`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.rolling_correlation.operator import (
    RollingCorrelationError,
    rolling_correlation,
)
from shared.operators.rolling_correlation.schemas import (
    RollingCorrelationMethod,
    RollingCorrelationParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "rolling_correlation",
    "RollingCorrelationError",
    "RollingCorrelationParams",
    "RollingCorrelationMethod",
    "CONFIG_PATH",
]

"""shared.operators.rolling_statistic — public API.

Stable re-exports so callers can ``from shared.operators.rolling_statistic
import rolling_statistic`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.rolling_statistic.operator import (
    RollingStatisticError,
    rolling_statistic,
)
from shared.operators.rolling_statistic.schemas import (
    RollingStatisticName,
    RollingStatisticParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "rolling_statistic",
    "RollingStatisticError",
    "RollingStatisticParams",
    "RollingStatisticName",
    "CONFIG_PATH",
]

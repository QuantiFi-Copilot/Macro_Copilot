"""shared.operators.ewm_statistic — public API.

Stable re-exports so callers can ``from shared.operators.ewm_statistic
import ewm_statistic`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.ewm_statistic.operator import (
    ewm_statistic,
    EwmStatisticError,
)
from shared.operators.ewm_statistic.schemas import (
    EwmStatistic,
    EwmStatisticParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "ewm_statistic",
    "EwmStatisticError",
    "EwmStatisticParams",
    "EwmStatistic",
    "CONFIG_PATH",
]

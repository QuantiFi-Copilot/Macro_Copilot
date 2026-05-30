"""shared.operators.rolling_zscore — public API.

Stable re-exports so callers can ``from shared.operators.rolling_zscore
import rolling_zscore`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.rolling_zscore.operator import (
    RollingZscoreError,
    rolling_zscore,
)
from shared.operators.rolling_zscore.schemas import RollingZscoreParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "rolling_zscore",
    "RollingZscoreError",
    "RollingZscoreParams",
    "CONFIG_PATH",
]

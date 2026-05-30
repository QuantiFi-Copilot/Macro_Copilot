"""shared.operators.cointegration — public API.

Stable re-exports so callers can ``from shared.operators.cointegration
import cointegration`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.cointegration.operator import (
    CointegrationError,
    cointegration,
)
from shared.operators.cointegration.schemas import (
    CointegrationAutolag,
    CointegrationMethod,
    CointegrationParams,
    CointegrationTrend,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "cointegration",
    "CointegrationError",
    "CointegrationParams",
    "CointegrationMethod",
    "CointegrationTrend",
    "CointegrationAutolag",
    "CONFIG_PATH",
]

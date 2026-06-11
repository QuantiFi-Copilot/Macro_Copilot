"""shared.operators.rolling_covariance — public API.

Stable re-exports so callers can ``from shared.operators.
rolling_covariance import rolling_covariance`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.rolling_covariance.operator import (
    rolling_covariance,
    RollingCovarianceError,
)
from shared.operators.rolling_covariance.schemas import RollingCovarianceParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "rolling_covariance",
    "RollingCovarianceError",
    "RollingCovarianceParams",
    "CONFIG_PATH",
]

"""shared.operators.covariance — public API.

Stable re-exports so callers can ``from shared.operators.covariance
import covariance`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.covariance.operator import covariance, CovarianceError
from shared.operators.covariance.schemas import CovarianceParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "covariance",
    "CovarianceError",
    "CovarianceParams",
    "CONFIG_PATH",
]

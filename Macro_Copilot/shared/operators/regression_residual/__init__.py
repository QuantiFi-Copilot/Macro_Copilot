"""shared.operators.regression_residual — public API.

Stable re-exports so callers can ``from shared.operators.
regression_residual import regression_residual`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.regression_residual.operator import (
    regression_residual,
    RegressionResidualError,
)
from shared.operators.regression_residual.schemas import (
    RegressionResidualParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "regression_residual",
    "RegressionResidualError",
    "RegressionResidualParams",
    "CONFIG_PATH",
]

"""shared.operators.fit_kalman — public API.

Stable re-exports so callers can ``from shared.operators.fit_kalman
import fit_kalman`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.fit_kalman.operator import fit_kalman, FitKalmanError
from shared.operators.fit_kalman.schemas import FitKalmanParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "fit_kalman",
    "FitKalmanError",
    "FitKalmanParams",
    "CONFIG_PATH",
]

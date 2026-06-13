"""shared.operators.rolling_pca — public API.

Stable re-exports so callers can ``from shared.operators.rolling_pca
import rolling_pca`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.rolling_pca.operator import rolling_pca, RollingPcaError
from shared.operators.rolling_pca.schemas import RollingPcaParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "rolling_pca",
    "RollingPcaError",
    "RollingPcaParams",
    "CONFIG_PATH",
]

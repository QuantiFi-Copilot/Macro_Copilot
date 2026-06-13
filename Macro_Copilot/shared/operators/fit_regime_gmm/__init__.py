"""shared.operators.fit_regime_gmm — public API.

Stable re-exports so callers can ``from shared.operators.
fit_regime_gmm import fit_regime_gmm`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.fit_regime_gmm.operator import (
    fit_regime_gmm,
    FitRegimeGmmError,
)
from shared.operators.fit_regime_gmm.schemas import FitRegimeGmmParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "fit_regime_gmm",
    "FitRegimeGmmError",
    "FitRegimeGmmParams",
    "CONFIG_PATH",
]

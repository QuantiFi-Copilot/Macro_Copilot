"""shared.operators.fit_regime_hmm — public API.

Stable re-exports so callers can ``from shared.operators.
fit_regime_hmm import fit_regime_hmm`` without reaching into submodules.
The four exports (CONFIG_PATH, the operator, the Params, the Error) are
the OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.fit_regime_hmm.operator import (
    fit_regime_hmm,
    FitRegimeHmmError,
)
from shared.operators.fit_regime_hmm.schemas import FitRegimeHmmParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "fit_regime_hmm",
    "FitRegimeHmmError",
    "FitRegimeHmmParams",
    "CONFIG_PATH",
]

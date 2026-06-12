"""shared.operators.fit_ou — public API.

Stable re-exports so callers can ``from shared.operators.fit_ou import
fit_ou`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.fit_ou.operator import fit_ou, FitOuError
from shared.operators.fit_ou.schemas import FitOuParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "fit_ou",
    "FitOuError",
    "FitOuParams",
    "CONFIG_PATH",
]

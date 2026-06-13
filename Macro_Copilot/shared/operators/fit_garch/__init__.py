"""shared.operators.fit_garch — public API.

Stable re-exports so callers can ``from shared.operators.fit_garch
import fit_garch`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.fit_garch.operator import fit_garch, FitGarchError
from shared.operators.fit_garch.schemas import FitGarchParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "fit_garch",
    "FitGarchError",
    "FitGarchParams",
    "CONFIG_PATH",
]

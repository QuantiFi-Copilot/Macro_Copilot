"""shared.operators.detrend — public API.

Stable re-exports so callers can ``from shared.operators.detrend
import detrend`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.detrend.operator import detrend, DetrendError
from shared.operators.detrend.schemas import DetrendMethod, DetrendParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "detrend",
    "DetrendError",
    "DetrendParams",
    "DetrendMethod",
    "CONFIG_PATH",
]

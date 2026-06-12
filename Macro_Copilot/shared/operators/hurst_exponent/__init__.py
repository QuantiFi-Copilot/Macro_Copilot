"""shared.operators.hurst_exponent — public API.

Stable re-exports so callers can ``from shared.operators.
hurst_exponent import hurst_exponent`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.hurst_exponent.operator import (
    hurst_exponent,
    HurstExponentError,
)
from shared.operators.hurst_exponent.schemas import HurstExponentParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "hurst_exponent",
    "HurstExponentError",
    "HurstExponentParams",
    "CONFIG_PATH",
]

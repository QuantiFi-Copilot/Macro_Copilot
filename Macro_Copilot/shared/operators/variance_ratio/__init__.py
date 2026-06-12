"""shared.operators.variance_ratio — public API.

Stable re-exports so callers can ``from shared.operators.
variance_ratio import variance_ratio`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.variance_ratio.operator import (
    variance_ratio,
    VarianceRatioError,
)
from shared.operators.variance_ratio.schemas import VarianceRatioParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "variance_ratio",
    "VarianceRatioError",
    "VarianceRatioParams",
    "CONFIG_PATH",
]

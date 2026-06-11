"""shared.operators.weighted_combination — public API.

Stable re-exports so callers can ``from shared.operators.
weighted_combination import weighted_combination`` without reaching
into submodules.  The four exports (CONFIG_PATH, the operator, the
Params, the Error) are the OPR16 ``__init__``-export contract every
operator satisfies.
"""

from pathlib import Path

from shared.operators.weighted_combination.operator import (
    weighted_combination,
    WeightedCombinationError,
)
from shared.operators.weighted_combination.schemas import (
    WeightedCombinationParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "weighted_combination",
    "WeightedCombinationError",
    "WeightedCombinationParams",
    "CONFIG_PATH",
]

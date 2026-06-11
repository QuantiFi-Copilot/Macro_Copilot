"""shared.operators.pairwise_spread_matrix — public API.

Stable re-exports so callers can ``from shared.operators.
pairwise_spread_matrix import pairwise_spread_matrix`` without reaching
into submodules.  The four exports (CONFIG_PATH, the operator, the
Params, the Error) are the OPR16 ``__init__``-export contract every
operator satisfies.
"""

from pathlib import Path

from shared.operators.pairwise_spread_matrix.operator import (
    pairwise_spread_matrix,
    PairwiseSpreadMatrixError,
)
from shared.operators.pairwise_spread_matrix.schemas import (
    PairwiseSpreadMatrixParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "pairwise_spread_matrix",
    "PairwiseSpreadMatrixError",
    "PairwiseSpreadMatrixParams",
    "CONFIG_PATH",
]

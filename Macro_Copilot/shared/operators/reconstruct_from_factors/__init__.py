"""shared.operators.reconstruct_from_factors — public API.

Stable re-exports so callers can ``from shared.operators.
reconstruct_from_factors import reconstruct_from_factors`` without
reaching into submodules.  The four exports (CONFIG_PATH, the operator,
the Params, the Error) are the OPR16 ``__init__``-export contract every
operator satisfies.
"""

from pathlib import Path

from shared.operators.reconstruct_from_factors.operator import (
    reconstruct_from_factors,
    ReconstructFromFactorsError,
)
from shared.operators.reconstruct_from_factors.schemas import (
    ReconstructFromFactorsParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "reconstruct_from_factors",
    "ReconstructFromFactorsError",
    "ReconstructFromFactorsParams",
    "CONFIG_PATH",
]

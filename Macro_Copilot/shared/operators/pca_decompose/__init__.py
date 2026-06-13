"""shared.operators.pca_decompose — public API.

Stable re-exports so callers can ``from shared.operators.pca_decompose
import pca_decompose`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.pca_decompose.operator import (
    pca_decompose,
    PcaDecomposeError,
)
from shared.operators.pca_decompose.schemas import PcaDecomposeParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "pca_decompose",
    "PcaDecomposeError",
    "PcaDecomposeParams",
    "CONFIG_PATH",
]

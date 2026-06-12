"""shared.operators.normality_test — public API.

Stable re-exports so callers can ``from shared.operators.
normality_test import normality_test`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.normality_test.operator import (
    normality_test,
    NormalityTestError,
)
from shared.operators.normality_test.schemas import NormalityTestParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "normality_test",
    "NormalityTestError",
    "NormalityTestParams",
    "CONFIG_PATH",
]

"""shared.operators.granger_causality — public API.

Stable re-exports so callers can ``from shared.operators.
granger_causality import granger_causality`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.granger_causality.operator import (
    granger_causality,
    GrangerCausalityError,
)
from shared.operators.granger_causality.schemas import (
    GrangerCausalityParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "granger_causality",
    "GrangerCausalityError",
    "GrangerCausalityParams",
    "CONFIG_PATH",
]

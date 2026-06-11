"""shared.operators.beta — public API.

Stable re-exports so callers can ``from shared.operators.beta import
beta`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.beta.operator import beta, BetaError
from shared.operators.beta.schemas import BetaParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "beta",
    "BetaError",
    "BetaParams",
    "CONFIG_PATH",
]

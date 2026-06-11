"""shared.operators.cumulative — public API.

Stable re-exports so callers can ``from shared.operators.cumulative
import cumulative`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.cumulative.operator import cumulative, CumulativeError
from shared.operators.cumulative.schemas import (
    CumulativeParams,
    CumulativeStatistic,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "cumulative",
    "CumulativeError",
    "CumulativeParams",
    "CumulativeStatistic",
    "CONFIG_PATH",
]

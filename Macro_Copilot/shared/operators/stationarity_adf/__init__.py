"""shared.operators.stationarity_adf — public API.

Stable re-exports so callers can ``from shared.operators.
stationarity_adf import stationarity_adf`` without reaching into
submodules.  The four exports (CONFIG_PATH, the operator, the Params,
the Error) are the OPR16 ``__init__``-export contract every operator
satisfies.
"""

from pathlib import Path

from shared.operators.stationarity_adf.operator import (
    stationarity_adf,
    StationarityAdfError,
)
from shared.operators.stationarity_adf.schemas import StationarityAdfParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "stationarity_adf",
    "StationarityAdfError",
    "StationarityAdfParams",
    "CONFIG_PATH",
]

"""shared.operators.convert_units — public API.

Stable re-exports so callers can ``from shared.operators.convert_units
import convert_units`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.convert_units.operator import (
    convert_units,
    ConvertUnitsError,
)
from shared.operators.convert_units.schemas import ConvertUnitsParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "convert_units",
    "ConvertUnitsError",
    "ConvertUnitsParams",
    "CONFIG_PATH",
]

"""shared.operators.bandpass — public API.

Stable re-exports so callers can ``from shared.operators.bandpass
import bandpass`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.bandpass.operator import bandpass, BandpassError
from shared.operators.bandpass.schemas import BandpassParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "bandpass",
    "BandpassError",
    "BandpassParams",
    "CONFIG_PATH",
]

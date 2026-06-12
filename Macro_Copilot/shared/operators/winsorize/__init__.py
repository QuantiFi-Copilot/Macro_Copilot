"""shared.operators.winsorize — public API.

Stable re-exports so callers can ``from shared.operators.winsorize
import winsorize`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.winsorize.operator import winsorize, WinsorizeError
from shared.operators.winsorize.schemas import WinsorizeMode, WinsorizeParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "winsorize",
    "WinsorizeError",
    "WinsorizeParams",
    "WinsorizeMode",
    "CONFIG_PATH",
]

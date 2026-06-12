"""shared.operators.streak — public API.

Stable re-exports so callers can ``from shared.operators.streak import
streak`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.streak.operator import streak, StreakError
from shared.operators.streak.schemas import StreakParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = ["streak", "StreakError", "StreakParams", "CONFIG_PATH"]

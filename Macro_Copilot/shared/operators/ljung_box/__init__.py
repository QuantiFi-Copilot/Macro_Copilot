"""shared.operators.ljung_box — public API.

Stable re-exports so callers can ``from shared.operators.ljung_box
import ljung_box`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.ljung_box.operator import ljung_box, LjungBoxError
from shared.operators.ljung_box.schemas import LjungBoxParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = ["ljung_box", "LjungBoxError", "LjungBoxParams", "CONFIG_PATH"]

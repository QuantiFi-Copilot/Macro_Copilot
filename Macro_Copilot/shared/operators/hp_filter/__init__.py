"""shared.operators.hp_filter — public API.

Stable re-exports so callers can ``from shared.operators.hp_filter
import hp_filter`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.hp_filter.operator import hp_filter, HpFilterError
from shared.operators.hp_filter.schemas import HpComponent, HpFilterParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "hp_filter",
    "HpFilterError",
    "HpFilterParams",
    "HpComponent",
    "CONFIG_PATH",
]

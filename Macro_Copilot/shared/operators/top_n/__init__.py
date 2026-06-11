"""shared.operators.top_n — public API.

Stable re-exports so callers can ``from shared.operators.top_n import
top_n`` without reaching into submodules.  The four exports
(CONFIG_PATH, the operator, the Params, the Error) are the OPR16
``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.top_n.operator import top_n, TopNError
from shared.operators.top_n.schemas import TopNMode, TopNParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "top_n",
    "TopNError",
    "TopNParams",
    "TopNMode",
    "CONFIG_PATH",
]

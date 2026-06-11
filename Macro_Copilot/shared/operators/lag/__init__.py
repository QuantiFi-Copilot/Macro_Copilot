"""shared.operators.lag — public API.

Stable re-exports so callers can ``from shared.operators.lag import
lag`` without reaching into submodules.  The four exports (CONFIG_PATH,
the operator, the Params, the Error) are the OPR16 ``__init__``-export
contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.lag.operator import lag, LagError
from shared.operators.lag.schemas import LagParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = ["lag", "LagError", "LagParams", "CONFIG_PATH"]

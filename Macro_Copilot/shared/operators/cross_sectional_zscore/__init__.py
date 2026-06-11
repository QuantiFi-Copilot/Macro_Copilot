"""shared.operators.cross_sectional_zscore — public API.

Stable re-exports so callers can ``from shared.operators.
cross_sectional_zscore import cross_sectional_zscore`` without reaching
into submodules.  The four exports (CONFIG_PATH, the operator, the
Params, the Error) are the OPR16 ``__init__``-export contract every
operator satisfies.
"""

from pathlib import Path

from shared.operators.cross_sectional_zscore.operator import (
    cross_sectional_zscore,
    CrossSectionalZscoreError,
)
from shared.operators.cross_sectional_zscore.schemas import (
    CrossSectionalZscoreParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "cross_sectional_zscore",
    "CrossSectionalZscoreError",
    "CrossSectionalZscoreParams",
    "CONFIG_PATH",
]

"""shared.operators.cross_sectional_statistic — public API.

Stable re-exports so callers can ``from shared.operators.
cross_sectional_statistic import cross_sectional_statistic`` without
reaching into submodules.  The four exports (CONFIG_PATH, the operator,
the Params, the Error) are the OPR16 ``__init__``-export contract every
operator satisfies.
"""

from pathlib import Path

from shared.operators.cross_sectional_statistic.operator import (
    cross_sectional_statistic,
    CrossSectionalStatisticError,
)
from shared.operators.cross_sectional_statistic.schemas import (
    CrossSectionalStatistic,
    CrossSectionalStatisticParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "cross_sectional_statistic",
    "CrossSectionalStatisticError",
    "CrossSectionalStatisticParams",
    "CrossSectionalStatistic",
    "CONFIG_PATH",
]

"""shared.operators.cross_sectional_rank — public API.

Stable re-exports so callers can ``from shared.operators.
cross_sectional_rank import cross_sectional_rank`` without reaching
into submodules.  The four exports (CONFIG_PATH, the operator, the
Params, the Error) are the OPR16 ``__init__``-export contract every
operator satisfies.
"""

from pathlib import Path

from shared.operators.cross_sectional_rank.operator import (
    cross_sectional_rank,
    CrossSectionalRankError,
)
from shared.operators.cross_sectional_rank.schemas import (
    CrossSectionalRankParams,
    RankMethod,
    RankTies,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "cross_sectional_rank",
    "CrossSectionalRankError",
    "CrossSectionalRankParams",
    "RankMethod",
    "RankTies",
    "CONFIG_PATH",
]

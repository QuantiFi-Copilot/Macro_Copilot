"""shared.operators.percentile_rank — public API.

Stable re-exports so callers can ``from shared.operators.percentile_rank
import percentile_rank`` without reaching into submodules.  The four
exports (CONFIG_PATH, the operator, the Params, the Error) are the
OPR16 ``__init__``-export contract every operator satisfies.
"""

from pathlib import Path

from shared.operators.percentile_rank.operator import (
    PercentileRankError,
    percentile_rank,
)
from shared.operators.percentile_rank.schemas import (
    PercentileRankMethod,
    PercentileRankParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "percentile_rank",
    "PercentileRankError",
    "PercentileRankParams",
    "PercentileRankMethod",
    "CONFIG_PATH",
]

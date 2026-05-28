"""shared.operators.cross_sectional_rank — finance-blind cross-sectional ranking.

Owns the **ranking** structural method family: for each row (date) of
a typed ``Panel``, ranks the columns against each other and emits a
``Panel`` whose cells are the per-date ranks (ordinal / percentile /
normalized per ``method``).

Listed as a Pass example in docs/architecture/operator_architecture.md.
V1 takes a ``Panel`` (consistent with ``rolling_zscore_panel`` and the
wide-cross-section artifacts that flow through the system); a SeriesSet
adapter is a planned extension.

Scope discipline: ranking ONLY.  Rolling z-score normalization ships
as the separate ``rolling_zscore_panel`` operator.

Public surface:

  - ``cross_sectional_rank``       the operator itself
  - ``CrossSectionalRankError``    typed user-facing error
  - ``CrossSectionalRankParams``   typed parameter object
  - ``CONFIG_PATH``                path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.cross_sectional_rank.operator import (
    cross_sectional_rank,
    CrossSectionalRankError,
)
from shared.operators.cross_sectional_rank.schemas import (
    CrossSectionalRankParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "cross_sectional_rank",
    "CrossSectionalRankError",
    "CrossSectionalRankParams",
    "CONFIG_PATH",
]

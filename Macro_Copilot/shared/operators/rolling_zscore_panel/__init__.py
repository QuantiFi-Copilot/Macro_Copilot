"""shared.operators.rolling_zscore_panel — finance-blind rolling z-score.

Owns the **rolling-normalization** structural method family: for each
column of a typed ``Panel``, computes a trailing rolling z-score
``(value - rolling_mean(window)) / rolling_std(window, ddof)`` and
emits a ``Panel`` with ``units_by_column = Z_SCORE``.

Generalises the per-column "trailing-window z-score" logic that six
FX call sites re-implemented (the four Phase F1 scanners plus legacy
``vol_z_score`` / ``vol_scanner``).  Finance-blind: runs unchanged on
rates / equities / temperature panels.

Scope discipline: z-score ONLY.  Cross-sectional rank / percentile
ship as the separate ``cross_sectional_rank`` ranking-family operator
(already a Pass example in the architecture doc).

Public surface:

  - ``rolling_zscore_panel``        the operator itself
  - ``RollingZscorePanelError``     typed user-facing error
  - ``RollingZscorePanelParams``    typed parameter object
  - ``CONFIG_PATH``                 path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.rolling_zscore_panel.operator import (
    rolling_zscore_panel,
    RollingZscorePanelError,
)
from shared.operators.rolling_zscore_panel.schemas import (
    RollingZscorePanelParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "rolling_zscore_panel",
    "RollingZscorePanelError",
    "RollingZscorePanelParams",
    "CONFIG_PATH",
]

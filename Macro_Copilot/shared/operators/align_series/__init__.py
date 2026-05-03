"""shared.operators.align_series — finance-blind index alignment of N typed Series.

The first central operator of the Phase 1A milestone.  See:

  - docs/architecture/operator_architecture.md
  - this folder's config.yaml for the methodology defaults
  - operator.py for the pure compute() function

Public surface (Phase 1A — minimum for Q1):

  - ``align_series``           the operator itself
  - ``AlignSeriesParams``      typed parameter object
  - ``CONFIG_PATH``            path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.align_series.operator import align_series
from shared.operators.align_series.schemas import AlignSeriesParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "align_series",
    "AlignSeriesParams",
    "CONFIG_PATH",
]

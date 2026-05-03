"""shared.operators.series_arithmetic — strict elementwise arithmetic on Series.

Operator 2 of the Phase 1A milestone (build plan v5).  Sits in the
``arithmetic`` method family.  See:

  - docs/architecture/operator_architecture.md
  - this folder's config.yaml for the methodology defaults
  - operator.py for the pure compute() function

Public surface (Phase 1A — minimum for Q1/Q3):

  - ``series_arithmetic``       the operator itself
  - ``SeriesArithmeticParams``  typed parameter object
  - ``CONFIG_PATH``             path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.series_arithmetic.operator import series_arithmetic
from shared.operators.series_arithmetic.schemas import SeriesArithmeticParams


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "series_arithmetic",
    "SeriesArithmeticParams",
    "CONFIG_PATH",
]

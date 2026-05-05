"""shared.operators.rolling_regression — finance-blind rolling-OLS operator.

Phase 2A operator (workflow-templates milestone) that exposes a
typed Series-in / SeriesSet-out surface over the existing
``shared.analytics.regression.rolling_ols`` compute primitive.

Powers the relationship-analysis branch of the
``regime_conditioned_relationship`` archetype: given two Series
(lhs / dependent, rhs / regressor — single regressor in V1), emits
a SeriesSet of {beta, alpha, r_squared} time series so downstream
operators can ``apply_mask`` per regime, ``summarize_series`` each
masked beta, and ``compare`` across regimes.

The operator is finance-blind — it knows nothing about yields,
spreads, or curves.  It only sees typed numeric Series with units;
the unit algebra is owned by this module (PERCENT level_change →
BPS, beta = RATIO, alpha = effective-lhs-unit).

Public surface:

  - ``rolling_regression``       the operator itself
  - ``RollingRegressionParams``  typed parameter object
  - ``CONFIG_PATH``              path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.rolling_regression.operator import (
    rolling_regression,
    RollingRegressionError,
)
from shared.operators.rolling_regression.schemas import (
    RollingRegressionParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "rolling_regression",
    "RollingRegressionError",
    "RollingRegressionParams",
    "CONFIG_PATH",
]

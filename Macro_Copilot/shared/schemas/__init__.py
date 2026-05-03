"""
shared.schemas — cross-tool input/output schema package.

Tools that produce time-series outputs OR consume structured input
specifications (caller-supplied series, pasted PCA loadings, etc.)
share these schemas to keep the contract identical across the
roster.  Existing migrated tools (curve_spread, yield_levels,
butterfly, cross_market_spread, curve_move_classifier) do NOT yet
use this package — their wire shapes are frozen for frontend
compatibility and will be migrated in a separate cleanup PR.

Introduced in the v6 sprint by ``zscore_custom`` (the first new
tool to emit a generic ``TimeSeries`` output).  Subsequent tools in
that sprint (rolling_regression, beta_adjusted_spread, half_life,
pca_yield_curve, yield_change_attribution_pca,
yield_change_decomposition_simple) consume / produce these shapes
uniformly.

See docs/architecture/tool_architecture.md for the canonical layout.
"""

from shared.schemas.time_series import (
    PairSpec,
    PastedPcaComponentMetadata,
    PastedPcaLoadings,
    PastedTimeSeries,
    SeriesSpec,
    TimeSeries,
    TimeSeriesRow,
    TimeSeriesUnits,
)


__all__ = [
    "TimeSeriesUnits",
    "TimeSeriesRow",
    "TimeSeries",
    "SeriesSpec",
    "PairSpec",
    "PastedTimeSeries",
    "PastedPcaComponentMetadata",
    "PastedPcaLoadings",
]

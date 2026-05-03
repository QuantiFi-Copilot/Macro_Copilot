"""shared.artifacts.adapters — bridges from non-artifact inputs to typed artifacts.

Per build plan v5: adapters live in the artifact layer (NOT under
``shared.operators``), because they know primitive-side wire formats /
pandas shapes — which violates the operator-architecture doc's
"finance-blind structural transformation" rule.

Phase 1A adapters:

  - ``raw_dataframe_to_artifact_series``  — fetch + clean DataFrame → Series.
    Used by the operator-layer composition tests and (later) by
    workflow templates that fetch raw data and feed operators.

  - ``time_series_to_artifact_series``    — scaffold for the future
    path where v6 ``TimeSeries``-emitting primitives feed the
    operator layer.  Not exercised in Phase 1A.
"""

from shared.artifacts.adapters.from_raw_dataframe import (
    raw_dataframe_to_artifact_series,
)


__all__ = [
    "raw_dataframe_to_artifact_series",
]

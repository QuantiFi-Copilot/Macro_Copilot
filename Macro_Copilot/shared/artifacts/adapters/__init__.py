"""shared.artifacts.adapters — bridges from non-artifact inputs to typed artifacts.

Per build plan v5: adapters live in the artifact layer (NOT under
``shared.operators``), because they know primitive-side wire formats /
pandas shapes — which violates the operator-architecture doc's
"finance-blind structural transformation" rule.

Adapters in v1:

  - ``raw_dataframe_to_artifact_series``  — fetch + clean DataFrame → Series.
    Used by the operator-layer composition tests and (later) by
    workflow templates that fetch raw data and feed operators.

  - ``time_series_to_artifact_series``    — primitive ``TimeSeries`` →
    Series (low-level, caller supplies a fully-built ``PrimitiveStep``).
    Phase 1B Work Item 2 — the primitive→operator bridge.

  - ``tool_output_to_artifact_series``    — high-level convenience
    wrapper: primitive output dict + tool identity → Series.  Builds
    the ``PrimitiveStep`` for the caller and auto-derives the
    ``CleanSingleSeriesV1`` missingness policy from the tool config.
    The 95% bridge callsite.
"""

from shared.artifacts.adapters.from_raw_dataframe import (
    raw_dataframe_to_artifact_series,
)
from shared.artifacts.adapters.from_time_series import (
    time_series_to_artifact_series,
    tool_output_to_artifact_series,
)


__all__ = [
    "raw_dataframe_to_artifact_series",
    "time_series_to_artifact_series",
    "tool_output_to_artifact_series",
]

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
    Phase 1B Work Item 2 — the primitive→operator bridge forward path.

  - ``tool_output_to_artifact_series``    — high-level convenience
    wrapper: primitive output dict + tool identity → Series.  Builds
    the ``PrimitiveStep`` for the caller and auto-derives the
    ``CleanSingleSeriesV1`` missingness policy from the tool config.
    The 95% bridge callsite (forward).

  - ``artifact_series_to_time_series``    — reverse path: ``Series``
    → wire ``TimeSeries`` for serialization to the LLM / frontend /
    future REST.  ``NaN`` → ``None`` at every row; description is
    auto-filled with a linear lineage summary unless the caller
    overrides.  Phase 1B Work Item 3.
"""

from shared.artifacts.adapters.from_raw_dataframe import (
    raw_dataframe_to_artifact_series,
)
from shared.artifacts.adapters.from_time_series import (
    artifact_series_to_time_series,
    time_series_to_artifact_series,
    tool_output_to_artifact_series,
)


__all__ = [
    "raw_dataframe_to_artifact_series",
    "time_series_to_artifact_series",
    "tool_output_to_artifact_series",
    "artifact_series_to_time_series",
]

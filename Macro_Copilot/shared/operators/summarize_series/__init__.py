"""shared.operators.summarize_series — Series → 1-row summary Series.

Phase 2A operator (workflow-templates milestone) that closes the
"compare across regimes" gap of the
``regime_conditioned_relationship`` archetype:

  classify regimes → split sample by mask → run a per-subsample
  analysis → compare across regimes

The "compare" step in V1 reuses ``series_arithmetic.subtract``.
That operator requires its two inputs to share a DatetimeIndex.
Per-regime masked Series have DISJOINT indexes by construction
(steepening days vs flattening days), so we cannot subtract them
directly.  ``summarize_series`` bridges the gap: each masked Series
collapses to a 1-row Series at a fixed sentinel date, and the two
sentinel-aligned summaries feed into the downstream subtract.

Design lock: the sentinel date is ``pd.Timestamp("1900-01-01")``,
hard-coded, not parameterizable.  Both per-regime summaries use the
same sentinel so the downstream subtract has a non-empty
intersection; the date itself is semantically meaningless (it
identifies "this is a scalar summary," not "the summary applies on
this date").  Documenting this here so a future template author
does not mistake the sentinel for a meaningful timestamp.

Public surface:

  - ``summarize_series``       the operator itself
  - ``SummarizeSeriesParams``  typed parameter object
  - ``CONFIG_PATH``            path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.summarize_series.operator import (
    summarize_series,
    SummarizeSeriesError,
    SUMMARY_SENTINEL_DATE,
)
from shared.operators.summarize_series.schemas import (
    SummarizeSeriesParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "summarize_series",
    "SummarizeSeriesError",
    "SUMMARY_SENTINEL_DATE",
    "SummarizeSeriesParams",
    "CONFIG_PATH",
]

"""shared.operators.select_from_series_set — extract one named Series from a SeriesSet.

Phase 2A operator (workflow-templates milestone) that closes the
SeriesSet → Series gap surfaced by Codex on PR #84:

  align_series → SeriesSet, but downstream Series-consuming operators
  (threshold_events, event_windows, apply_mask, ...) need an
  individually-named Series back out.  ``SeriesSet.get_series(key)``
  is the in-memory accessor; this operator is its workflow-graph
  surface — finance-blind, lineage-preserving, registered in the
  operator registry so templates can reference it.

Public surface:

  - ``select_from_series_set``        the operator itself
  - ``SelectFromSeriesSetParams``     typed parameter object
  - ``CONFIG_PATH``                   path to bundled config.yaml
"""

from pathlib import Path

from shared.operators.select_from_series_set.operator import (
    select_from_series_set,
    SelectFromSeriesSetError,
)
from shared.operators.select_from_series_set.schemas import (
    SelectFromSeriesSetParams,
)


CONFIG_PATH: Path = Path(__file__).resolve().parent / "config.yaml"


__all__ = [
    "select_from_series_set",
    "SelectFromSeriesSetError",
    "SelectFromSeriesSetParams",
    "CONFIG_PATH",
]

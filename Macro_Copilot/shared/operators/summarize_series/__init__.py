"""shared.operators.summarize_series — Series → ScalarMetric.

Thin re-export header.  The authoritative module docstring lives in
``operator.py`` (P10: one source of truth) — read it for the full
contract, the statistic set, and the migration note.

In brief: this ``aggregation`` operator collapses a Series to ONE
scalar summary statistic (mean / median / std / sum / count / last /
first / quantile), emitted as a dateless ``ScalarMetric`` (the closed-
family scalar type) carrying the input's units.  The legacy
``1900-01-01`` (``SUMMARY_SENTINEL_DATE``) single-row-Series design is
RETIRED — it is retained only as the migration-note constant in
``operator.py`` and is no longer the output shape.

Public surface:

  - ``summarize_series``       the operator itself
  - ``SummarizeSeriesParams``  typed parameter object
  - ``SummarizeSeriesError``   the typed error (OPR13)
  - ``SUMMARY_SENTINEL_DATE``  legacy migration-note constant
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

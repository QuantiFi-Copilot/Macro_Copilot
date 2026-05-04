"""shared.artifacts — typed analytical artifacts for the operator layer.

Per ``docs/architecture/operator_architecture.md``, central operators
consume and emit *typed* artifacts (not raw pandas / NumPy objects).
Each artifact wraps payload + structural metadata (index semantics,
units, frequency, missingness policy, lineage).

This package defines the artifact wrappers, the lineage types, and the
adapter layer that bridges the existing fetch/clean substrate to the
operator layer.

Phase 1A artifact set (per build plan v5):

  - Series          — single indexed numeric series + structural metadata
  - SeriesSet       — keyed collection of aligned series
  - EventSet        — boolean event mask + per-event metadata
  - Panel           — wide table of aligned series
  - WindowedPanel   — N event windows over a target series

Plus lineage:

  - Lineage / LineageStep
  - FetchStep / CleanStep / AdapterStep / PrimitiveStep / OperatorStep

Plus structural-metadata types:

  - MissingnessPolicy  (closed family — CleanSingleSeriesV1 /
    RawNoCleaning / AlignSeriesFFillV1)
  - TimeSeriesUnits    (re-exported from shared.schemas — single taxonomy)
"""

from shared.artifacts.lineage import (
    AdapterStep,
    CleanStep,
    FetchStep,
    Lineage,
    LineageStep,
    OperatorStep,
    PrimitiveStep,
)
from shared.artifacts.missingness import (
    AlignSeriesFFillV1,
    CleanSingleSeriesV1,
    MissingnessPolicy,
    RawNoCleaning,
)
from shared.artifacts.types import (
    EventSet,
    Panel,
    Series,
    SeriesSet,
    WindowedPanel,
)
from shared.artifacts.units import TimeSeriesUnits


__all__ = [
    # Artifact wrappers
    "Series",
    "SeriesSet",
    "EventSet",
    "Panel",
    "WindowedPanel",
    # Lineage
    "Lineage",
    "LineageStep",
    "FetchStep",
    "CleanStep",
    "AdapterStep",
    "PrimitiveStep",
    "OperatorStep",
    # Structural metadata
    "MissingnessPolicy",
    "CleanSingleSeriesV1",
    "RawNoCleaning",
    "AlignSeriesFFillV1",
    "TimeSeriesUnits",
]

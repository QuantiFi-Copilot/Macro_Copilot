"""shared.artifacts.types — typed artifact wrappers.

Per the operator architecture doc: artifacts flowing through the
operator layer are NOT raw pandas / NumPy objects.  Each wrapper
carries:

  - payload (the underlying pandas Series / DataFrame, etc.)
  - structural metadata (units, frequency, missingness policy)
  - lineage (content-addressed chain of steps that produced it)

The metadata is what operators read.  The payload is what they
transform.  The wrappers are *frozen* — once built, they cannot be
mutated; transformations always emit NEW artifacts.

Phase 1A artifact set (build plan v5 / R5):

  - Series
  - SeriesSet         keyed collection of aligned series
  - EventSet          boolean event mask + per-event metadata
  - Panel             wide tabular artifact
  - WindowedPanel     N event windows over a target series

``ScalarMetric`` — a single finite scalar statistic — was admitted in
v2.0 (ADR 0016); see the ScalarMetric class below.

A note on Pydantic + pandas
---------------------------
The payloads are ``pd.Series`` / ``pd.DataFrame`` instances.  Pydantic
v2 does not natively validate these, so we use ``arbitrary_types_allowed``
and validate shape / index manually inside ``model_validator`` blocks
where needed.  The wrappers are still ``frozen`` — that prevents
field reassignment; the underlying pandas object remains mutable in
principle, but the operator-layer convention is "treat artifacts as
immutable, copy on transformation."
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.lineage import Lineage
from shared.artifacts.missingness import MissingnessPolicy
from shared.artifacts.units import TimeSeriesUnits


# ============================================================================
# SHARED ARTIFACT VALIDATORS (ART11 — one validator across every indexed /
# numeric artifact, so the "parts" are held to the same strictness as the
# operators; ADR 0016 Decision 5).  Every indexed artifact's model_validator
# calls these at construction, so downstream operators never re-check.
# ============================================================================


def _validate_datetime_index(index: Any, label: str) -> None:
    """Require a sorted, duplicate-free ``DatetimeIndex`` (ART11)."""
    if not isinstance(index, pd.DatetimeIndex):
        raise ValueError(
            f"{label} must have a DatetimeIndex; got {type(index).__name__}."
        )
    if index.has_duplicates:
        raise ValueError(
            f"{label} index has duplicate timestamps; deduplicate before "
            "building the artifact."
        )
    if not index.is_monotonic_increasing:
        raise ValueError(f"{label} index must be sorted ascending.")


def _validate_numeric_payload(values: Any, label: str) -> None:
    """Require numeric dtype and reject ``+/-Inf`` (ART11).

    ``NaN`` is permitted — it is the missingness sentinel — but ``+/-Inf``
    has no canonical JSON form and silently corrupts to ``null`` on
    persistence, so it is forbidden at construction.  An operator that
    would produce ``Inf`` must refuse with its own typed error instead.
    Accepts a ``pd.Series`` or an ``np.ndarray``.
    """
    if isinstance(values, pd.Series):
        if not pd.api.types.is_numeric_dtype(values.dtype):
            raise ValueError(
                f"{label} dtype must be numeric; got {values.dtype}."
            )
        arr = values.to_numpy()
    else:
        arr = np.asarray(values)
        if not np.issubdtype(arr.dtype, np.number):
            raise ValueError(
                f"{label} dtype must be numeric; got {arr.dtype}."
            )
    if np.isinf(arr).any():
        raise ValueError(
            f"{label} contains +/-Inf; infinities are forbidden (NaN is "
            "allowed as the missingness sentinel).  An operator that would "
            "produce Inf must refuse with its typed error instead."
        )


# ============================================================================
# SERIES
# ============================================================================


class Series(BaseModel):
    """A single indexed numeric series + structural metadata.

    The payload is a ``pd.Series`` with a ``DatetimeIndex``.  Index
    validation runs on construction so downstream operators can trust
    the contract without re-checking.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    series_key: str = Field(..., min_length=1)
    payload: pd.Series
    units: TimeSeriesUnits
    frequency: Optional[Literal["B", "D", "W", "M", "Q", "Y", "irregular"]] = None
    missingness_policy: MissingnessPolicy
    lineage: Lineage

    @model_validator(mode="after")
    def _validate_payload(self) -> "Series":
        label = f"Series '{self.series_key}'"
        _validate_datetime_index(self.payload.index, label)
        _validate_numeric_payload(self.payload, f"{label} payload")
        return self

    def __len__(self) -> int:
        return len(self.payload)


# ============================================================================
# SERIES SET (keyed; per build plan v5 / R2)
# ============================================================================


class SeriesSet(BaseModel):
    """A keyed collection of aligned ``Series``.

    Per build plan v5 / R2: downstream operators retrieve component
    series by ``series_key``, NOT by position.  ``get_series(key)``
    returns a ``Series`` whose lineage carries forward the original
    upstream lineage of THAT input series, with the alignment step
    appended — so the retrieved series knows it has been aligned.

    The ``SeriesSet`` itself is also a typed artifact with its own
    lineage (the alignment step, with input hashes = the per-series
    upstream heads).  Operators that consume the whole set (e.g.
    ``cross_sectional_rank``) read the set-level lineage; operators
    that consume one component (e.g. ``series_arithmetic``) read the
    per-component lineage via ``get_series``.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    series_by_key: Dict[str, pd.Series]
    units_by_key: Dict[str, TimeSeriesUnits]
    missingness_by_key: Dict[str, MissingnessPolicy]
    upstream_lineage_by_key: Dict[str, Lineage]
    common_index: pd.DatetimeIndex
    frequency: Optional[Literal["B", "D", "W", "M", "Q", "Y", "irregular"]] = None
    lineage: Lineage  # the SeriesSet's own lineage (head = align_series step)

    @model_validator(mode="after")
    def _validate_alignment(self) -> "SeriesSet":
        keys = set(self.series_by_key.keys())
        if not keys:
            raise ValueError("SeriesSet must contain at least one series.")
        for required in (
            self.units_by_key,
            self.missingness_by_key,
            self.upstream_lineage_by_key,
        ):
            if set(required.keys()) != keys:
                raise ValueError(
                    "SeriesSet keys must match across series_by_key, "
                    "units_by_key, missingness_by_key, "
                    "upstream_lineage_by_key."
                )
        _validate_datetime_index(self.common_index, "SeriesSet.common_index")
        for key, payload in self.series_by_key.items():
            if not payload.index.equals(self.common_index):
                raise ValueError(
                    f"SeriesSet member '{key}' index does not equal "
                    "common_index — alignment contract violated."
                )
            _validate_numeric_payload(payload, f"SeriesSet member '{key}'")
        return self

    def keys(self) -> List[str]:
        """Stable sorted list of series_keys."""
        return sorted(self.series_by_key.keys())

    def get_series(self, series_key: str) -> Series:
        """Return the named member as a ``Series`` artifact, with the
        alignment step appended to its upstream lineage.

        This is the load-bearing contract from build plan v5 / R2:
        downstream operators consume the returned ``Series`` and see
        ``[..., AlignStep]`` in its lineage chain — so the operator's
        own output lineage transitively includes alignment without
        having to walk back to the parent ``SeriesSet``.
        """
        if series_key not in self.series_by_key:
            raise KeyError(
                f"SeriesSet has no series with key '{series_key}'. "
                f"Available keys: {self.keys()}."
            )
        upstream = self.upstream_lineage_by_key[series_key]
        align_step = self.lineage.steps[-1]
        composed = upstream.append(align_step)
        return Series(
            series_key=series_key,
            payload=self.series_by_key[series_key],
            units=self.units_by_key[series_key],
            frequency=self.frequency,
            missingness_policy=self.missingness_by_key[series_key],
            lineage=composed,
        )

    def __len__(self) -> int:
        return len(self.common_index)


# ============================================================================
# EVENT SET
# ============================================================================


class EventSet(BaseModel):
    """A boolean event mask + per-event metadata.

    The mask is a ``pd.Series[bool]`` indexed on a ``DatetimeIndex``;
    True at indices where the event fires.  ``event_dates`` is a
    convenience list of the True dates in order.  ``per_event_metadata``
    holds per-event diagnostic context (triggering value, threshold
    used, etc.) — exactly which keys are populated depends on the
    operator that produced the EventSet.

    ``frequency`` is the structural-metadata tag inherited from the
    source Series, used by downstream operators like ``event_windows``
    to enforce frequency-tag agreement against a target Series
    (``require_matching_frequency=True`` is otherwise an empty
    contract — Codex P1 follow-up on PR #54).  ``None`` means the
    source Series didn't declare a frequency; downstream strict
    checks treat ``None`` as agreeing only with another ``None``.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    mask: pd.Series  # dtype=bool
    event_dates: List[pd.Timestamp]
    per_event_metadata: List[Dict[str, Any]]
    source_series_key: str  # the Series this event set was derived from
    frequency: Optional[Literal["B", "D", "W", "M", "Q", "Y", "irregular"]] = None
    lineage: Lineage

    @model_validator(mode="after")
    def _validate_mask(self) -> "EventSet":
        _validate_datetime_index(self.mask.index, "EventSet.mask")
        if self.mask.dtype != bool:
            raise ValueError(
                f"EventSet.mask dtype must be bool; got {self.mask.dtype}."
            )
        if len(self.event_dates) != len(self.per_event_metadata):
            raise ValueError(
                f"EventSet event_dates length ({len(self.event_dates)}) "
                f"!= per_event_metadata length ({len(self.per_event_metadata)})."
            )
        # Semantic invariant (ART11): event_dates is EXACTLY the mask's
        # True positions, in index order.  Because the index is sorted +
        # duplicate-free, this single check subsumes count-equality,
        # set-equality, uniqueness of event_dates, and ascending
        # co-ordering with per_event_metadata — an internally
        # inconsistent EventSet can no longer be constructed.
        expected_dates = list(self.mask.index[self.mask.to_numpy(dtype=bool)])
        if list(self.event_dates) != expected_dates:
            raise ValueError(
                "EventSet.event_dates must equal the mask's True dates in "
                f"ascending order ({len(self.event_dates)} event_dates vs "
                f"{len(expected_dates)} True mask positions); fix the "
                "mask/event_dates disagreement (duplicate, missing, extra, "
                "or out-of-order date)."
            )
        return self

    @property
    def n_events(self) -> int:
        return len(self.event_dates)


# ============================================================================
# PANEL
# ============================================================================


class Panel(BaseModel):
    """A wide tabular artifact: rows = dates, columns = named series.

    Used when an operator produces a 2D output that is not a
    ``WindowedPanel`` — e.g., a panel of regression coefficients
    indexed by date.  Phase 1A may not consume this directly; it is
    declared so the artifact set is closed.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    payload: pd.DataFrame
    units_by_column: Dict[str, TimeSeriesUnits]
    missingness_policy: MissingnessPolicy
    lineage: Lineage

    @model_validator(mode="after")
    def _validate_payload(self) -> "Panel":
        _validate_datetime_index(self.payload.index, "Panel")
        if set(self.units_by_column.keys()) != set(self.payload.columns):
            raise ValueError(
                "Panel units_by_column keys must match payload columns."
            )
        for col in self.payload.columns:
            _validate_numeric_payload(self.payload[col], f"Panel column '{col}'")
        return self


# ============================================================================
# WINDOWED PANEL
# ============================================================================


class WindowedPanel(BaseModel):
    """N event windows over a target series.

    ``payload`` is shape ``[n_events, window_length]``; rows are
    events, columns are event-relative day offsets (``offsets[0]``
    = ``-pre_window``, ``offsets[-1]`` = ``+post_window``).  Per-event
    metadata mirrors the source ``EventSet``.

    ``units`` is the unit of the windowed values (which may differ
    from the source target's units when ``units_basis`` is something
    like ``level_change`` — that transition is owned by the operator
    that produces the panel and is recorded in lineage).
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    payload: np.ndarray  # shape [n_events, window_length]
    offsets: List[int]   # event-relative day indices
    event_dates: List[pd.Timestamp]
    per_event_metadata: List[Dict[str, Any]]
    target_series_key: str
    units: TimeSeriesUnits
    lineage: Lineage

    @model_validator(mode="after")
    def _validate_shape(self) -> "WindowedPanel":
        if self.payload.ndim != 2:
            raise ValueError(
                f"WindowedPanel.payload must be 2D; got ndim={self.payload.ndim}."
            )
        n_events, window_length = self.payload.shape
        if n_events != len(self.event_dates):
            raise ValueError(
                f"WindowedPanel payload n_events ({n_events}) != "
                f"event_dates length ({len(self.event_dates)})."
            )
        if window_length != len(self.offsets):
            raise ValueError(
                f"WindowedPanel payload window_length ({window_length}) != "
                f"offsets length ({len(self.offsets)})."
            )
        if len(self.event_dates) != len(self.per_event_metadata):
            raise ValueError(
                "WindowedPanel event_dates / per_event_metadata length mismatch."
            )
        if list(self.offsets) != sorted(set(self.offsets)):
            raise ValueError(
                "WindowedPanel.offsets must be strictly increasing and "
                f"unique; got {self.offsets}."
            )
        _validate_numeric_payload(self.payload, "WindowedPanel.payload")
        return self

    @property
    def n_events(self) -> int:
        return len(self.event_dates)

    @property
    def window_length(self) -> int:
        return len(self.offsets)


# ============================================================================
# SCALAR METRIC  (admitted v2.0 — ADR 0016 / ART4–ART5)
# ============================================================================


class ScalarMetric(BaseModel):
    """A single finite scalar statistic + structural metadata.

    The honest closed-family shape for operators whose output is *one
    number*, not a series — a full-sample correlation, a covariance, a
    cointegration test statistic.  Admitted in v2.0 (ADR 0016) to
    replace the prior single-row-``Series`` + ``SUMMARY_SENTINEL_DATE``
    workaround, which stamped a fictional ``1970-01-01`` date into the
    data.

    ``value`` is a *finite* float: ``NaN`` / ``+-Inf`` are rejected at
    construction (ART11).  An undefined statistic (e.g. the correlation
    of a zero-variance series) is a typed *operator* refusal, never a
    non-finite ``ScalarMetric``.  Like every artifact it carries a
    mandatory ``lineage`` chain (ART9) and is content-addressed by
    ``lineage.head_hash`` (ART10).
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    metric_key: str = Field(..., min_length=1)
    value: float
    units: TimeSeriesUnits
    lineage: Lineage

    @model_validator(mode="after")
    def _validate_value(self) -> "ScalarMetric":
        if not np.isfinite(self.value):
            raise ValueError(
                f"ScalarMetric '{self.metric_key}' value must be finite; "
                f"got {self.value!r}.  An undefined statistic is a typed "
                "operator refusal (e.g. CorrelationError), never a "
                "non-finite ScalarMetric payload."
            )
        return self


__all__ = [
    "Series",
    "SeriesSet",
    "EventSet",
    "Panel",
    "WindowedPanel",
    "ScalarMetric",
]

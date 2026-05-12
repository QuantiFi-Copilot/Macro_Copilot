"""state.artifact_store — content-addressed persistence for typed artifacts.

Phase 0 PR 7.  Wraps the ``copilot_state.artifact_metadata`` table
and the configured ``ObjectStorageBackend`` to provide three
operations:

  - ``put_artifact(artifact, *, conn, object_storage) -> hash``
      Persist a typed artifact.  Idempotent: a hash that already
      exists in the metadata table short-circuits (no object-storage
      write, no metadata insert).  Returns the artifact's hash
      (which equals ``artifact.lineage.head_hash``).

  - ``get_artifact(hash, *, conn, object_storage) -> Artifact``
      Rehydrate the typed artifact.  Fast path for inline-stored
      artifacts (no object-storage fetch); blob-stored artifacts
      pull the payload bytes from object storage.

  - ``get_artifact_summary(hash, *, conn) -> ArtifactSummary``
      Pure metadata read.  Does NOT touch object storage.  Used by
      the Workspace renderer to populate per-node cards without
      paying for the payload deserialization cost.

Determinism + content-addressing
--------------------------------
The artifact's hash IS its ``lineage.head_hash`` — the hash recipe
from PR 2 (``shared.artifacts.lineage._compute_step_hash``).  We do
NOT recompute or supplement it here.  This means:

  - Two artifacts with identical lineage chains share a hash.  Put
    one, the next put-of-the-same is a no-op.
  - The hash is independent of serialization format.  A future PR
    that migrates from JSON-inline to a different inline encoding
    does NOT change any hashes; only the row's ``inline_payload``
    representation changes.

Inline-vs-blob decision
-----------------------
Default thresholds:

  - ``INLINE_PAYLOAD_ROW_LIMIT = 100``  — > this, blob.
  - ``INLINE_PAYLOAD_SIZE_LIMIT_BYTES = 8192``  — > this, blob.

Both overridable via env var (``ARTIFACT_INLINE_ROW_LIMIT`` /
``ARTIFACT_INLINE_SIZE_LIMIT_BYTES``).  The "blob if EITHER cap is
exceeded" disjunction biases toward smaller Postgres rows.

Connection management
---------------------
``put_artifact`` / ``get_artifact`` / ``get_artifact_summary`` all
take ``conn`` as a keyword-only argument.  This is the
``Connection``-injection pattern established in PR 3:

  with engine.begin() as conn:
      h = put_artifact(art, conn=conn, object_storage=os)
      ...

The caller manages transaction boundaries.  In particular, a caller
that wants the metadata insert atomic with some other row (e.g. a
working_set entry that references the new artifact) can wrap both
in a single ``engine.begin()``.

Orphan blobs
------------
If the metadata-insert phase of ``put_artifact`` rolls back after
the object-storage write completed, the blob is orphaned in object
storage.  These orphans are benign:

  - Content-addressed, so a future re-put of the same artifact
    produces the same URI and either no-ops (LocalFS overwrite) or
    re-uploads (idempotent at the object-storage Protocol level).
  - The GC sweep in ``state.gc`` includes an orphan-finder pass
    that lists blobs without metadata rows (deferred enablement;
    see ``state/gc.py``).
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.engine import Connection

from shared.artifacts.lineage import Lineage
from shared.artifacts.missingness import MissingnessPolicy
from shared.artifacts.types import (
    EventSet,
    Panel,
    Series,
    SeriesSet,
    WindowedPanel,
)
from shared.artifacts.units import TimeSeriesUnits

# MissingnessPolicy is a discriminated union (CleanSingleSeriesV1 |
# RawNoCleaning | AlignSeriesFFillV1).  TypeAdapter handles the
# discriminator-based dispatch on deserialization.
_MISSINGNESS_ADAPTER: TypeAdapter = TypeAdapter(MissingnessPolicy)


def _dump_missingness(policy) -> Dict[str, Any]:
    return policy.model_dump(mode="json")


def _load_missingness(d: Dict[str, Any]):
    return _MISSINGNESS_ADAPTER.validate_python(d)
from state.object_storage import HASH_LEN, ObjectStorageBackend, _validate_hash
from state.schemas import ArtifactSummary, StoredArtifact

logger = logging.getLogger("state.artifact_store")


# ============================================================================
# CONSTANTS
# ============================================================================

# Closed-family of supported artifact types.  Mirrors
# ``shared.artifacts.types`` and the ``ArtifactTypeLiteral`` in
# ``state.schemas``.  Maintained as a tuple of (name, class) so the
# discriminator-based serializer can iterate it.
_ARTIFACT_CLASSES: Tuple[Tuple[str, type], ...] = (
    ("Series", Series),
    ("SeriesSet", SeriesSet),
    ("EventSet", EventSet),
    ("Panel", Panel),
    ("WindowedPanel", WindowedPanel),
)
_NAME_TO_CLASS: Dict[str, type] = {name: cls for name, cls in _ARTIFACT_CLASSES}
_CLASS_TO_NAME: Dict[type, str] = {cls: name for name, cls in _ARTIFACT_CLASSES}

# Default schema namespace.  Copies the constant from the migration
# rather than importing the migration module (Alembic revisions are
# not normal Python packages).
_COPILOT_STATE_SCHEMA = "copilot_state"

# Inline-vs-blob thresholds.  Overridable via env vars.
DEFAULT_INLINE_ROW_LIMIT = 100
DEFAULT_INLINE_SIZE_LIMIT_BYTES = 8192

# Sparkline preview size used by ``get_artifact_summary``.  Bounded
# so the summary stays cheap even for very large artifacts.
_PREVIEW_POINTS = 16


# A typed-artifact union — what put_artifact accepts and what
# get_artifact returns.
Artifact = Union[Series, SeriesSet, EventSet, Panel, WindowedPanel]


# ============================================================================
# Public API — put / get / summary
# ============================================================================


def put_artifact(
    artifact: Artifact,
    *,
    conn: Connection,
    object_storage: ObjectStorageBackend,
    inline_row_limit: Optional[int] = None,
    inline_size_limit_bytes: Optional[int] = None,
) -> str:
    """Persist a typed artifact.  Returns the artifact's hash.

    Idempotent: if an ``artifact_metadata`` row with the same hash
    already exists, this is a no-op (no object-storage write, no
    metadata insert).

    Phase 0 PR 7.  See module docstring for the inline-vs-blob
    decision tree, orphan-blob policy, and connection conventions.
    """
    artifact_type = _artifact_type_name(artifact)
    artifact_hash = artifact.lineage.head_hash
    _validate_hash(artifact_hash)

    # Idempotency gate: cheap Postgres-only check before we spend
    # any cycles on serialization / object-storage I/O.
    if _hash_exists(conn, artifact_hash):
        logger.debug(
            "put_artifact: hash %s... already present, no-op", artifact_hash[:12]
        )
        return artifact_hash

    # Resolve thresholds.  Env-var overrides win over defaults; explicit
    # arg overrides win over env vars.
    row_limit = (
        inline_row_limit
        if inline_row_limit is not None
        else int(os.getenv("ARTIFACT_INLINE_ROW_LIMIT", DEFAULT_INLINE_ROW_LIMIT))
    )
    size_limit = (
        inline_size_limit_bytes
        if inline_size_limit_bytes is not None
        else int(
            os.getenv(
                "ARTIFACT_INLINE_SIZE_LIMIT_BYTES",
                DEFAULT_INLINE_SIZE_LIMIT_BYTES,
            )
        )
    )

    # Serialize the artifact into the on-disk shape.  This is the same
    # JSON-safe dict regardless of inline vs blob; the only difference
    # is where the bytes land.
    stored = _artifact_to_stored(artifact)
    serialized_bytes = _stored_to_bytes(stored)
    row_count = _artifact_row_count(artifact)

    inline = row_count <= row_limit and len(serialized_bytes) <= size_limit

    if inline:
        payload_uri: Optional[str] = None
        inline_payload: Optional[Dict[str, Any]] = stored.model_dump(mode="json")
    else:
        payload_uri = object_storage.put_bytes(artifact_hash, serialized_bytes)
        inline_payload = None

    # Extract the column-level metadata that artifact_metadata stores
    # separately from the full payload.  Useful for queries that
    # filter by type / units / row_count without paying for the
    # JSONB scan.
    units = _artifact_units(artifact)
    frequency = _artifact_frequency(artifact)
    lineage_json: Dict[str, Any] = artifact.lineage.model_dump(mode="json")

    _insert_metadata_row(
        conn,
        hash=artifact_hash,
        artifact_type=artifact_type,
        units=units,
        frequency=frequency,
        row_count=row_count,
        byte_size=len(serialized_bytes),
        payload_uri=payload_uri,
        inline_payload=inline_payload,
        lineage=lineage_json,
    )
    logger.debug(
        "put_artifact: stored %s (type=%s, %d rows, %d bytes, inline=%s)",
        artifact_hash[:12], artifact_type, row_count, len(serialized_bytes), inline,
    )
    return artifact_hash


def get_artifact(
    hash: str,
    *,
    conn: Connection,
    object_storage: ObjectStorageBackend,
) -> Artifact:
    """Rehydrate a typed artifact from its hash.

    Phase 0 PR 7.  Fast path for inline-stored artifacts (no
    object-storage fetch); blob-stored artifacts pull payload bytes
    from object storage.

    Raises:
        KeyError: no artifact_metadata row for ``hash``.
        FileNotFoundError: the metadata row points at a payload URI
            that the object storage backend cannot find (orphaned
            metadata).
    """
    _validate_hash(hash)
    row = _fetch_full_row(conn, hash)
    if row is None:
        raise KeyError(f"No artifact with hash {hash!r}")

    artifact_type = row["artifact_type"]
    if artifact_type not in _NAME_TO_CLASS:
        raise ValueError(
            f"Unknown artifact_type {artifact_type!r} for hash {hash}.  "
            "Closed-family set is "
            f"{sorted(_NAME_TO_CLASS.keys())}."
        )

    if row["inline_payload"] is not None:
        stored_dict = row["inline_payload"]
    else:
        if row["payload_uri"] is None:
            raise RuntimeError(
                f"Artifact {hash} has neither inline_payload nor "
                "payload_uri set.  This violates the CHECK constraint "
                "ck_artifact_metadata_payload_exactly_one — data "
                "corruption?"
            )
        payload_bytes = object_storage.get_bytes(row["payload_uri"])
        stored_dict = _bytes_to_stored_dict(payload_bytes)

    stored = StoredArtifact.model_validate(stored_dict)
    return _stored_to_artifact(stored)


def get_artifact_summary(
    hash: str,
    *,
    conn: Connection,
) -> ArtifactSummary:
    """Return a lightweight ``ArtifactSummary`` without touching
    object storage or fully deserializing the payload.

    Used by the Workspace renderer to populate per-node cards
    quickly.  Reads only ``copilot_state.artifact_metadata``;
    for inline-stored artifacts it pulls a small sparkline preview
    from ``inline_payload``, for blob-stored artifacts the preview
    is empty (the caller can call ``get_artifact(...)`` for the full
    payload if a richer preview is needed).
    """
    _validate_hash(hash)
    row = _fetch_summary_row(conn, hash)
    if row is None:
        raise KeyError(f"No artifact with hash {hash!r}")

    inline = row["inline_payload"] is not None
    preview_index: List[str] = []
    preview_values: List[Optional[float]] = []

    if inline:
        # Sparkline-extract from the inline JSON without going
        # through full Pydantic validation.  Best-effort: a malformed
        # inline_payload falls back to empty preview rather than
        # raising — the summary's purpose is fast rendering, not
        # data-integrity enforcement (that's get_artifact's job).
        try:
            preview_index, preview_values = _extract_preview(
                row["inline_payload"], _PREVIEW_POINTS
            )
        except Exception:
            logger.warning(
                "preview extraction failed for hash %s; returning empty",
                hash[:12],
            )

    return ArtifactSummary(
        hash=row["hash"],
        artifact_type=row["artifact_type"],
        units=row["units"],
        frequency=row["frequency"],
        row_count=row["row_count"],
        byte_size=row["byte_size"],
        payload_uri=row["payload_uri"],
        inline=inline,
        created_at=row["created_at"],
        preview_index=preview_index,
        preview_values=preview_values,
    )


# ============================================================================
# Postgres helpers
# ============================================================================


def _hash_exists(conn: Connection, hash: str) -> bool:
    """Idempotency gate.  Cheap PK lookup."""
    row = conn.execute(
        text(
            f"SELECT 1 FROM {_COPILOT_STATE_SCHEMA}.artifact_metadata "
            "WHERE hash = :hash LIMIT 1"
        ),
        {"hash": hash},
    ).fetchone()
    return row is not None


def _insert_metadata_row(
    conn: Connection,
    *,
    hash: str,
    artifact_type: str,
    units: Optional[str],
    frequency: Optional[str],
    row_count: Optional[int],
    byte_size: int,
    payload_uri: Optional[str],
    inline_payload: Optional[Dict[str, Any]],
    lineage: Dict[str, Any],
) -> None:
    """Insert one ``artifact_metadata`` row.  Caller has already
    confirmed the hash does not exist."""
    stmt = text(
        f"""
        INSERT INTO {_COPILOT_STATE_SCHEMA}.artifact_metadata (
            hash, artifact_type, units, frequency, row_count,
            byte_size, payload_uri, inline_payload, lineage,
            methodology_version_ids
        )
        VALUES (
            :hash, :artifact_type, :units, :frequency, :row_count,
            :byte_size, :payload_uri,
            CAST(:inline_payload AS JSONB),
            CAST(:lineage AS JSONB),
            NULL
        )
        """
    )
    conn.execute(
        stmt,
        {
            "hash": hash,
            "artifact_type": artifact_type,
            "units": units,
            "frequency": frequency,
            "row_count": row_count,
            "byte_size": byte_size,
            "payload_uri": payload_uri,
            "inline_payload": (
                json.dumps(inline_payload) if inline_payload is not None else None
            ),
            "lineage": json.dumps(lineage),
        },
    )


def _fetch_full_row(conn: Connection, hash: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        text(
            f"""
            SELECT
                hash, artifact_type, units, frequency, row_count,
                byte_size, payload_uri, inline_payload, lineage,
                methodology_version_ids, created_at
            FROM {_COPILOT_STATE_SCHEMA}.artifact_metadata
            WHERE hash = :hash
            """
        ),
        {"hash": hash},
    ).mappings().first()
    return dict(row) if row is not None else None


def _fetch_summary_row(conn: Connection, hash: str) -> Optional[Dict[str, Any]]:
    """Same as ``_fetch_full_row`` but explicit about pulling
    ``inline_payload`` only for the preview path; blob-stored
    artifacts skip the JSONB column from the SELECT.

    For now we pull inline_payload unconditionally because Postgres
    JSONB read cost is dominated by row + TOAST overhead, not by
    selecting the column.  Splitting would be premature
    optimization."""
    return _fetch_full_row(conn, hash)


# ============================================================================
# Artifact → stored dict (serialization)
# ============================================================================


def _artifact_type_name(artifact: Artifact) -> str:
    cls = type(artifact)
    if cls not in _CLASS_TO_NAME:
        raise TypeError(
            f"Unsupported artifact type {cls.__name__!r}.  Supported: "
            f"{sorted(_NAME_TO_CLASS.keys())}."
        )
    return _CLASS_TO_NAME[cls]


def _artifact_to_stored(artifact: Artifact) -> StoredArtifact:
    type_name = _artifact_type_name(artifact)
    if isinstance(artifact, Series):
        meta, payload = _series_to_stored(artifact)
    elif isinstance(artifact, SeriesSet):
        meta, payload = _series_set_to_stored(artifact)
    elif isinstance(artifact, EventSet):
        meta, payload = _event_set_to_stored(artifact)
    elif isinstance(artifact, Panel):
        meta, payload = _panel_to_stored(artifact)
    elif isinstance(artifact, WindowedPanel):
        meta, payload = _windowed_panel_to_stored(artifact)
    else:
        raise TypeError(f"Unsupported artifact type {type(artifact).__name__}")
    return StoredArtifact(
        artifact_type=type_name,
        metadata=meta,
        payload=payload,
    )


def _stored_to_artifact(stored: StoredArtifact) -> Artifact:
    type_name = stored.artifact_type
    cls = _NAME_TO_CLASS[type_name]
    if cls is Series:
        return _series_from_stored(stored)
    if cls is SeriesSet:
        return _series_set_from_stored(stored)
    if cls is EventSet:
        return _event_set_from_stored(stored)
    if cls is Panel:
        return _panel_from_stored(stored)
    if cls is WindowedPanel:
        return _windowed_panel_from_stored(stored)
    raise TypeError(f"Unsupported artifact type {type_name!r}")


# ----- Series ----------------------------------------------------------------


def _series_to_stored(art: Series) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    meta = {
        "series_key": art.series_key,
        # TimeSeriesUnits is a string Enum; serialize its .value.
        "units": art.units.value,
        "frequency": art.frequency,
        "missingness_policy": _dump_missingness(art.missingness_policy),
        "lineage": art.lineage.model_dump(mode="json"),
    }
    payload = _pd_series_to_jsonable(art.payload)
    return meta, payload


def _series_from_stored(stored: StoredArtifact) -> Series:
    payload = _pd_series_from_jsonable(stored.payload)
    return Series(
        series_key=stored.metadata["series_key"],
        payload=payload,
        units=TimeSeriesUnits(stored.metadata["units"]),
        frequency=stored.metadata.get("frequency"),
        missingness_policy=_load_missingness(stored.metadata["missingness_policy"]),
        lineage=Lineage.model_validate(stored.metadata["lineage"]),
    )


# ----- SeriesSet -------------------------------------------------------------


def _series_set_to_stored(art: SeriesSet) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    meta = {
        # TimeSeriesUnits is a string Enum; serialize each value.
        "units_by_key": {k: v.value for k, v in art.units_by_key.items()},
        "missingness_by_key": {
            k: _dump_missingness(v) for k, v in art.missingness_by_key.items()
        },
        "upstream_lineage_by_key": {
            k: v.model_dump(mode="json") for k, v in art.upstream_lineage_by_key.items()
        },
        "frequency": art.frequency,
        "lineage": art.lineage.model_dump(mode="json"),
    }
    payload = {
        "common_index": _datetime_index_to_iso(art.common_index),
        "series_by_key": {
            k: list(v.values.tolist()) for k, v in art.series_by_key.items()
        },
    }
    return meta, payload


def _series_set_from_stored(stored: StoredArtifact) -> SeriesSet:
    common_index = _datetime_index_from_iso(stored.payload["common_index"])
    series_by_key = {
        k: pd.Series(v, index=common_index, dtype=float)
        for k, v in stored.payload["series_by_key"].items()
    }
    return SeriesSet(
        series_by_key=series_by_key,
        units_by_key={
            k: TimeSeriesUnits(v)
            for k, v in stored.metadata["units_by_key"].items()
        },
        missingness_by_key={
            k: _load_missingness(v)
            for k, v in stored.metadata["missingness_by_key"].items()
        },
        upstream_lineage_by_key={
            k: Lineage.model_validate(v)
            for k, v in stored.metadata["upstream_lineage_by_key"].items()
        },
        common_index=common_index,
        frequency=stored.metadata.get("frequency"),
        lineage=Lineage.model_validate(stored.metadata["lineage"]),
    )


# ----- EventSet --------------------------------------------------------------


def _event_set_to_stored(art: EventSet) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    meta = {
        "source_series_key": art.source_series_key,
        "frequency": art.frequency,
        "lineage": art.lineage.model_dump(mode="json"),
    }
    payload = {
        "mask_index": _datetime_index_to_iso(art.mask.index),
        "mask_values": [bool(v) for v in art.mask.values],
        "event_dates": [_iso(ts) for ts in art.event_dates],
        "per_event_metadata": art.per_event_metadata,
    }
    return meta, payload


def _event_set_from_stored(stored: StoredArtifact) -> EventSet:
    mask_index = _datetime_index_from_iso(stored.payload["mask_index"])
    mask = pd.Series(
        stored.payload["mask_values"], index=mask_index, dtype=bool
    )
    event_dates = [pd.Timestamp(s) for s in stored.payload["event_dates"]]
    return EventSet(
        mask=mask,
        event_dates=event_dates,
        per_event_metadata=stored.payload["per_event_metadata"],
        source_series_key=stored.metadata["source_series_key"],
        frequency=stored.metadata.get("frequency"),
        lineage=Lineage.model_validate(stored.metadata["lineage"]),
    )


# ----- Panel -----------------------------------------------------------------


def _panel_to_stored(art: Panel) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    meta = {
        "units_by_column": {k: v.value for k, v in art.units_by_column.items()},
        "missingness_policy": _dump_missingness(art.missingness_policy),
        "lineage": art.lineage.model_dump(mode="json"),
    }
    payload = {
        "index": _datetime_index_to_iso(art.payload.index),
        "columns": list(art.payload.columns),
        "data": [list(row) for row in art.payload.values.tolist()],
    }
    return meta, payload


def _panel_from_stored(stored: StoredArtifact) -> Panel:
    idx = _datetime_index_from_iso(stored.payload["index"])
    columns = stored.payload["columns"]
    data = stored.payload["data"]
    df = pd.DataFrame(data, index=idx, columns=columns)
    return Panel(
        payload=df,
        units_by_column={
            k: TimeSeriesUnits(v)
            for k, v in stored.metadata["units_by_column"].items()
        },
        missingness_policy=_load_missingness(stored.metadata["missingness_policy"]),
        lineage=Lineage.model_validate(stored.metadata["lineage"]),
    )


# ----- WindowedPanel ---------------------------------------------------------


def _windowed_panel_to_stored(
    art: WindowedPanel,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    meta = {
        "offsets": art.offsets,
        "target_series_key": art.target_series_key,
        "units": art.units.value,
        "lineage": art.lineage.model_dump(mode="json"),
    }
    payload = {
        "data": [list(row) for row in art.payload.tolist()],
        "event_dates": [_iso(ts) for ts in art.event_dates],
        "per_event_metadata": art.per_event_metadata,
    }
    return meta, payload


def _windowed_panel_from_stored(stored: StoredArtifact) -> WindowedPanel:
    return WindowedPanel(
        payload=np.asarray(stored.payload["data"], dtype=float),
        offsets=stored.metadata["offsets"],
        event_dates=[pd.Timestamp(s) for s in stored.payload["event_dates"]],
        per_event_metadata=stored.payload["per_event_metadata"],
        target_series_key=stored.metadata["target_series_key"],
        units=TimeSeriesUnits(stored.metadata["units"]),
        lineage=Lineage.model_validate(stored.metadata["lineage"]),
    )


# ============================================================================
# pandas / numpy helpers
# ============================================================================


def _iso(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).isoformat()


def _datetime_index_to_iso(idx: pd.DatetimeIndex) -> List[str]:
    return [_iso(ts) for ts in idx]


def _datetime_index_from_iso(values: List[str]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(s) for s in values])


def _pd_series_to_jsonable(series: pd.Series) -> Dict[str, Any]:
    # Convert NaN to None for JSON-safety.  pandas NaN serializes as
    # ``NaN`` which is not valid JSON under ``allow_nan=False``.
    values: List[Optional[float]] = []
    for v in series.values:
        if pd.isna(v):
            values.append(None)
        else:
            values.append(float(v))
    return {
        "index": _datetime_index_to_iso(series.index),
        "values": values,
    }


def _pd_series_from_jsonable(payload: Dict[str, Any]) -> pd.Series:
    idx = _datetime_index_from_iso(payload["index"])
    raw_values = payload["values"]
    # Convert back: None → NaN.  We use float dtype throughout for
    # numerical artifacts (the Series._validate_payload check enforces
    # this on construction).
    coerced = [float("nan") if v is None else float(v) for v in raw_values]
    return pd.Series(coerced, index=idx, dtype=float)


def _artifact_row_count(artifact: Artifact) -> int:
    """How many rows the artifact contains.  Used for the
    inline-vs-blob decision and for the metadata column."""
    if isinstance(artifact, Series):
        return len(artifact.payload)
    if isinstance(artifact, SeriesSet):
        return len(artifact.common_index)
    if isinstance(artifact, EventSet):
        return len(artifact.mask)
    if isinstance(artifact, Panel):
        return len(artifact.payload)
    if isinstance(artifact, WindowedPanel):
        return artifact.payload.shape[0]
    raise TypeError(f"Unsupported artifact type {type(artifact).__name__}")


def _artifact_units(artifact: Artifact) -> Optional[str]:
    """Best-effort units string for the queryable metadata column.

    Single-unit artifacts (Series, WindowedPanel) emit their unit's
    string value (e.g. ``"bps"`` / ``"percent"``).  Multi-unit
    artifacts (SeriesSet, Panel with per-column units, EventSet
    which has no units) emit NULL — the per-column / per-key units
    live inside the JSONB payload, queryable but not promoted to a
    column.
    """
    if isinstance(artifact, Series):
        return artifact.units.value
    if isinstance(artifact, WindowedPanel):
        return artifact.units.value
    return None


def _artifact_frequency(artifact: Artifact) -> Optional[str]:
    if isinstance(artifact, Series):
        return artifact.frequency
    if isinstance(artifact, SeriesSet):
        return artifact.frequency
    if isinstance(artifact, EventSet):
        return artifact.frequency
    return None


# ============================================================================
# StoredArtifact ↔ bytes (for blob storage)
# ============================================================================


def _stored_to_bytes(stored: StoredArtifact) -> bytes:
    """Canonical bytes encoding for blob storage.

    JSON via Pydantic, UTF-8 encoded, sorted keys.  The same shape
    that ``inline_payload`` stores in Postgres; for blob mode it's
    just stored externally instead of in-row.  No special framing or
    headers — the bytes are pure JSON, suitable for ``jq`` inspection
    if an operator needs to debug a corrupted artifact.

    We deliberately do NOT use Parquet here for the blob bytes:
    parquet's reader / writer state can drift across pyarrow versions
    in subtle ways (column statistics, page metadata), and the
    inline / blob shapes diverging would complicate get_artifact's
    fast path.  Same JSON shape everywhere.
    """
    return stored.model_dump_json(by_alias=False).encode("utf-8")


def _bytes_to_stored_dict(content: bytes) -> Dict[str, Any]:
    """Reverse of ``_stored_to_bytes``.  Returns the parsed dict;
    Pydantic validation happens at the caller (``get_artifact``)."""
    return json.loads(content.decode("utf-8"))


# ============================================================================
# Sparkline preview extraction (summary endpoint)
# ============================================================================


def _extract_preview(
    inline_payload: Any, max_points: int
) -> Tuple[List[str], List[Optional[float]]]:
    """Best-effort sparkline preview from an inline JSONB payload.

    Inspects the discriminator ``artifact_type`` (carried inside the
    StoredArtifact dict) and pulls a bounded slice of the payload.
    Empty preview for artifact types without a natural single-series
    representation (SeriesSet, Panel, WindowedPanel) — callers can
    fetch the full artifact for richer previews.
    """
    if not isinstance(inline_payload, dict):
        return [], []
    artifact_type = inline_payload.get("artifact_type")
    payload = inline_payload.get("payload", {})

    if artifact_type == "Series":
        idx = payload.get("index", [])
        vals = payload.get("values", [])
        return idx[:max_points], vals[:max_points]

    if artifact_type == "EventSet":
        # Render the mask as 0 / 1 sparkline — useful for showing
        # event density at a glance.
        idx = payload.get("mask_index", [])
        bools = payload.get("mask_values", [])
        return idx[:max_points], [1.0 if b else 0.0 for b in bools[:max_points]]

    # SeriesSet, Panel, WindowedPanel: no canonical "single series" for
    # the preview.  Callers can request the full artifact and pick a
    # column to render.
    return [], []

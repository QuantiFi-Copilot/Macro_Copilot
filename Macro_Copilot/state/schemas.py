"""state.schemas — Pydantic models for the artifact store surface.

These are the small wire / API shapes that wrap the typed artifacts
from ``shared.artifacts.types`` for storage / retrieval purposes.

The typed ``Artifact`` family (``Series``, ``SeriesSet``, ``EventSet``,
``Panel``, ``WindowedPanel``) is the rich, payload-bearing object that
operators and primitives produce.  These ``state.schemas`` models are
the lightweight wrappers around stored representations — what
``artifact_metadata.inline_payload`` carries on the wire and what
the Workspace renderer reads back without paying for the full
payload fetch.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# CLOSED-FAMILY ARTIFACT TYPE TAGS
# ============================================================================
# Mirror of the typed-artifact closed family in
# ``shared.artifacts.types``.  Stored as a plain string in
# ``copilot_state.artifact_metadata.artifact_type``.  Enforced at the
# Pydantic layer (the storage code refuses to put / get a type not
# in this list) rather than via a Postgres CHECK constraint, so the
# closed family stays in one place (Python) instead of two (Python +
# SQL).

ArtifactTypeLiteral = Literal[
    "Series",
    "SeriesSet",
    "EventSet",
    "Panel",
    "WindowedPanel",
    # v2.0 (ADR 0016): ScalarMetric — the single-number closed-family
    # shape for full-sample statistics (correlation, covariance, ...).
    "ScalarMetric",
    # Phase 1 PR 12 — backtest archetype substrate.  TradeSet joins
    # the closed family; PositionPath is deferred to V2 (the brief's
    # "if needed" — V1's evaluate_trades derives positions internally
    # from entry/exit + leg weights without surfacing them as an
    # artifact).
    "TradeSet",
]


# ============================================================================
# ArtifactSummary — lightweight metadata-only view
# ============================================================================


class ArtifactSummary(BaseModel):
    """A small, payload-free view of a stored artifact.

    Returned by ``state.artifact_store.get_artifact_summary``.  Used
    by the Workspace renderer to populate per-node cards quickly —
    one Postgres read, no object-storage fetch, no payload
    deserialization.  Full payload retrieval is via
    ``get_artifact(hash, ...)`` and is opt-in per card-click.

    ``preview_values`` and ``preview_index`` are deliberately bounded
    (default 16 points) so a sparkline can render without pulling
    the whole payload.  Tests should verify the bound is honored
    even for arbitrarily large source artifacts.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    hash: str = Field(..., min_length=64, max_length=64)
    artifact_type: ArtifactTypeLiteral
    units: Optional[str] = None
    frequency: Optional[str] = None
    row_count: Optional[int] = None
    byte_size: int = 0
    payload_uri: Optional[str] = None
    inline: bool
    created_at: datetime
    # A small sparkline-ready slice for fast rendering.  Empty for
    # artifact types whose payload is not naturally a single series
    # (SeriesSet, Panel, WindowedPanel may emit a representative
    # column / row instead).
    preview_index: List[str] = Field(default_factory=list)
    preview_values: List[Optional[float]] = Field(default_factory=list)


# ============================================================================
# ObjectStorageConfig — backend selection at app startup
# ============================================================================


class ObjectStorageConfig(BaseModel):
    """Configuration for the object-storage backend.

    Read from environment variables at app startup in
    ``api.dependencies.init_object_storage`` (Phase 0 PR 7).  The
    backend itself is one of ``LocalFSBackend`` or ``GCSBackend``
    (see ``state.object_storage``); this model just captures the
    selection + per-backend params in a typed shape so the lifespan
    code doesn't sprinkle ``os.getenv`` calls.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    backend: Literal["localfs", "gcs"] = "localfs"

    # LocalFSBackend: the root directory under which artifact blobs
    # live.  Each artifact is at
    # ``{root}/{hash[:2]}/{hash[2:]}.bin``.
    local_root: Optional[str] = None

    # GCSBackend: bucket name + key prefix.  Each artifact is at
    # ``gs://{bucket}/{prefix}/{hash[:2]}/{hash[2:]}.bin``.
    gcs_bucket: Optional[str] = None
    gcs_prefix: str = "artifacts"


# ============================================================================
# StoredArtifact — the on-disk shape (a single artifact's full storage form)
# ============================================================================


class StoredArtifact(BaseModel):
    """Internal type: an artifact in its serialized-for-storage form.

    NOT a public API — exposed here so the artifact-store helpers and
    tests can talk about the storage shape with type safety.  The
    canonical content is:

      - ``artifact_type``       discriminator
      - ``metadata``            JSON-safe dict of all non-payload
                                Pydantic fields (units, frequency,
                                series_key, missingness_policy,
                                lineage, event_dates, ...)
      - ``payload``             JSON-safe representation of the
                                pd / numpy payload (see
                                ``state.artifact_store._payload_*``
                                helpers)

    For inline storage, this whole dict is the contents of
    ``artifact_metadata.inline_payload`` JSONB.

    For blob storage, this dict is JSON-serialized + UTF-8 encoded
    and written as bytes to object storage; the Postgres row stores
    only the URI and the same column-level metadata.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: ArtifactTypeLiteral
    metadata: Dict[str, Any]
    payload: Dict[str, Any]


__all__ = [
    "ArtifactTypeLiteral",
    "ArtifactSummary",
    "ObjectStorageConfig",
    "StoredArtifact",
]

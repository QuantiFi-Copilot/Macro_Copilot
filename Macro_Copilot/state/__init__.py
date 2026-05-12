"""state — persistence layer for typed artifacts + workspaces.

Phase 0 PR 7 introduces:

  - the artifact store (this PR's primary surface):
      ``put_artifact`` / ``get_artifact`` / ``get_artifact_summary``
  - the object-storage backend abstraction with two implementations:
      ``LocalFSBackend`` (default; dev / CI) and ``GCSBackend``
  - garbage-collection sweep helpers (implemented, NOT yet wired
    to any scheduler — Phase 4 enables): ``find_unreferenced_artifacts``,
    ``purge_artifact``

Future phases (week 5+) will add:
  - ``working_set`` writes (week 5)
  - workspace persistence helpers (week 7)

This module re-exports the stable public API.  Internal helpers
(serialization shims for the typed artifact family, Postgres row
builders, etc.) stay in their submodules.
"""

from __future__ import annotations

from state.artifact_store import (
    DEFAULT_INLINE_ROW_LIMIT,
    DEFAULT_INLINE_SIZE_LIMIT_BYTES,
    Artifact,
    get_artifact,
    get_artifact_summary,
    put_artifact,
)
from state.gc import find_unreferenced_artifacts, purge_artifact
from state.object_storage import (
    HASH_LEN,
    GCSBackend,
    LocalFSBackend,
    ObjectStorageBackend,
    build_backend,
)
from state.schemas import (
    ArtifactSummary,
    ArtifactTypeLiteral,
    ObjectStorageConfig,
    StoredArtifact,
)

__all__ = [
    # artifact store
    "put_artifact",
    "get_artifact",
    "get_artifact_summary",
    "Artifact",
    "DEFAULT_INLINE_ROW_LIMIT",
    "DEFAULT_INLINE_SIZE_LIMIT_BYTES",
    # object storage
    "ObjectStorageBackend",
    "LocalFSBackend",
    "GCSBackend",
    "build_backend",
    "HASH_LEN",
    # gc (not yet wired)
    "find_unreferenced_artifacts",
    "purge_artifact",
    # schemas
    "ArtifactSummary",
    "ArtifactTypeLiteral",
    "ObjectStorageConfig",
    "StoredArtifact",
]

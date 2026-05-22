"""api/routes/artifacts.py — artifact-keyed REST surface.

Phase 0 PR 10.

Endpoint: ``GET /api/v1/artifacts/{artifact_hash}/replay``

This is the relocated PR 9 endpoint that used to live at
``GET /api/v1/workspace/{hash}``.  Phase 0 PR 10 introduced
workspace persistence under ``/api/v1/workspace/{slug}``; to keep
the path map honest (and the URL self-describing), the
artifact-keyed view moved here.  The response shape is unchanged
from PR 9.

What it returns
---------------
Given an artifact hash, surface the methodology + application-
version provenance captured by PR 9's ``put_artifact`` wiring:

  - ``mode=original`` (default): reconstruct each pinned YAML from
    the registry, validate it round-trips through ``ToolConfig``,
    return the resulting identity hash for each.
  - ``mode=current``: compare each pinned YAML against the same
    on-disk path NOW and report drift as ``methodology_diffs``.

Underlying logic lives in ``api/routes/_replay_helpers.py`` —
imported here AND from the workspace replay route so both surfaces
share one canonical implementation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict

from api.dependencies import (
    get_artifact_cache,
    get_engine,
    get_object_storage,
)
from api.routes._replay_helpers import (
    MethodologyDiff,
    ReconstructedMethodology,
    is_hex,
    replay_summary_for_artifact,
)
from state.artifact_store import get_artifact_payload_dict
from state.schemas import ArtifactTypeLiteral

logger = logging.getLogger("api.routes.artifacts")


router = APIRouter()


# ============================================================================
# Response model
# ============================================================================


class ArtifactReplayResponse(BaseModel):
    """Response shape for ``GET /artifacts/{hash}/replay``.

    Field-for-field identical to PR 9's ``WorkspaceReplayResponse``
    (just renamed for honesty — this is an ARTIFACT view, not a
    workspace view).  Frontend types alias both.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_hash: str
    mode: Literal["original", "current"]
    produced_under_commit: Optional[str]
    current_commit: Optional[str]
    commit_differs: bool
    methodology_version_ids: List[int]
    methodology_diffs: List[MethodologyDiff]
    reconstructed: List[ReconstructedMethodology]
    notes: List[str]


# ============================================================================
# Route
# ============================================================================


@router.get(
    "/{artifact_hash}/replay",
    response_model=ArtifactReplayResponse,
)
def artifact_replay(
    artifact_hash: str,
    mode: Literal["original", "current"] = Query("original"),
) -> ArtifactReplayResponse:
    """Surface methodology + application-version provenance for the
    artifact identified by ``artifact_hash``."""
    if len(artifact_hash) != 64 or not is_hex(artifact_hash):
        raise HTTPException(
            status_code=400,
            detail="artifact_hash must be a 64-char hex SHA-256 digest",
        )

    engine = get_engine()
    with engine.connect() as conn:
        try:
            summary = replay_summary_for_artifact(
                conn, artifact_hash, mode,
            )
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"No artifact with hash {artifact_hash}",
            )

    return ArtifactReplayResponse(**summary)


# ============================================================================
# R5.2 — Payload endpoint
# ============================================================================
#
# ``GET /api/v1/artifacts/{artifact_hash}/payload`` returns the full
# deserialised artifact contents (the ``StoredArtifact`` dict — the same
# shape that's stored in Postgres JSONB or object storage).  Used by
# Build's rich-model widgets (PCA / RollingRegression / Attribution)
# to render the full output without re-running the underlying tool.
#
# Response shape mirrors ``StoredArtifact``:
#
#   {"artifact_type": "...", "metadata": {...}, "payload": {...}}
#
# The same shape ``state.artifact_store.get_artifact_payload_dict``
# returns, validated via Pydantic at the storage boundary so a corrupt
# row surfaces as a clear server error rather than malformed JSON.


class ArtifactPayloadResponse(BaseModel):
    """Response shape for ``GET /artifacts/{hash}/payload``.

    Mirrors the ``StoredArtifact`` wire shape so a UI consumer can
    write ``response.payload.current_metrics.loadings`` (etc.) with
    no extra unwrapping.  ``metadata`` and ``payload`` are open
    dicts — every artifact type encodes its own schema inside them.
    """

    model_config = ConfigDict(extra="forbid")

    artifact_type: ArtifactTypeLiteral
    metadata: Dict[str, Any]
    payload: Dict[str, Any]


@router.get(
    "/{artifact_hash}/payload",
    response_model=ArtifactPayloadResponse,
)
def artifact_payload(
    artifact_hash: str,
    response: Response,
) -> ArtifactPayloadResponse:
    """Surface the deserialised contents of the artifact identified by
    ``artifact_hash``.

    400 if the hash isn't a 64-char hex SHA-256.  404 if no artifact
    metadata row exists, OR if the metadata row points at a missing
    object-storage URI (orphaned metadata).  500 if the row is
    malformed (CHECK constraint violation in storage).
    """
    if len(artifact_hash) != 64 or not is_hex(artifact_hash):
        raise HTTPException(
            status_code=400,
            detail="artifact_hash must be a 64-char hex SHA-256 digest",
        )

    engine = get_engine()
    object_storage = get_object_storage()
    if object_storage is None:
        # Mirrors the runtime guard the workspace replay route uses
        # when object storage isn't initialised — degrades to a clear
        # 503 rather than a None-deref later.
        raise HTTPException(
            status_code=503,
            detail="Object storage backend is not initialised.",
        )
    cache = get_artifact_cache()

    with engine.connect() as conn:
        try:
            stored_dict = get_artifact_payload_dict(
                artifact_hash,
                conn=conn,
                object_storage=object_storage,
                cache=cache,
            )
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"No artifact with hash {artifact_hash}",
            )
        except FileNotFoundError:
            # Orphaned metadata — row exists, but the blob URI it
            # points at doesn't.  Treat as not-found from the caller's
            # perspective; the underlying issue is operational.
            logger.warning(
                "Artifact %s has metadata but the payload blob is missing",
                artifact_hash,
            )
            raise HTTPException(
                status_code=404,
                detail=f"Artifact payload for {artifact_hash} is missing",
            )

    # Surface the artifact byte_size as a header so the UI can warn on
    # large downloads without parsing the body.  The actual byte count
    # for inline-stored artifacts is the JSONB column size; for blob-
    # stored ones it's the blob bytes.  Approximate via the JSON
    # serialization length — accurate enough for size warnings.
    import json as _json
    body_size = len(_json.dumps(stored_dict).encode("utf-8"))
    response.headers["X-Artifact-Size"] = str(body_size)

    return ArtifactPayloadResponse(**stored_dict)

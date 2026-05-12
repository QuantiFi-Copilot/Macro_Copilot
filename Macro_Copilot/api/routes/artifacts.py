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
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from api.dependencies import get_engine
from api.routes._replay_helpers import (
    MethodologyDiff,
    ReconstructedMethodology,
    is_hex,
    replay_summary_for_artifact,
)

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

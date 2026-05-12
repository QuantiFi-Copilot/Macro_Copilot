"""api/routes/workspace.py — workspace persistence + replay REST surface.

Phase 0 PR 10.

Three endpoints under ``/api/v1/workspace``:

  - ``POST /``
        Create a workspace from an existing ``dag_hash``.  Returns
        the workspace's slug + UUID + a self-describing URL.
        Body: ``{ dag_hash, name?, created_by?, focus_node? }``.

  - ``GET /{slug}``
        Fetch the workspace state + DAG topology + per-node
        artifact summaries.  Summaries-only by default: full
        payloads come from the artifact-store endpoints on
        per-card fetch.  Target ≤500ms p95 for moderate DAGs.

  - ``GET /{slug}/replay?mode=original|current``
        Walk every PrimitiveStep across every node in the DAG and
        aggregate the per-artifact replay surface (the same shape
        PR 9 returned for a single artifact, deduped across nodes
        so each unique methodology_version_id is reconstructed /
        diffed exactly once).

URL discipline
--------------
The slug is the URL handle.  Derived ONCE at create time by
``state.workspace_repo.derive_slug``; never re-derived.  Renames
update only the display ``name``.  The slug is therefore stable
across renames — the load-bearing six-month-replay guarantee.

The PR 9 single-artifact replay endpoint MOVED.  It now lives at
``GET /api/v1/artifacts/{hash}/replay`` (see
``api/routes/artifacts.py``).  Both routes share their replay
helpers via ``api/routes/_replay_helpers.py`` so divergence-
detection logic is canonical in one place.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from api.dependencies import get_engine
from api.routes._replay_helpers import (
    MethodologyDiff,
    ReconstructedMethodology,
    load_methodology_record,
    reconstruct_one,
    diff_one,
    is_hex,
)

logger = logging.getLogger("api.routes.workspace")


router = APIRouter()


# ============================================================================
# Request / response models
# ============================================================================


class CreateWorkspaceRequest(BaseModel):
    """Body for ``POST /workspace``."""

    model_config = ConfigDict(extra="forbid")

    dag_hash: str = Field(
        ...,
        min_length=64,
        max_length=64,
        description="64-char hex SHA-256 of the DAG to bind.",
    )
    name: Optional[str] = Field(
        default=None,
        max_length=256,
        description=(
            "Human-readable display name.  None for auto-named "
            "system workspaces.  The URL slug is derived from this "
            "name PLUS an 8-char hex UUID suffix; renames preserve "
            "the slug."
        ),
    )
    created_by: Optional[str] = Field(
        default=None, max_length=128,
        description="Application-defined user identifier.",
    )
    focus_node: Optional[str] = Field(
        default=None, max_length=256,
        description="UI focus pointer; not load-bearing for replay.",
    )


class CreateWorkspaceResponse(BaseModel):
    """Response from ``POST /workspace``."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    slug: str
    name: Optional[str]
    dag_hash: str
    url: str = Field(
        ...,
        description=(
            "Server-relative URL the frontend can route to "
            "(``/workspace/{slug}``).  Frontends free to prefix "
            "with their host."
        ),
    )


class NodeSummary(BaseModel):
    """One per ``dag_nodes`` row, surfaced with artifact metadata
    (when the node has been executed)."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    kind: str
    name: str
    params: Dict[str, Any]
    artifact_hash: Optional[str]
    artifact: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "ArtifactSummary dict when ``artifact_hash`` resolves; "
            "None for unexecuted nodes or nodes whose artifact has "
            "been GC'd.  Same shape as ``state.ArtifactSummary`` "
            "model_dump."
        ),
    )


class EdgeSummary(BaseModel):
    """One per ``dag_edges`` row."""

    model_config = ConfigDict(extra="forbid")

    from_node: str
    to_node: str
    slot_name: str


class WorkspaceDetailResponse(BaseModel):
    """Response from ``GET /workspace/{slug}``."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    slug: str
    name: Optional[str]
    dag_hash: str
    focus_node: Optional[str]
    parent_workspace_id: Optional[uuid.UUID]
    schema_version: int
    created_by: Optional[str]
    created_at: str
    updated_at: str
    nodes: List[NodeSummary]
    edges: List[EdgeSummary]


class WorkspaceReplayResponse(BaseModel):
    """Response from ``GET /workspace/{slug}/replay``.

    Aggregates per-artifact replay results across every node in the
    DAG, with the methodology surface DEDUPLICATED across nodes
    (each unique methodology_version_id is reconstructed / diffed
    once per request, not once per node that references it).
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    slug: str
    dag_hash: str
    mode: Literal["original", "current"]
    produced_under_commits: List[str] = Field(
        ...,
        description=(
            "Sorted-deduplicated list of distinct ``git_commit`` "
            "values pinned across the DAG's artifacts.  Length > 1 "
            "means the DAG mixes artifacts from multiple code "
            "revisions — a yellow flag for replay-faithfulness."
        ),
    )
    current_commit: Optional[str]
    commit_differs: bool
    node_artifact_hashes: List[str] = Field(
        ...,
        description=(
            "All node ``artifact_hash`` values in node_id order — "
            "the load-bearing identity set the integration test "
            "asserts is byte-identical across server restarts."
        ),
    )
    methodology_version_ids: List[int]
    methodology_diffs: List[MethodologyDiff]
    reconstructed: List[ReconstructedMethodology]
    notes: List[str]


# ============================================================================
# POST /workspace
# ============================================================================


@router.post(
    "",
    response_model=CreateWorkspaceResponse,
    status_code=201,
)
def create_workspace_endpoint(
    body: CreateWorkspaceRequest,
) -> CreateWorkspaceResponse:
    """Create a workspace pointing at an existing DAG.

    The DAG must already exist in ``copilot_state.dags`` (typically
    persisted via ``state.dag_repo.persist_dag_from_lineage`` after
    the head artifact has been put).  A request referencing an
    unknown ``dag_hash`` 404s.
    """
    from state.workspace_repo import (
        InvalidNameError,
        create_workspace,
    )

    if len(body.dag_hash) != 64 or not is_hex(body.dag_hash):
        raise HTTPException(
            status_code=400,
            detail="dag_hash must be a 64-char hex SHA-256 digest",
        )

    engine = get_engine()
    with engine.begin() as conn:
        # Confirm the dag exists — FK on workspaces.dag_hash will
        # catch a missing row but the 4xx is friendlier than a 500.
        row = conn.execute(
            text(
                "SELECT 1 FROM copilot_state.dags WHERE hash = :h"
            ),
            {"h": body.dag_hash},
        ).first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"No DAG with hash {body.dag_hash}",
            )

        try:
            rec = create_workspace(
                body.dag_hash,
                conn=conn,
                name=body.name,
                created_by=body.created_by,
                focus_node=body.focus_node,
            )
        except InvalidNameError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return CreateWorkspaceResponse(
        workspace_id=rec.id,
        slug=rec.slug,
        name=rec.name,
        dag_hash=rec.dag_hash,
        url=f"/workspace/{rec.slug}",
    )


# ============================================================================
# GET /workspace/{slug}
# ============================================================================


@router.get(
    "/{slug}",
    response_model=WorkspaceDetailResponse,
)
def get_workspace_endpoint(slug: str) -> WorkspaceDetailResponse:
    """Return workspace state + DAG topology + per-node artifact
    summaries.

    The artifact metadata lookup is summaries-only: one read per
    node from ``state.artifact_store.get_artifact_summary``.  No
    payload deserialization, no object-storage fetch.  Heavy
    payload loads are per-card opt-in via the existing artifact
    endpoint surface.
    """
    from state.artifact_store import get_artifact_summary
    from state.dag_repo import get_dag
    from state.workspace_repo import (
        UnknownWorkspaceError,
        get_workspace_by_slug,
    )

    engine = get_engine()
    with engine.connect() as conn:
        try:
            ws = get_workspace_by_slug(slug, conn=conn)
        except UnknownWorkspaceError:
            raise HTTPException(
                status_code=404, detail=f"No workspace with slug {slug!r}"
            )

        try:
            stored_dag = get_dag(ws.dag_hash, conn=conn)
        except KeyError:
            # Workspace.dag_hash FK is RESTRICT, so this shouldn't
            # happen, but a clean 500 is better than an opaque crash.
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Workspace {slug} references missing DAG "
                    f"{ws.dag_hash}"
                ),
            )

        nodes: List[NodeSummary] = []
        for node in stored_dag.nodes:
            summary_dict: Optional[Dict[str, Any]] = None
            if node.artifact_hash is not None:
                try:
                    summary = get_artifact_summary(
                        node.artifact_hash, conn=conn,
                    )
                    summary_dict = summary.model_dump(mode="json")
                except KeyError:
                    logger.warning(
                        "Workspace %s node %s references missing "
                        "artifact %s — surfacing None summary",
                        slug, node.node_id, node.artifact_hash,
                    )
            nodes.append(
                NodeSummary(
                    node_id=node.node_id,
                    kind=node.kind,
                    name=node.name,
                    params=node.params,
                    artifact_hash=node.artifact_hash,
                    artifact=summary_dict,
                )
            )
        edges = [
            EdgeSummary(
                from_node=e.from_node,
                to_node=e.to_node,
                slot_name=e.slot_name,
            )
            for e in stored_dag.edges
        ]

    return WorkspaceDetailResponse(
        workspace_id=ws.id,
        slug=ws.slug,
        name=ws.name,
        dag_hash=ws.dag_hash,
        focus_node=ws.focus_node,
        parent_workspace_id=ws.parent_workspace_id,
        schema_version=ws.schema_version,
        created_by=ws.created_by,
        created_at=ws.created_at.isoformat(),
        updated_at=ws.updated_at.isoformat(),
        nodes=nodes,
        edges=edges,
    )


# ============================================================================
# GET /workspace/{slug}/replay
# ============================================================================


@router.get(
    "/{slug}/replay",
    response_model=WorkspaceReplayResponse,
)
def replay_workspace_endpoint(
    slug: str,
    mode: Literal["original", "current"] = Query("original"),
) -> WorkspaceReplayResponse:
    """Walk every node of the workspace's DAG and aggregate the
    replay surface.

    Per-artifact computation reuses ``api.routes._replay_helpers``;
    the aggregation step deduplicates ``methodology_version_id``s
    across nodes so each unique YAML version is reconstructed /
    diffed at most once per request.
    """
    from state.dag_repo import get_dag
    from state.workspace_repo import (
        UnknownWorkspaceError,
        get_workspace_by_slug,
    )
    from state.methodology_versions import current_application_version_id

    engine = get_engine()
    with engine.connect() as conn:
        try:
            ws = get_workspace_by_slug(slug, conn=conn)
        except UnknownWorkspaceError:
            raise HTTPException(
                status_code=404, detail=f"No workspace with slug {slug!r}"
            )
        try:
            stored_dag = get_dag(ws.dag_hash, conn=conn)
        except KeyError:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Workspace {slug} references missing DAG "
                    f"{ws.dag_hash}"
                ),
            )

        notes: List[str] = []

        # Collect every artifact hash referenced by the DAG, dedup
        # the methodology version ids across them, and reconstruct
        # / diff each unique version once.
        node_artifact_hashes: List[str] = [
            n.artifact_hash for n in stored_dag.nodes
            if n.artifact_hash is not None
        ]
        if not node_artifact_hashes:
            notes.append(
                "DAG has no executed nodes — nothing to replay"
            )

        # Fetch each artifact's pinned ids + application_version_id
        # in one batched query so the route stays cheap even for
        # 20-node DAGs.
        version_ids_set: set = set()
        application_version_ids: set = set()
        if node_artifact_hashes:
            rows = conn.execute(
                text(
                    """
                    SELECT hash, methodology_version_ids,
                           application_version_id
                    FROM copilot_state.artifact_metadata
                    WHERE hash = ANY(:hashes)
                    """
                ),
                {"hashes": node_artifact_hashes},
            ).mappings().all()
            for r in rows:
                for vid in (r["methodology_version_ids"] or []):
                    version_ids_set.add(int(vid))
                if r["application_version_id"] is not None:
                    application_version_ids.add(
                        int(r["application_version_id"])
                    )

        sorted_version_ids = sorted(version_ids_set)

        # Application-version surface.
        produced_under_commits_set: set = set()
        for av_id in application_version_ids:
            commit_row = conn.execute(
                text(
                    "SELECT git_commit FROM "
                    "copilot_state.application_version WHERE id = :id"
                ),
                {"id": av_id},
            ).first()
            if commit_row is not None:
                produced_under_commits_set.add(commit_row[0])
        produced_under_commits = sorted(produced_under_commits_set)

        try:
            current_id = current_application_version_id(conn=conn)
            current_row = conn.execute(
                text(
                    "SELECT git_commit FROM "
                    "copilot_state.application_version WHERE id = :id"
                ),
                {"id": current_id},
            ).first()
            current_commit = current_row[0] if current_row else None
        except Exception as exc:
            current_commit = None
            notes.append(
                f"could not resolve current application_version: {exc}"
            )

        commit_differs = bool(
            current_commit is not None
            and produced_under_commits
            and any(c != current_commit for c in produced_under_commits)
        )

        # Per-unique-version reconstruction / diff.
        reconstructed: List[ReconstructedMethodology] = []
        diffs: List[MethodologyDiff] = []
        for vid in sorted_version_ids:
            try:
                rec = load_methodology_record(conn, vid)
            except KeyError:
                notes.append(
                    f"methodology_version_id {vid} no longer exists "
                    "in the registry — skipping"
                )
                continue
            if mode == "original":
                reconstructed.append(reconstruct_one(rec))
            else:
                diff = diff_one(rec)
                if diff is not None:
                    diffs.append(diff)

    return WorkspaceReplayResponse(
        workspace_id=ws.id,
        slug=ws.slug,
        dag_hash=ws.dag_hash,
        mode=mode,
        produced_under_commits=produced_under_commits,
        current_commit=current_commit,
        commit_differs=commit_differs,
        node_artifact_hashes=node_artifact_hashes,
        methodology_version_ids=sorted_version_ids,
        methodology_diffs=diffs,
        reconstructed=reconstructed,
        notes=notes,
    )



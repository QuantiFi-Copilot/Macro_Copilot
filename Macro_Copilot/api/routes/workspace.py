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
    """Response from ``GET /workspace/{slug}``.

    PR B extension: ``template_id`` + ``bound_slot_values`` are
    surfaced so the Build UI knows whether the fork affordance is
    available (both non-null = forkable; either null = locked).
    """

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
    template_id: Optional[str] = Field(
        default=None,
        description=(
            "WorkflowTemplate.template_id this workspace was produced "
            "by.  None on legacy (pre-PR-B) rows; in that case the "
            "fork-with-overrides affordance stays disabled."
        ),
    )
    bound_slot_values: Optional[Dict[str, Any]] = Field(
        default=None,
        description=(
            "Original slot_values dict the user / LLM passed when "
            "binding this workspace's template.  Required by the "
            "fork endpoint to apply a patch; None on legacy rows."
        ),
    )


class ForkWorkspaceRequest(BaseModel):
    """Body for ``POST /workspace/{slug}/fork``.

    The request describes a PATCH on the parent workspace's bound
    slot values.  Top-level scalar slots can be overridden in-place
    via ``slot_overrides``; nested dict slots (e.g. ``signal_params``)
    use ``slot_dict_overrides`` to surgically merge keys without
    forcing the client to resend the whole nested dict.
    """

    model_config = ConfigDict(extra="forbid")

    slot_overrides: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Direct replacements for top-level slots on the parent's "
            "``bound_slot_values``.  Empty when only nested-dict "
            "overrides are needed."
        ),
    )
    slot_dict_overrides: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Per-slot dict merges.  ``{signal_params: {window_days: "
            "126}}`` merges ``window_days`` into the parent's "
            "``signal_params`` dict; other keys on that dict are "
            "preserved.  Use this for the common case of editing one "
            "field inside a nested-params slot."
        ),
    )
    name: Optional[str] = Field(
        default=None,
        max_length=256,
        description=(
            "Display name for the variant workspace.  None lets the "
            "substrate auto-name it (slug-only)."
        ),
    )
    created_by: Optional[str] = Field(
        default=None, max_length=128,
        description="Application-defined user identifier.",
    )


class ForkWorkspaceResponse(BaseModel):
    """Response from ``POST /workspace/{slug}/fork``.

    Mirrors ``CreateWorkspaceResponse`` plus carries the
    diff-summary so the Build UI's VariantStrip can render the
    before/after deltas without a second round-trip.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    slug: str
    name: Optional[str]
    dag_hash: str
    parent_workspace_id: uuid.UUID
    url: str
    override_summary: Dict[str, Any] = Field(
        ...,
        description=(
            "Echo of what was applied: ``{changed: [...], "
            "new_slot_values: {...}, parent_slot_values: {...}}``.  "
            "Persisted in ``workspace_variants.override_summary`` "
            "for replay-side rendering."
        ),
    )


class WorkspaceListItem(BaseModel):
    """One row in the sidebar list response.

    Lightweight by design — no DAG topology, no per-node artifact
    summaries.  Those are fetched lazily via ``GET /{slug}`` when the
    user opens a specific workspace.  Carries just enough for the
    sidebar to render a row + decide ordering.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: uuid.UUID
    slug: str
    name: Optional[str]
    dag_hash: str
    focus_node: Optional[str]
    parent_workspace_id: Optional[uuid.UUID]
    created_by: Optional[str]
    created_at: str
    updated_at: str


class WorkspaceListResponse(BaseModel):
    """Response from ``GET /workspace?limit=N&filter=...``.

    Wrapper envelope rather than a bare list so future fields
    (``has_more``, ``total_count``, cursor tokens) can be added
    without bumping the response shape.
    """

    model_config = ConfigDict(extra="forbid")

    items: List[WorkspaceListItem]
    limit: int
    offset: int
    filter: str = Field(
        ...,
        description=(
            "Echoes the requested filter for client cache keying — "
            "matches one of the accepted values (``recent``, "
            "``all``, ``pinned``, ``shared``).  Forward-compat keys "
            "(pinned / shared) fall through to recent ordering "
            "until the substrate tracks those flags."
        ),
    )


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
# GET /workspace — sidebar list surface
# ============================================================================


@router.get(
    "",
    response_model=WorkspaceListResponse,
)
def list_workspaces_endpoint(
    limit: int = Query(
        25,
        ge=1,
        le=200,
        description=(
            "Maximum number of workspaces to return.  Clamped to "
            "[1, 200] server-side regardless of client value."
        ),
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Pagination offset; defaults to 0.",
    ),
    filter: str = Query(  # noqa: A002 - matches client ?filter=...
        "recent",
        description=(
            "Sidebar section semantics.  ``recent`` (default) orders "
            "by last_accessed_at NULL-coalesced to updated_at.  "
            "``all`` orders by updated_at only.  ``pinned`` / "
            "``shared`` are accepted for forward-compat with the "
            "sidebar UI's section taxonomy and currently fall "
            "through to recent ordering."
        ),
    ),
    parent_workspace_id: Optional[uuid.UUID] = Query(
        None,
        description=(
            "Optional parent filter — when set, returns only "
            "workspaces whose ``parent_workspace_id`` equals this "
            "value.  Powers the VariantStrip's siblings list."
        ),
    ),
) -> WorkspaceListResponse:
    """List workspaces for the Build sidebar.

    Read-only, summaries-only.  The per-row response carries just
    the identifying fields + ordering timestamps; opening a
    workspace via ``GET /{slug}`` fetches the full DAG topology +
    per-node artifact summaries.
    """
    from state.workspace_repo import list_workspaces

    engine = get_engine()
    with engine.connect() as conn:
        records = list_workspaces(
            conn=conn,
            limit=limit,
            offset=offset,
            filter=filter,
            parent_workspace_id=parent_workspace_id,
        )

    items = [
        WorkspaceListItem(
            workspace_id=r.id,
            slug=r.slug,
            name=r.name,
            dag_hash=r.dag_hash,
            focus_node=r.focus_node,
            parent_workspace_id=r.parent_workspace_id,
            created_by=r.created_by,
            created_at=r.created_at.isoformat(),
            updated_at=r.updated_at.isoformat(),
        )
        for r in records
    ]
    return WorkspaceListResponse(
        items=items,
        limit=limit,
        offset=offset,
        filter=filter,
    )


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
        touch_workspace_access,
    )

    engine = get_engine()
    # ``connect()`` returns an autocommit-style read connection; the
    # ``touch_workspace_access`` write is a tiny one-row UPDATE that
    # we wrap in an explicit transaction to keep the contract clean
    # (one statement, committed before the read returns).  Failures
    # are logged but never block the read — stamping last_accessed_at
    # is a best-effort observability signal, not a correctness gate.
    with engine.connect() as conn:
        try:
            ws = get_workspace_by_slug(slug, conn=conn)
        except UnknownWorkspaceError:
            raise HTTPException(
                status_code=404, detail=f"No workspace with slug {slug!r}"
            )

        try:
            with conn.begin():
                touch_workspace_access(slug, conn=conn)
        except Exception as exc:  # defensive: never block the read
            logger.warning(
                "touch_workspace_access failed for slug=%s: %s",
                slug, exc,
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
        template_id=ws.template_id,
        bound_slot_values=ws.bound_slot_values,
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


# ============================================================================
# POST /workspace/{slug}/fork — fork-with-overrides (PR B)
# ============================================================================


@router.post(
    "/{slug}/fork",
    response_model=ForkWorkspaceResponse,
    status_code=201,
)
def fork_workspace_endpoint(
    slug: str,
    body: ForkWorkspaceRequest,
) -> ForkWorkspaceResponse:
    """Create a variant workspace by patching the parent's bound
    slot values and re-running the same template.

    Failure modes
    -------------
    - 404 — parent slug doesn't exist.
    - 409 — parent has NULL ``template_id`` or ``bound_slot_values``
      (legacy workspace; cannot fork).
    - 422 — applying the patch fails (slot binding refusal: bad
      enum, type mismatch, constraint violation).
    - 500 — execution failed inside the substrate.

    On success
    ----------
    Persists a new workspace whose ``parent_workspace_id`` points at
    the source, populates a ``workspace_variants`` row, and returns
    the new slug + an override summary suitable for the
    VariantStrip's before/after rendering.
    """
    from api.dependencies import (
        get_engine,
        get_object_storage,
        init_object_storage,
    )
    from rates_agent.workflows._runner import run_template
    from state.workspace_repo import (
        UnknownWorkspaceError,
        get_workspace_by_slug,
    )

    engine = get_engine()
    try:
        object_storage = get_object_storage()
    except RuntimeError:
        object_storage = init_object_storage()

    # 1. Resolve the parent workspace.
    with engine.connect() as conn:
        try:
            parent = get_workspace_by_slug(slug, conn=conn)
        except UnknownWorkspaceError:
            raise HTTPException(
                status_code=404,
                detail=f"No workspace with slug {slug!r}",
            )

    # 2. Confirm the parent is forkable.
    if not parent.template_id or parent.bound_slot_values is None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Workspace {slug!r} is not forkable: it predates the "
                "PR-B persistence schema and has NULL template_id / "
                "bound_slot_values.  Re-run the original prompt to "
                "produce a forkable workspace."
            ),
        )

    # 3. Apply the patch to the parent's slot values.
    new_slot_values, changed_keys = _apply_slot_overrides(
        dict(parent.bound_slot_values or {}),
        body.slot_overrides,
        body.slot_dict_overrides,
    )

    if not changed_keys:
        raise HTTPException(
            status_code=400,
            detail=(
                "Fork request has no effective overrides.  Pass at "
                "least one slot in ``slot_overrides`` or "
                "``slot_dict_overrides`` to produce a meaningful "
                "variant."
            ),
        )

    # 4. Re-run the template with the patched values, persisting the
    #    result as a child workspace.  ``run_template`` handles
    #    template resolution + slot binding refusals + execution
    #    failures — we surface those as 422 / 500 respectively.
    envelope = run_template(
        parent.template_id,
        new_slot_values,
        engine=engine,
        persist=True,
        object_storage=object_storage,
        workspace_name=body.name,
        workspace_created_by=body.created_by,
        parent_workspace_id=parent.id,
    )

    if not envelope.get("ok"):
        err = envelope.get("error") or "(no detail)"
        # Slot-binding refusals carry the substrate's clear message —
        # surface as 422 so the client can render it inline.
        status = 422 if "Slot binding" in err or "validation" in err else 500
        raise HTTPException(status_code=status, detail=err)

    persistence = envelope.get("persistence") or {}
    if not persistence.get("ok"):
        raise HTTPException(
            status_code=500,
            detail=(
                f"Workflow executed but persistence failed: "
                f"{persistence.get('error') or '(no detail)'}"
            ),
        )

    workspace = envelope["workspace"]

    # 5. Record an override-summary row in workspace_variants so the
    #    VariantStrip can render the diff without re-deriving it.
    #    The fork helper handles the workspaces.parent_workspace_id
    #    column; we extend that with the variant-side row here.
    override_summary = {
        "template_id": parent.template_id,
        "changed": sorted(changed_keys),
        "new_slot_values": new_slot_values,
        "parent_slot_values": dict(parent.bound_slot_values or {}),
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO copilot_state.workspace_variants (
                    workspace_id, variant_dag_hash, override_summary
                )
                VALUES (
                    :workspace_id, :variant_dag_hash,
                    CAST(:override_summary AS JSONB)
                )
                ON CONFLICT DO NOTHING
                """
            ),
            {
                "workspace_id": workspace["id"],
                "variant_dag_hash": workspace["dag_hash"],
                "override_summary": _json_dumps(override_summary),
            },
        )

    return ForkWorkspaceResponse(
        workspace_id=uuid.UUID(workspace["id"]),
        slug=workspace["slug"],
        name=workspace["name"],
        dag_hash=workspace["dag_hash"],
        parent_workspace_id=parent.id,
        url=workspace["url"],
        override_summary=override_summary,
    )


def _apply_slot_overrides(
    base: Dict[str, Any],
    scalar_overrides: Dict[str, Any],
    dict_overrides: Dict[str, Dict[str, Any]],
) -> tuple:
    """Apply both override flavors to a copy of ``base`` and return
    ``(new_slot_values, changed_keys)``.

    Discipline:
      - ``scalar_overrides`` replaces top-level slots wholesale.
        Setting a value identical to the existing one is a no-op
        (won't be reported as changed).
      - ``dict_overrides`` merges per-key into nested dict slots.
        Non-dict existing values are replaced wholesale; the merge
        is shallow (no recursive merging — keep the semantics
        predictable).
      - Both flavors are applied left-to-right; the dict-merge
        happens AFTER scalars so a slot can be cleared then
        re-keyed in one request without ordering bugs.
    """
    out = dict(base)
    changed: set = set()

    for key, val in scalar_overrides.items():
        if out.get(key) == val:
            continue
        out[key] = val
        changed.add(key)

    for key, patch in dict_overrides.items():
        existing = out.get(key)
        if not isinstance(existing, dict):
            # Replace wholesale — keeps semantics predictable when
            # the parent's slot was a scalar / list and the client
            # sends a dict patch.
            out[key] = dict(patch)
            changed.add(key)
            continue
        merged = dict(existing)
        actually_changed = False
        for k, v in patch.items():
            if merged.get(k) != v:
                merged[k] = v
                actually_changed = True
        if actually_changed:
            out[key] = merged
            changed.add(key)

    return out, changed


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, default=str)



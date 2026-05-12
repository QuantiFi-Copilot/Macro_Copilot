"""api/routes/workspace.py — methodology-pinned replay surface.

Phase 0 PR 9.

Endpoint: ``GET /api/v1/workspace/{artifact_hash}?mode={original|current}``

What this does
--------------
Given an artifact hash, surface the methodology + application-version
provenance that PR 9 captures, and either:

  - ``mode=original``  — reconstruct each YAML version pinned at
    artifact-production time from
    ``copilot_state.methodology_versions.yaml_content``.  This is
    the replay-faithful view: the YAML content is loaded from the
    registry, NOT the filesystem, so a subsequent on-disk edit
    cannot taint the reconstructed config.  Validates each
    reconstructed dict round-trips into ``ToolConfig`` (proves the
    Pydantic round-trip is loss-less for the stored data).

  - ``mode=current``   — for each pinned methodology_version_id,
    compare its YAML content against what the same on-disk path
    holds NOW.  Reports ``methodology_diffs`` listing every
    version whose stored hash differs from the current
    on-disk hash.  The frontend / test can use the diff array to
    flag "this analysis would produce a different artifact under
    the current YAML".

Why this is NOT a re-executor
-----------------------------
PR 9 wires the substrate (registry + per-artifact pinning) but
deliberately does NOT plug the primitive executor into the route.
Re-execution requires the per-tool ``calculate_*`` functions plus a
live DB connection to fetch the underlying market-data slice — a
heavier integration that belongs to the workspace-persistence PR
(Phase 0 PR 11) once the workspace lifecycle is fully lit.

What the route DOES guarantee
-----------------------------
1. ``application_version_id`` of the artifact is surfaced as a
   ``produced_under_commit`` string, alongside the
   ``current_commit`` (the running process's git rev-parse HEAD,
   cached via ``state.methodology_versions``).  A boolean
   ``commit_differs`` makes the divergence cheap to detect.

2. Every ``methodology_version_id`` in the artifact's array is
   loaded.  In ``original`` mode the stored YAML content is
   round-tripped through ``ToolConfig.model_validate`` so a
   reader-side failure surfaces here, not later in a primitive
   re-execution that doesn't yet exist.

3. In ``current`` mode, on-disk YAML drift is detected at the
   ``yaml_content_hash`` level (cheapest sound comparison).

Mode-default
------------
``mode`` defaults to ``original`` — the replay-faithful view.  An
operator who wants the divergence report opts in explicitly with
``?mode=current``.

Mounting
--------
Mounted under ``/api/v1/workspace`` in ``api/server.py`` alongside
the rates + workflows routers.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from api.dependencies import get_engine

logger = logging.getLogger("api.routes.workspace")


router = APIRouter()


# ============================================================================
# Response models
# ============================================================================


class MethodologyDiff(BaseModel):
    """One per stored methodology version that differs from the
    current on-disk YAML.

    ``original_version_id`` is the id pinned on the artifact.
    ``current_version_id`` is the id the same on-disk YAML would
    register as TODAY (None when the file no longer exists).
    ``fields_changed`` is a best-effort top-level diff of the
    parsed dicts — empty when the file is missing.
    """

    model_config = ConfigDict(extra="forbid")

    yaml_path: str
    original_version_id: int
    original_content_hash: str
    current_version_id: Optional[int]
    current_content_hash: Optional[str]
    file_exists_on_disk: bool
    fields_changed: List[str]


class ReconstructedMethodology(BaseModel):
    """One per pinned methodology version in ``original`` mode.

    Carries the reconstructed parsed dict plus a flag confirming the
    Pydantic round-trip via ``ToolConfig`` succeeded.  Tests assert
    ``tool_config_round_trip_ok`` is True for every YAML in the
    catalogue.
    """

    model_config = ConfigDict(extra="forbid")

    yaml_path: str
    version_id: int
    yaml_content_hash: str
    tool_config_round_trip_ok: bool
    tool_config_hash: Optional[str]


class WorkspaceReplayResponse(BaseModel):
    """The route's top-level response."""

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
    "/{artifact_hash}",
    response_model=WorkspaceReplayResponse,
)
def workspace_replay(
    artifact_hash: str,
    mode: Literal["original", "current"] = Query("original"),
) -> WorkspaceReplayResponse:
    """Surface the methodology + application-version provenance of
    an artifact under the requested replay mode.

    See module docstring for the contract.
    """
    if len(artifact_hash) != 64 or not _is_hex(artifact_hash):
        raise HTTPException(
            status_code=400,
            detail=(
                "artifact_hash must be a 64-char hex SHA-256 digest"
            ),
        )

    engine = get_engine()
    notes: List[str] = []

    with engine.connect() as conn:
        artifact_row = conn.execute(
            text(
                """
                SELECT methodology_version_ids, application_version_id
                FROM copilot_state.artifact_metadata
                WHERE hash = :h
                """
            ),
            {"h": artifact_hash},
        ).mappings().first()

        if artifact_row is None:
            raise HTTPException(
                status_code=404,
                detail=f"No artifact with hash {artifact_hash}",
            )

        methodology_version_ids: List[int] = list(
            artifact_row["methodology_version_ids"] or []
        )
        application_version_id: Optional[int] = artifact_row[
            "application_version_id"
        ]

        # ------------------------------------------------------------------
        # Application-version surface
        # ------------------------------------------------------------------
        produced_under_commit = _load_git_commit(
            conn, application_version_id,
        )
        try:
            from state.methodology_versions import (
                current_application_version_id,
            )
            current_id = current_application_version_id(conn=conn)
            current_commit = _load_git_commit(conn, current_id)
        except Exception as exc:
            current_commit = None
            notes.append(
                f"could not resolve current application_version: {exc}"
            )
        commit_differs = (
            produced_under_commit is not None
            and current_commit is not None
            and produced_under_commit != current_commit
        )

        if produced_under_commit is None:
            notes.append(
                "artifact predates Phase 0 PR 9 (no application_version_id "
                "recorded); application-version divergence cannot be "
                "checked"
            )

        # ------------------------------------------------------------------
        # Methodology surface (mode-dependent)
        # ------------------------------------------------------------------
        if not methodology_version_ids:
            notes.append(
                "artifact has no methodology_version_ids recorded "
                "(PR 7 / PR 8 era row, or a Lineage with no PrimitiveStep)"
            )

        reconstructed: List[ReconstructedMethodology] = []
        diffs: List[MethodologyDiff] = []

        for vid in methodology_version_ids:
            try:
                rec = _load_methodology_record(conn, vid)
            except KeyError:
                notes.append(
                    f"methodology_version_id {vid} no longer exists "
                    "in the registry — skipping"
                )
                continue

            if mode == "original":
                reconstructed.append(
                    _reconstruct_one(rec)
                )
            else:  # mode == "current"
                diff = _diff_one(rec)
                if diff is not None:
                    diffs.append(diff)

    return WorkspaceReplayResponse(
        artifact_hash=artifact_hash,
        mode=mode,
        produced_under_commit=produced_under_commit,
        current_commit=current_commit,
        commit_differs=commit_differs,
        methodology_version_ids=methodology_version_ids,
        methodology_diffs=diffs,
        reconstructed=reconstructed,
        notes=notes,
    )


# ============================================================================
# Helpers
# ============================================================================


def _is_hex(s: str) -> bool:
    try:
        int(s, 16)
    except ValueError:
        return False
    return True


def _load_git_commit(conn, application_version_id: Optional[int]) -> Optional[str]:
    if application_version_id is None:
        return None
    row = conn.execute(
        text(
            "SELECT git_commit FROM copilot_state.application_version "
            "WHERE id = :id"
        ),
        {"id": application_version_id},
    ).first()
    return row[0] if row is not None else None


def _load_methodology_record(conn, version_id: int):
    from state.methodology_versions import get_yaml

    return get_yaml(version_id, conn=conn)


def _reconstruct_one(rec) -> ReconstructedMethodology:
    """Round-trip the stored yaml_content through ``ToolConfig`` and
    return the result.  Failure does NOT raise — we capture the
    round-trip outcome on the response so the test can assert it
    rather than getting a 500."""
    from shared.config.tool_config import ToolConfig

    round_trip_ok = False
    tool_config_hash: Optional[str] = None
    try:
        cfg = ToolConfig.model_validate(rec.yaml_content)
        round_trip_ok = True
        tool_config_hash = cfg.conventions_hash()
    except Exception as exc:
        logger.warning(
            "ToolConfig round-trip failed for methodology_version_id %d "
            "(path=%s): %s",
            rec.id, rec.yaml_path, exc,
        )

    return ReconstructedMethodology(
        yaml_path=rec.yaml_path,
        version_id=rec.id,
        yaml_content_hash=rec.yaml_content_hash,
        tool_config_round_trip_ok=round_trip_ok,
        tool_config_hash=tool_config_hash,
    )


def _diff_one(rec) -> Optional[MethodologyDiff]:
    """Compare the registry-stored YAML against the current on-disk
    file at ``rec.yaml_path``.  Returns None when the contents match
    (no diff to report); returns a populated ``MethodologyDiff`` when
    the file is missing OR differs.
    """
    from state.methodology_versions import (
        _hash_yaml_content,
        canonicalize_yaml_content,
    )

    path = Path(rec.yaml_path)
    if not path.is_file():
        return MethodologyDiff(
            yaml_path=rec.yaml_path,
            original_version_id=rec.id,
            original_content_hash=rec.yaml_content_hash,
            current_version_id=None,
            current_content_hash=None,
            file_exists_on_disk=False,
            fields_changed=[],
        )

    import yaml as _yaml

    try:
        with path.open("r") as f:
            current_parsed = _yaml.safe_load(f.read())
    except Exception as exc:
        logger.warning(
            "could not load current YAML at %s: %s", path, exc,
        )
        return MethodologyDiff(
            yaml_path=rec.yaml_path,
            original_version_id=rec.id,
            original_content_hash=rec.yaml_content_hash,
            current_version_id=None,
            current_content_hash=None,
            file_exists_on_disk=True,
            fields_changed=[],
        )

    if not isinstance(current_parsed, dict):
        return None

    current_canonical = canonicalize_yaml_content(current_parsed)
    current_hash = _hash_yaml_content(current_canonical)

    if current_hash == rec.yaml_content_hash:
        return None  # No diff — file matches the pinned snapshot.

    # Compute a best-effort top-level diff so the response carries
    # operator-readable signal instead of just "they differ".  We diff
    # the leaf-value paths in the conventions block; anything else
    # at the top level lands under the "other" bucket.
    fields_changed = _top_level_diff(rec.yaml_content, current_canonical)

    return MethodologyDiff(
        yaml_path=rec.yaml_path,
        original_version_id=rec.id,
        original_content_hash=rec.yaml_content_hash,
        current_version_id=None,  # Not registered here; caller can re-register
        current_content_hash=current_hash,
        file_exists_on_disk=True,
        fields_changed=fields_changed,
    )


def _top_level_diff(orig: Dict[str, Any], curr: Dict[str, Any]) -> List[str]:
    """Return a list of ``conventions.<key>`` paths whose values
    differ, plus a bucket for any top-level structural change.

    Best-effort: handles the common case of "someone bumped one
    convention value" cleanly; falls back to a top-level
    "<root>" entry when the dict shape diverges more broadly.
    """
    changed: List[str] = []

    orig_conv = orig.get("conventions") if isinstance(orig, dict) else None
    curr_conv = curr.get("conventions") if isinstance(curr, dict) else None
    if isinstance(orig_conv, dict) and isinstance(curr_conv, dict):
        keys = set(orig_conv) | set(curr_conv)
        for k in sorted(keys):
            o = orig_conv.get(k)
            c = curr_conv.get(k)
            # We care about the ``.value`` field — the source /
            # rationale / valid_range are documentation, not identity
            # (matches ``ToolConfig.conventions_hash`` semantics).
            o_val = o.get("value") if isinstance(o, dict) else o
            c_val = c.get("value") if isinstance(c, dict) else c
            if o_val != c_val:
                changed.append(f"conventions.{k}")

    # Surface any other top-level mismatch as a coarse path.
    for key in sorted(set(orig.keys()) | set(curr.keys())):
        if key == "conventions":
            continue
        if orig.get(key) != curr.get(key):
            changed.append(key)

    return changed

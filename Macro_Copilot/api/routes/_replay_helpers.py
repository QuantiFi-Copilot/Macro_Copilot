"""api/routes/_replay_helpers.py — shared replay-route plumbing.

Phase 0 PR 10.

Two routes ship in PR 10 that surface methodology + application-
version provenance:

  - ``GET /api/v1/artifacts/{hash}/replay`` — single-artifact view
    (the PR 9 surface, relocated under ``/artifacts`` so the
    workspace path is freed for slug routing).
  - ``GET /api/v1/workspace/{slug}/replay`` — workspace-level view;
    walks every node of the DAG and aggregates the per-artifact
    surfaces.

Both routes share the same divergence-detection logic.  That logic
lives here so each route module imports one canonical implementation.

What this module is NOT
-----------------------
This module is NOT the route handler.  It is the substrate the
handlers call.  HTTP shape (status codes, query parsing, response
mounting) stays in the route modules so each module is single-purpose
and the swagger surface matches.

Reuse contract
--------------
- ``MethodologyDiff`` / ``ReconstructedMethodology`` —
  re-exported.  Both new routes use these shapes.
- ``replay_summary_for_artifact(conn, artifact_hash, mode)`` —
  computes the per-artifact surface that PR 9 returned.  The
  workspace replay handler calls this once per DAG node and
  aggregates.
- ``aggregate_workspace_replay(per_artifact)`` — dedups
  methodology_version_ids across artifacts so each unique YAML
  version is reconstructed / diffed exactly once per request.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

logger = logging.getLogger("api.routes._replay_helpers")


# ============================================================================
# Response models — re-used by both routes
# ============================================================================


class MethodologyDiff(BaseModel):
    """One per stored methodology version that differs from the
    current on-disk YAML.

    See ``api/routes/workspace.py`` (Phase 0 PR 9) for the original
    field-by-field rationale; this is the same shape, relocated.
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
    """One per pinned methodology version in ``original`` mode."""

    model_config = ConfigDict(extra="forbid")

    yaml_path: str
    version_id: int
    yaml_content_hash: str
    tool_config_round_trip_ok: bool
    tool_config_hash: Optional[str]


# ============================================================================
# Helpers — pure-Python where possible; SQL helpers take Connection
# ============================================================================


def is_hex(s: str) -> bool:
    try:
        int(s, 16)
    except ValueError:
        return False
    return True


def load_git_commit(
    conn, application_version_id: Optional[int],
) -> Optional[str]:
    """Resolve ``application_version.git_commit`` for an id.

    Returns None for None inputs or unknown ids (the latter
    indicates schema drift, not a normal application state — the
    caller surfaces the unknown via a ``notes`` entry).
    """
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


def load_methodology_record(conn, version_id: int):
    """Defer to ``state.methodology_versions.get_yaml``.

    Wrapped here so the route module's import surface stays small.
    """
    from state.methodology_versions import get_yaml

    return get_yaml(version_id, conn=conn)


def reconstruct_one(rec) -> ReconstructedMethodology:
    """Round-trip a stored YAML record through ``ToolConfig`` to
    confirm replay-faithfulness.  Failure is captured on the
    response, not raised — callers can render the result even when
    a single YAML's round-trip is broken."""
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


def diff_one(rec) -> Optional[MethodologyDiff]:
    """Compare stored YAML vs the current on-disk file at
    ``rec.yaml_path``.  Returns None when no diff to report.
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
        return None

    fields_changed = top_level_diff(rec.yaml_content, current_canonical)
    return MethodologyDiff(
        yaml_path=rec.yaml_path,
        original_version_id=rec.id,
        original_content_hash=rec.yaml_content_hash,
        current_version_id=None,
        current_content_hash=current_hash,
        file_exists_on_disk=True,
        fields_changed=fields_changed,
    )


def top_level_diff(
    orig: Dict[str, Any], curr: Dict[str, Any],
) -> List[str]:
    """Best-effort top-level diff between two parsed YAML dicts.

    Surfaces ``conventions.<key>`` paths whose ``.value`` differs,
    plus any other top-level key whose payload diverges.  Same
    semantics as PR 9's diff (matches ``ToolConfig.conventions_hash``
    identity definition).
    """
    changed: List[str] = []
    orig_conv = orig.get("conventions") if isinstance(orig, dict) else None
    curr_conv = curr.get("conventions") if isinstance(curr, dict) else None
    if isinstance(orig_conv, dict) and isinstance(curr_conv, dict):
        keys = set(orig_conv) | set(curr_conv)
        for k in sorted(keys):
            o = orig_conv.get(k)
            c = curr_conv.get(k)
            o_val = o.get("value") if isinstance(o, dict) else o
            c_val = c.get("value") if isinstance(c, dict) else c
            if o_val != c_val:
                changed.append(f"conventions.{k}")
    for key in sorted(set(orig.keys()) | set(curr.keys())):
        if key == "conventions":
            continue
        if orig.get(key) != curr.get(key):
            changed.append(key)
    return changed


# ============================================================================
# Higher-level: per-artifact replay summary
# ============================================================================


def replay_summary_for_artifact(
    conn,
    artifact_hash: str,
    mode: Literal["original", "current"],
) -> Dict[str, Any]:
    """Compute the PR 9 single-artifact response body for an
    artifact_hash.

    Returns a plain dict (not the Pydantic response model) so the
    workspace replay aggregator can compose multiple of these
    cheaply.  Callers wrap into ``ArtifactReplayResponse``.

    Raises ``KeyError`` if the artifact does not exist.

    The ``notes`` list is best-effort operator signal.  Empty when
    the artifact is fully PR 9-pinned; populated when something is
    missing or degraded (pre-PR-9 row, current-commit resolution
    failure, etc.).
    """
    notes: List[str] = []

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
        raise KeyError(f"No artifact with hash {artifact_hash!r}")

    methodology_version_ids: List[int] = list(
        artifact_row["methodology_version_ids"] or []
    )
    application_version_id: Optional[int] = artifact_row[
        "application_version_id"
    ]

    produced_under_commit = load_git_commit(conn, application_version_id)
    try:
        from state.methodology_versions import current_application_version_id
        current_id = current_application_version_id(conn=conn)
        current_commit = load_git_commit(conn, current_id)
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
            "recorded); application-version divergence cannot be checked"
        )
    if not methodology_version_ids:
        notes.append(
            "artifact has no methodology_version_ids recorded "
            "(PR 7 / PR 8 era row, or a Lineage with no PrimitiveStep)"
        )

    reconstructed: List[ReconstructedMethodology] = []
    diffs: List[MethodologyDiff] = []
    for vid in methodology_version_ids:
        try:
            rec = load_methodology_record(conn, vid)
        except KeyError:
            notes.append(
                f"methodology_version_id {vid} no longer exists in the "
                "registry — skipping"
            )
            continue
        if mode == "original":
            reconstructed.append(reconstruct_one(rec))
        else:
            diff = diff_one(rec)
            if diff is not None:
                diffs.append(diff)

    return {
        "artifact_hash": artifact_hash,
        "mode": mode,
        "produced_under_commit": produced_under_commit,
        "current_commit": current_commit,
        "commit_differs": commit_differs,
        "methodology_version_ids": methodology_version_ids,
        "methodology_diffs": diffs,
        "reconstructed": reconstructed,
        "notes": notes,
    }

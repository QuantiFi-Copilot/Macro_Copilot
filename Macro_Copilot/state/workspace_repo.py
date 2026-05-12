"""state.workspace_repo — workspace CRUD + slug derivation.

Phase 0 PR 10.

A workspace is the user-addressable handle for a DAG.  It binds:

  - a stable URL slug (the routing key),
  - a human-editable display name (mutable; URL stays stable),
  - the ``dag_hash`` it renders,
  - parent / fork lineage for variants,
  - the application-defined ``created_by`` user id.

URL discipline — slug vs name
-----------------------------
The slug is the URL handle.  It is DERIVED ONCE at create time
and NEVER re-derived:

    slug = slugify(name) + "-" + uuid.hex[:8]

Renames mutate ``name`` only — the slug (and therefore the URL)
is preserved.  Two workspaces sharing a display name produce
distinct slugs because the 8-char hex suffix is rooted in the
workspace's UUID (random, 2^32 namespace per name).  Collisions
are theoretically possible but vanishingly unlikely; the partial
unique index ``ix_workspaces_slug_active`` is the DB-level
backstop.

For system / unnamed workspaces (``name=None``), the slug is
just the 8-char hex prefix with no name component:
``ws-{hex8}``.

Slug character set
------------------
Only ``[a-z0-9-]``.  Adversarial inputs are stripped:

  - Unicode is NFKD-normalised, then non-ASCII chars are dropped.
  - Anything outside ``[a-z0-9]`` collapses to ``-``.
  - Consecutive ``-`` are collapsed to one.
  - Leading / trailing ``-`` are trimmed.
  - The name component is capped at ``_MAX_NAME_SLUG_LEN``
    characters BEFORE the 8-char UUID suffix is appended.

The slugifier is unit-tested with hostile inputs
(``"; DROP TABLE workspaces"``, ``"../../../etc/passwd"``,
unicode, leading/trailing punctuation) in
``tests/state/test_workspace_repo.py``.

Connection injection
--------------------
Every public function follows the PR 3 ``*, conn`` convention.
The caller owns the transaction.  Production callers wrap
``create_workspace`` and ``put_artifact`` + ``persist_dag_from_lineage``
in the same ``engine.begin()`` block so the workspace, the DAG, and
the head artifact all land atomically.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger("state.workspace_repo")


# ============================================================================
# Constants
# ============================================================================

_COPILOT_STATE_SCHEMA = "copilot_state"

# 8 hex chars from the UUID — 2^32 namespace per slug prefix.
_UUID_SUFFIX_LEN = 8

# Cap on the human-readable component of the slug.  Plenty for any
# realistic display name; the suffix adds 9 chars (``-`` + 8 hex)
# to land below typical URL-segment limits.
_MAX_NAME_SLUG_LEN = 88

# Display-name guardrails.  Wide enough for any realistic title;
# protects the table from accidental large blobs.
_MAX_DISPLAY_NAME_LEN = 256

_SLUG_ALLOWED = re.compile(r"^[a-z0-9-]+$")
_SLUG_COLLAPSE_DASHES = re.compile(r"-+")
_SLUG_REPLACE_NONALNUM = re.compile(r"[^a-z0-9]+")

# Auto-naming prefix for unnamed workspaces — surfaces as ``ws-{hex8}``.
_UNNAMED_PREFIX = "ws"


# ============================================================================
# Public dataclasses
# ============================================================================


@dataclass(frozen=True)
class WorkspaceRecord:
    """One row from ``copilot_state.workspaces``."""

    id: uuid.UUID
    slug: str
    name: Optional[str]
    dag_hash: str
    focus_node: Optional[str]
    parent_workspace_id: Optional[uuid.UUID]
    schema_version: int
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WorkspaceVariantRecord:
    """One row from ``copilot_state.workspace_variants``."""

    id: int
    workspace_id: uuid.UUID
    variant_dag_hash: str
    override_summary: Dict[str, Any]
    created_at: datetime


# ============================================================================
# Errors
# ============================================================================


class WorkspaceError(Exception):
    """Base class for workspace-repo errors."""


class InvalidNameError(WorkspaceError):
    """Raised when a display name fails validation."""


class UnknownWorkspaceError(WorkspaceError):
    """Raised by ``get_workspace`` / ``rename_workspace`` /
    ``fork_workspace`` when the id (or slug) does not exist."""


# ============================================================================
# Slug derivation
# ============================================================================


def slugify(name: Optional[str]) -> str:
    """Render the name component of a slug.

    Idempotent under repeated application: ``slugify(slugify(x)) ==
    slugify(x)``.  Returns ``_UNNAMED_PREFIX`` when the input is
    empty / whitespace / produces an empty slug after sanitisation.

    Adversarial-input safety: the only characters that survive are
    ``[a-z0-9-]``.  SQL injection vectors collapse harmlessly to
    dashes (e.g. ``"; DROP TABLE workspaces"`` → ``drop-table-workspaces``).
    """
    if name is None or not str(name).strip():
        return _UNNAMED_PREFIX

    # NFKD-normalise + drop non-ASCII so "é" -> "e", "über" -> "uber"
    # and CJK chars are removed (no honest transliteration for them).
    normalised = unicodedata.normalize("NFKD", str(name))
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    dashed = _SLUG_REPLACE_NONALNUM.sub("-", lowered)
    collapsed = _SLUG_COLLAPSE_DASHES.sub("-", dashed).strip("-")
    if not collapsed:
        return _UNNAMED_PREFIX
    return collapsed[:_MAX_NAME_SLUG_LEN].strip("-") or _UNNAMED_PREFIX


def derive_slug(name: Optional[str], workspace_uuid: uuid.UUID) -> str:
    """Build the canonical slug for a (name, uuid) pair.

    Pure function — same inputs produce the same slug.  Called
    ONCE per workspace at ``create_workspace`` time and never
    re-derived.
    """
    prefix = slugify(name)
    suffix = workspace_uuid.hex[:_UUID_SUFFIX_LEN]
    candidate = f"{prefix}-{suffix}"
    # Defensive: the pattern guarantees the result is slug-shaped,
    # but assert here so a bug in slugify surfaces loudly rather
    # than poisoning the URL space.
    assert _SLUG_ALLOWED.fullmatch(candidate), (
        f"derive_slug produced non-conformant slug {candidate!r}"
    )
    return candidate


# ============================================================================
# Name validation
# ============================================================================


def _validate_name(name: Optional[str]) -> None:
    """Validate a workspace display name.

    None is allowed (system workspaces).  Empty strings, whitespace-
    only strings, very long strings, and strings containing control
    characters are rejected.
    """
    if name is None:
        return
    if not isinstance(name, str):
        raise InvalidNameError(
            f"name must be str or None; got {type(name).__name__}"
        )
    stripped = name.strip()
    if not stripped:
        raise InvalidNameError("name cannot be empty / whitespace-only")
    if len(name) > _MAX_DISPLAY_NAME_LEN:
        raise InvalidNameError(
            f"name longer than {_MAX_DISPLAY_NAME_LEN} chars: "
            f"{len(name)}"
        )
    for ch in name:
        # Control chars are forbidden — they don't survive the slug
        # pipeline anyway and they can break log rendering / shell
        # output.
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            raise InvalidNameError(
                f"name contains a control character "
                f"(ord=0x{ord(ch):02x})"
            )


# ============================================================================
# Public API
# ============================================================================


def create_workspace(
    dag_hash: str,
    *,
    conn: Connection,
    name: Optional[str] = None,
    created_by: Optional[str] = None,
    focus_node: Optional[str] = None,
    workspace_uuid: Optional[uuid.UUID] = None,
) -> WorkspaceRecord:
    """Create a workspace pointing at ``dag_hash``.

    Generates a fresh UUID (unless ``workspace_uuid`` is supplied,
    which tests can use to pin a predictable URL), derives the
    slug, inserts the row, returns the record.

    Foreign-key safety
    ------------------
    ``workspaces.dag_hash`` is ``ON DELETE RESTRICT`` against
    ``dags.hash``.  Supplying a hash that does not exist in
    ``dags`` aborts the txn with an ``IntegrityError``.  Order
    the call chain so ``persist_dag_from_lineage`` runs first.
    """
    _validate_name(name)
    if workspace_uuid is None:
        workspace_uuid = uuid.uuid4()
    slug = derive_slug(name, workspace_uuid)

    row = conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.workspaces (
                id, slug, name, dag_hash, focus_node, created_by
            )
            VALUES (
                :id, :slug, :name, :dag_hash, :focus_node, :created_by
            )
            RETURNING id, slug, name, dag_hash, focus_node,
                      parent_workspace_id, schema_version, created_by,
                      created_at, updated_at
            """
        ),
        {
            "id": workspace_uuid,
            "slug": slug,
            "name": name,
            "dag_hash": dag_hash,
            "focus_node": focus_node,
            "created_by": created_by,
        },
    ).mappings().one()

    rec = _row_to_record(row)
    logger.info(
        "create_workspace: id=%s slug=%s dag=%s name=%r",
        rec.id, rec.slug, rec.dag_hash[:12], rec.name,
    )
    return rec


def get_workspace(
    workspace_id: uuid.UUID, *, conn: Connection,
) -> WorkspaceRecord:
    """Fetch a workspace by UUID.  Raises ``UnknownWorkspaceError``
    if the id does not exist."""
    row = conn.execute(
        text(
            f"""
            SELECT id, slug, name, dag_hash, focus_node,
                   parent_workspace_id, schema_version, created_by,
                   created_at, updated_at
            FROM {_COPILOT_STATE_SCHEMA}.workspaces
            WHERE id = :id
            """
        ),
        {"id": workspace_id},
    ).mappings().first()
    if row is None:
        raise UnknownWorkspaceError(
            f"No workspace with id {workspace_id!r}"
        )
    return _row_to_record(row)


def get_workspace_by_slug(
    slug: str, *, conn: Connection,
) -> WorkspaceRecord:
    """Fetch a workspace by slug.  This is the URL-resolution path.

    Raises ``UnknownWorkspaceError`` if no row matches.  Slug
    matching is case-sensitive because the column stores
    lowercased values exclusively — a request that comes in with
    mixed case simply won't match.
    """
    if not isinstance(slug, str) or not _SLUG_ALLOWED.fullmatch(slug):
        raise UnknownWorkspaceError(
            f"slug {slug!r} is not slug-shaped"
        )
    row = conn.execute(
        text(
            f"""
            SELECT id, slug, name, dag_hash, focus_node,
                   parent_workspace_id, schema_version, created_by,
                   created_at, updated_at
            FROM {_COPILOT_STATE_SCHEMA}.workspaces
            WHERE slug = :slug
            """
        ),
        {"slug": slug},
    ).mappings().first()
    if row is None:
        raise UnknownWorkspaceError(
            f"No workspace with slug {slug!r}"
        )
    return _row_to_record(row)


def rename_workspace(
    workspace_id: uuid.UUID,
    new_name: Optional[str],
    *,
    conn: Connection,
) -> WorkspaceRecord:
    """Update the display name; **slug is NOT changed**.

    This is the load-bearing URL-stability guarantee — once a user
    shares a workspace URL, renaming the workspace doesn't break
    the link.  The slug was rooted in the workspace's UUID at
    create time and persists for the workspace's lifetime.
    """
    _validate_name(new_name)
    row = conn.execute(
        text(
            f"""
            UPDATE {_COPILOT_STATE_SCHEMA}.workspaces
            SET name = :name, updated_at = now()
            WHERE id = :id
            RETURNING id, slug, name, dag_hash, focus_node,
                      parent_workspace_id, schema_version, created_by,
                      created_at, updated_at
            """
        ),
        {"id": workspace_id, "name": new_name},
    ).mappings().first()
    if row is None:
        raise UnknownWorkspaceError(
            f"No workspace with id {workspace_id!r}"
        )
    rec = _row_to_record(row)
    logger.info(
        "rename_workspace: id=%s slug=%s name=%r (slug preserved)",
        rec.id, rec.slug, rec.name,
    )
    return rec


def fork_workspace(
    parent_id: uuid.UUID,
    *,
    conn: Connection,
    variant_dag_hash: str,
    override_summary: Dict[str, Any],
    name: Optional[str] = None,
    created_by: Optional[str] = None,
    workspace_uuid: Optional[uuid.UUID] = None,
) -> WorkspaceRecord:
    """Clone a workspace as a child + insert a ``workspace_variants``
    row recording the override.

    Phase 0 ships ``fork_workspace`` but the UI does not call it yet
    (Phase 3 plugs in variant authoring).  The function is included
    here so the substrate is complete and tested.

    ``variant_dag_hash`` is the DAG the child workspace renders —
    typically a sibling of the parent's ``dag_hash`` with a few
    nodes' params overridden.  ``override_summary`` is the
    application-defined diff payload (shape evolves with Phase 3).
    """
    _validate_name(name)
    parent = get_workspace(parent_id, conn=conn)
    if workspace_uuid is None:
        workspace_uuid = uuid.uuid4()
    slug = derive_slug(name, workspace_uuid)

    # Insert the child workspace row.
    row = conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.workspaces (
                id, slug, name, dag_hash, parent_workspace_id,
                created_by
            )
            VALUES (
                :id, :slug, :name, :dag_hash, :parent_id, :created_by
            )
            RETURNING id, slug, name, dag_hash, focus_node,
                      parent_workspace_id, schema_version, created_by,
                      created_at, updated_at
            """
        ),
        {
            "id": workspace_uuid,
            "slug": slug,
            "name": name,
            "dag_hash": variant_dag_hash,
            "parent_id": parent.id,
            "created_by": created_by,
        },
    ).mappings().one()
    rec = _row_to_record(row)

    # Record the override summary in ``workspace_variants``.
    conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.workspace_variants (
                workspace_id, variant_dag_hash, override_summary
            )
            VALUES (
                :workspace_id, :variant_dag_hash,
                CAST(:override_summary AS JSONB)
            )
            """
        ),
        {
            "workspace_id": rec.id,
            "variant_dag_hash": variant_dag_hash,
            "override_summary": json.dumps(override_summary),
        },
    )
    logger.info(
        "fork_workspace: child=%s parent=%s variant_dag=%s",
        rec.id, parent_id, variant_dag_hash[:12],
    )
    return rec


# ============================================================================
# Internals
# ============================================================================


def _row_to_record(row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        dag_hash=row["dag_hash"],
        focus_node=row["focus_node"],
        parent_workspace_id=row["parent_workspace_id"],
        schema_version=int(row["schema_version"]),
        created_by=row["created_by"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


__all__ = [
    "WorkspaceRecord",
    "WorkspaceVariantRecord",
    "WorkspaceError",
    "InvalidNameError",
    "UnknownWorkspaceError",
    "slugify",
    "derive_slug",
    "create_workspace",
    "get_workspace",
    "get_workspace_by_slug",
    "rename_workspace",
    "fork_workspace",
]

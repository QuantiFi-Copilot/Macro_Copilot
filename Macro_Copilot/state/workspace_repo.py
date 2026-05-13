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
from typing import Any, Dict, List, Optional

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
    """One row from ``copilot_state.workspaces``.

    PR B extension: ``template_id`` + ``bound_slot_values`` carry the
    identifying surface needed to fork this workspace with parameter
    overrides.  Both are nullable to keep legacy (pre-PR-B) rows valid;
    when either is None the fork endpoint refuses with a clear
    diagnostic rather than guessing.
    """

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
    template_id: Optional[str] = None
    bound_slot_values: Optional[Dict[str, Any]] = None


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
    template_id: Optional[str] = None,
    bound_slot_values: Optional[Dict[str, Any]] = None,
    parent_workspace_id: Optional[uuid.UUID] = None,
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
    the call chain so the DAG is persisted first.

    PR B kwargs
    -----------
    ``template_id`` + ``bound_slot_values`` are the load-bearing
    inputs to the fork-with-overrides path.  Both default to None
    so existing callers (legacy + tests that don't care about
    forking) keep working.

    ``parent_workspace_id`` lets a fork stamp the parent linkage
    at create time without a second UPDATE — used by the new
    ``POST /workspace/{slug}/fork`` endpoint.
    """
    _validate_name(name)
    if workspace_uuid is None:
        workspace_uuid = uuid.uuid4()
    slug = derive_slug(name, workspace_uuid)

    row = conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.workspaces (
                id, slug, name, dag_hash, focus_node, created_by,
                template_id, bound_slot_values, parent_workspace_id
            )
            VALUES (
                :id, :slug, :name, :dag_hash, :focus_node, :created_by,
                :template_id,
                CAST(:bound_slot_values AS JSONB),
                :parent_workspace_id
            )
            RETURNING id, slug, name, dag_hash, focus_node,
                      parent_workspace_id, schema_version, created_by,
                      created_at, updated_at, template_id,
                      bound_slot_values
            """
        ),
        {
            "id": workspace_uuid,
            "slug": slug,
            "name": name,
            "dag_hash": dag_hash,
            "focus_node": focus_node,
            "created_by": created_by,
            "template_id": template_id,
            "bound_slot_values": (
                json.dumps(bound_slot_values)
                if bound_slot_values is not None
                else None
            ),
            "parent_workspace_id": parent_workspace_id,
        },
    ).mappings().one()

    rec = _row_to_record(row)
    logger.info(
        "create_workspace: id=%s slug=%s dag=%s name=%r template=%s",
        rec.id, rec.slug, rec.dag_hash[:12], rec.name,
        rec.template_id,
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
                   created_at, updated_at, template_id,
                   bound_slot_values
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
                   created_at, updated_at, template_id,
                   bound_slot_values
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
                      created_at, updated_at, template_id,
                      bound_slot_values
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
                created_by, template_id, bound_slot_values
            )
            VALUES (
                :id, :slug, :name, :dag_hash, :parent_id,
                :created_by, :template_id,
                CAST(:bound_slot_values AS JSONB)
            )
            RETURNING id, slug, name, dag_hash, focus_node,
                      parent_workspace_id, schema_version, created_by,
                      created_at, updated_at, template_id,
                      bound_slot_values
            """
        ),
        {
            "id": workspace_uuid,
            "slug": slug,
            "name": name,
            "dag_hash": variant_dag_hash,
            "parent_id": parent.id,
            "created_by": created_by,
            # PR B — children inherit the parent's template_id +
            # carry the patched bound_slot_values that produced the
            # variant DAG.  The caller (the fork endpoint) computes
            # those values and passes them via the override_summary's
            # ``new_slot_values`` key (kept in lockstep with the
            # endpoint to avoid silently dropping the metadata).
            "template_id": (
                override_summary.get("template_id")
                if isinstance(override_summary, dict)
                else None
            ) or parent.template_id,
            "bound_slot_values": (
                json.dumps(override_summary["new_slot_values"])
                if isinstance(override_summary, dict)
                and "new_slot_values" in override_summary
                else None
            ),
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
# List / sidebar surface
# ============================================================================


# Filter keys accepted by ``list_workspaces``.  ``recent`` is the
# default catalogue order (NULL-safe).  ``all`` returns every
# workspace ordered by ``updated_at``.  The remaining keys are
# accepted for forward-compat with sidebar UI sections (pinned,
# shared) but currently fall through to ``recent`` because the
# substrate doesn't yet track those flags — declared here so the
# caller can pre-bind the section semantics ahead of the
# columns landing.
_LIST_WORKSPACES_FILTERS = ("recent", "all", "pinned", "shared")


def list_workspaces(
    *,
    conn: Connection,
    limit: int = 25,
    offset: int = 0,
    filter: str = "recent",  # noqa: A002 - matches GET ?filter=...
    parent_workspace_id: Optional[uuid.UUID] = None,
) -> List[WorkspaceRecord]:
    """List workspaces for the sidebar / browse surface.

    Returns a window of ``WorkspaceRecord`` rows ordered by
    most-recent-activity.  Ordering is NULL-safe over
    ``last_accessed_at``: workspaces that have never been opened
    fall back to ``updated_at`` so a newly-persisted workflow
    surfaces immediately in the sidebar.

    Parameters
    ----------
    conn :
        Caller-owned transaction.  This is a read-only query but
        we keep the ``conn=`` convention for consistency with the
        rest of the module.
    limit :
        Page size.  Clamped to ``[1, 200]`` so a misconfigured
        client cannot fetch the whole table.
    offset :
        Page offset.  Negative values are clamped to zero.
    filter :
        Which list semantics to apply.  Today only ``recent`` and
        ``all`` differ in behaviour; the other keys are reserved
        for the sidebar's section taxonomy and fall through to
        ``recent`` ordering until the underlying columns land.

    Notes
    -----
    No JOIN against ``dag_nodes`` or ``artifact_metadata`` here —
    the sidebar reads a thin per-row summary only.  Per-node
    fetches happen via the existing ``GET /workspace/{slug}``
    endpoint when a user opens a workspace.
    """
    safe_limit = max(1, min(int(limit), 200))
    safe_offset = max(0, int(offset))
    if filter not in _LIST_WORKSPACES_FILTERS:
        # Defensive: an unknown filter is treated as ``recent``
        # rather than raising, so a future client sending a new
        # section name degrades gracefully on older servers.
        filter = "recent"

    # ``all`` orders purely by updated_at; ``recent`` and the
    # forward-compat keys use COALESCE so never-opened
    # workspaces still appear in chronological order.
    if filter == "all":
        order_clause = "updated_at DESC"
    else:
        order_clause = "COALESCE(last_accessed_at, updated_at) DESC"

    # PR B — optional parent filter powers the Build sidebar's
    # "Variants of this workspace" surface.  When None, all
    # workspaces are visible regardless of parent linkage.
    where_clause = ""
    params: Dict[str, Any] = {
        "limit": safe_limit,
        "offset": safe_offset,
    }
    if parent_workspace_id is not None:
        where_clause = "WHERE parent_workspace_id = :parent_workspace_id"
        params["parent_workspace_id"] = parent_workspace_id

    rows = conn.execute(
        text(
            f"""
            SELECT id, slug, name, dag_hash, focus_node,
                   parent_workspace_id, schema_version, created_by,
                   created_at, updated_at, template_id,
                   bound_slot_values
            FROM {_COPILOT_STATE_SCHEMA}.workspaces
            {where_clause}
            ORDER BY {order_clause}, id DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        params,
    ).mappings().all()

    return [_row_to_record(r) for r in rows]


def touch_workspace_access(
    slug: str, *, conn: Connection,
) -> None:
    """Stamp ``last_accessed_at = now()`` on the named workspace.

    Called by the ``GET /workspace/{slug}`` route handler so the
    sidebar's ``recent`` ordering reflects actual user activity
    rather than just creation time.  No-op if the slug does not
    exist (the route handler will surface that failure mode
    separately).

    The partial index ``ix_workspaces_last_accessed_active``
    (migration 0006) only indexes non-null rows, so writing this
    column hot-paths the sidebar's ORDER BY without scanning the
    table.
    """
    if not isinstance(slug, str) or not _SLUG_ALLOWED.fullmatch(slug):
        return
    conn.execute(
        text(
            f"""
            UPDATE {_COPILOT_STATE_SCHEMA}.workspaces
            SET last_accessed_at = now()
            WHERE slug = :slug
            """
        ),
        {"slug": slug},
    )


# ============================================================================
# Internals
# ============================================================================


def _row_to_record(row) -> WorkspaceRecord:
    # PR B — ``template_id`` + ``bound_slot_values`` are nullable
    # (added in migration 0009).  Older rows + tests that hand-craft
    # rows may not provide them; ``.get`` keeps the conversion
    # tolerant.
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
        template_id=row.get("template_id") if hasattr(row, "get") else (
            row["template_id"] if "template_id" in row else None
        ),
        bound_slot_values=(
            row.get("bound_slot_values") if hasattr(row, "get") else (
                row["bound_slot_values"]
                if "bound_slot_values" in row
                else None
            )
        ),
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
    "list_workspaces",
    "touch_workspace_access",
]

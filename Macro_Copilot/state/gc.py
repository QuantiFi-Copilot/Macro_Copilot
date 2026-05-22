"""state.gc — artifact garbage-collection sweep (Phase 0 PR 7).

Implemented but deliberately **not wired** to any scheduler in this
PR.  The Prefect worker service in ``docker-compose.yml`` is the
natural future host; Phase 4 (production deployment) enables it.

Why ship code that nothing invokes?  Two reasons:

  1. Tests are valuable now.  ``find_unreferenced_artifacts`` and
     ``purge_artifact`` have non-trivial correctness conditions
     (don't delete an artifact that any workspace / DAG / working_set
     still references; don't leave object-storage blobs orphaned;
     don't race against in-flight ``put_artifact`` calls).  Pinning
     those conditions in tests now is much cheaper than after a
     production incident.

  2. The schema is ready.  ``artifact_metadata`` has a ``created_at``
     column from PR 4; the FK constraints from PR 4 already enforce
     "you can't delete a referenced artifact" at the DB level.  This
     module just adds the "find candidates" + "execute safe delete"
     bits.

Two operations
--------------

  - ``find_unreferenced_artifacts(conn, older_than_days=30)``
      Returns hashes that:
        * have no FK references from ``dag_nodes.artifact_hash`` or
          ``working_set.artifact_hash``,
        * AND have ``created_at < NOW() - INTERVAL N DAY``.
      The age gate is the safety belt against deleting an artifact
      that was just created but hasn't yet been referenced by an
      in-flight transaction.

  - ``purge_artifact(hash, *, conn, object_storage)``
      Deletes the object-storage blob (if any) THEN the metadata
      row.  Order matters:
        * Object-storage delete first — even if the metadata-row
          delete then fails, the orphaned blob is benign (next
          sweep will retry).
        * Metadata-row delete second — the FK RESTRICT constraint
          provides a final safety check: if another transaction
          inserted a working_set reference between the
          ``find_unreferenced`` query and the delete, the FK refuses
          the delete and the artifact survives.

Orphan-blob sweep
-----------------
A future PR will add an orphan-blob sweep that walks the object
storage backend and removes blobs without matching metadata rows.
Not in PR 7 scope — orphans are benign (content-addressed; a re-put
of the same content produces the same URI) and the cost only matters
at scale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from state.object_storage import ObjectStorageBackend, _validate_hash

logger = logging.getLogger("state.gc")

_COPILOT_STATE_SCHEMA = "copilot_state"


def find_unreferenced_artifacts(
    conn: Connection,
    *,
    older_than_days: int = 30,
    limit: int = 1000,
) -> List[str]:
    """Return artifact hashes eligible for GC.

    An artifact is eligible when:

      1. No row in ``copilot_state.dag_nodes`` references its hash
         via ``dag_nodes.artifact_hash``.
      2. No row in ``copilot_state.working_set`` references its hash
         via ``working_set.artifact_hash`` (active OR retired —
         retired bindings keep their reference so historical turn
         resolution remains possible).
      3. ``artifact_metadata.created_at < NOW() - INTERVAL '<N>
         days'``.

    The query bounds the result with ``LIMIT`` so an over-eager
    sweeper (running on a very large DB) doesn't try to delete
    millions of artifacts in one transaction.  Callers should loop:

        while True:
            candidates = find_unreferenced_artifacts(conn)
            if not candidates:
                break
            for h in candidates:
                purge_artifact(h, conn=conn, object_storage=os)

    Parameters
    ----------
    older_than_days
        Skip artifacts created within the last N days.  Default 30
        is the safety belt against deleting artifacts of in-flight
        workspaces that haven't yet wired into a workspace_variants
        / working_set entry.  Lower this only with caution.
    limit
        Maximum candidates to return per call.
    """
    if older_than_days < 0:
        raise ValueError(
            f"older_than_days must be non-negative, got {older_than_days}"
        )
    if limit <= 0:
        raise ValueError(f"limit must be positive, got {limit}")

    stmt = text(
        f"""
        SELECT am.hash
        FROM {_COPILOT_STATE_SCHEMA}.artifact_metadata am
        WHERE am.created_at < NOW() - make_interval(days => :days)
          AND NOT EXISTS (
              SELECT 1 FROM {_COPILOT_STATE_SCHEMA}.dag_nodes dn
              WHERE dn.artifact_hash = am.hash
          )
          AND NOT EXISTS (
              SELECT 1 FROM {_COPILOT_STATE_SCHEMA}.working_set ws
              WHERE ws.artifact_hash = am.hash
          )
        ORDER BY am.created_at ASC
        LIMIT :lim
        """
    )
    rows = conn.execute(stmt, {"days": older_than_days, "lim": limit}).fetchall()
    return [r[0] for r in rows]


def purge_artifact(
    hash: str,
    *,
    conn: Connection,
    object_storage: ObjectStorageBackend,
) -> bool:
    """Delete an artifact's blob (if any) and metadata row.

    Returns True if the artifact was deleted, False if the metadata
    row was missing (already deleted by another sweeper).  Raises
    if the artifact still has FK references and the delete is
    refused by Postgres.

    Order of operations:
      1. SELECT payload_uri for the hash.  If row is missing, return
         False (caller should treat as already-purged).
      2. DELETE from object storage (idempotent; missing blob is a
         no-op).
      3. DELETE FROM artifact_metadata.  FK RESTRICT on dag_nodes
         and working_set provides the final safety check — if a
         concurrent transaction added a reference, the delete fails
         and the metadata + blob survive (blob is now orphaned but
         will be re-purged by the next sweep once the reference
         clears).
    """
    _validate_hash(hash)

    row = conn.execute(
        text(
            f"SELECT payload_uri FROM {_COPILOT_STATE_SCHEMA}.artifact_metadata "
            "WHERE hash = :hash"
        ),
        {"hash": hash},
    ).first()
    if row is None:
        logger.debug(
            "purge_artifact: hash %s... not found, nothing to do",
            hash[:12],
        )
        return False

    payload_uri = row[0]
    if payload_uri is not None:
        try:
            object_storage.delete(payload_uri)
        except Exception as exc:  # noqa: BLE001
            # We don't abort the metadata-row delete on an
            # object-storage failure — a leftover blob is benign;
            # leaving the metadata row intact while the blob is gone
            # would be worse (subsequent get_artifact would return
            # a 404 on payload fetch).  Log and continue.
            logger.warning(
                "purge_artifact: blob delete failed for %s (%s); "
                "continuing with metadata-row delete",
                payload_uri, exc,
            )

    conn.execute(
        text(
            f"DELETE FROM {_COPILOT_STATE_SCHEMA}.artifact_metadata "
            "WHERE hash = :hash"
        ),
        {"hash": hash},
    )
    logger.debug("purge_artifact: deleted hash=%s...", hash[:12])
    return True


__all__ = [
    "find_unreferenced_artifacts",
    "purge_artifact",
]

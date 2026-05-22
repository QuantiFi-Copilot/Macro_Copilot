"""state.working_set — per-session map of {name -> artifact_hash}.

Phase 0 PR 8.

The working set is the substrate the orchestrator uses to bind
short, user-friendly names ("tips_2y_v1", "turn_3_result") to
content-addressed artifacts.  Every chat turn either consumes
existing names (the user said "that series I just pulled") or
produces a new one (an auto-named ``turn_<n>_result`` plus any
explicit ``save as foo`` instruction the user gave).

Append-mostly semantics
-----------------------
The table is append-mostly.  A rebind (``add`` for a name that
already has an active entry) RETIRES the old row (sets
``retired_at_turn``) rather than deleting it.  Two reasons:

  1. **Historical resolution.**  A prior turn that referenced
     ``foo`` resolved against the artifact ``foo`` pointed at when
     that turn ran.  We must be able to recover that resolution
     later (e.g. when rebuilding a workspace from the DAG history),
     so the prior binding has to survive.

  2. **Foreign-key safety.**  ``working_set.artifact_hash`` is
     ``ON DELETE RESTRICT`` against ``artifact_metadata.hash``.
     Deleting a working-set row that's the only ref to an artifact
     does not let the artifact GC.  Retiring leaves the reference
     intact — GC walks ACTIVE references only when deciding what's
     reachable (see ``state.gc``).

Partial-unique-index gate
-------------------------
``uq_working_set_session_name_active`` is a partial unique index
on ``(session_id, name) WHERE retired_at_turn IS NULL``.  This means
at most one ACTIVE binding per (session, name).  The retirement
transition in ``add`` MUST atomically retire the old row and insert
the new one inside the same transaction — otherwise a concurrent
``add`` could violate the index.  The implementation uses
``engine.begin()`` semantics: the caller wraps both ops in the
same transaction.

Connection injection
--------------------
All public functions follow the ``*, conn`` convention from PR 3:

    with engine.begin() as conn:
        working_set.add("foo", artifact_hash, turn_id, conn=conn,
                        session_id=session_id)

The caller owns the transaction boundary.  Tests and the orchestrator
both wrap a turn's name commits in a single transaction so the
working-set and the turn's commit_turn rows land atomically.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger("state.working_set")


# ============================================================================
# Constants
# ============================================================================

_COPILOT_STATE_SCHEMA = "copilot_state"

# Working-set names are user-typed identifiers.  Restrict to a small,
# safe alphabet so we don't have to worry about SQL-quoting weirdness
# downstream (and so a user accidentally typing ``"; DROP TABLE`` does
# not even get past validation).  Length cap keeps Postgres TEXT rows
# tiny.
_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


# ============================================================================
# Public dataclass — what callers see
# ============================================================================


@dataclass(frozen=True)
class NamedArtifact:
    """A single working-set entry.  Frozen so callers can keep it in
    sets / use it as a dict key without surprise."""

    id: int
    session_id: uuid.UUID
    name: str
    artifact_hash: str
    introduced_at_turn: uuid.UUID
    retired_at_turn: Optional[uuid.UUID]

    @property
    def is_active(self) -> bool:
        return self.retired_at_turn is None


# ============================================================================
# Errors
# ============================================================================


class WorkingSetError(Exception):
    """Base class for working-set errors."""


class InvalidNameError(WorkingSetError):
    """Raised when a name fails ``_NAME_PATTERN`` validation."""


class UnknownNameError(WorkingSetError):
    """Raised by ``resolve`` / ``retire`` when no active binding exists."""


# ============================================================================
# Validation helpers
# ============================================================================


def _validate_name(name: str) -> None:
    if not isinstance(name, str):
        raise InvalidNameError(f"name must be str; got {type(name).__name__}")
    if not _NAME_PATTERN.fullmatch(name):
        raise InvalidNameError(
            f"Invalid working-set name {name!r}.  Names must match "
            f"{_NAME_PATTERN.pattern!r} (start with letter / underscore, "
            "ASCII letters / digits / underscore only, max 64 chars)."
        )


# ============================================================================
# Public API
# ============================================================================


def add(
    name: str,
    artifact_hash: str,
    introduced_at_turn: uuid.UUID,
    *,
    session_id: uuid.UUID,
    conn: Connection,
) -> NamedArtifact:
    """Bind ``name`` to ``artifact_hash`` in this session.

    If ``name`` is already actively bound in this session, the old
    binding is RETIRED (its ``retired_at_turn`` is set to
    ``introduced_at_turn``) and a new row is inserted.  Both happen
    inside the caller's transaction so the partial-unique gate
    ``uq_working_set_session_name_active`` is never violated.

    Returns the newly-inserted ``NamedArtifact``.

    Raises:
        InvalidNameError: ``name`` does not match the safe pattern.
    """
    _validate_name(name)

    # Retire any existing active binding (no-op if none exists).
    conn.execute(
        text(
            f"""
            UPDATE {_COPILOT_STATE_SCHEMA}.working_set
            SET retired_at_turn = :turn_id
            WHERE session_id = :session_id
              AND name = :name
              AND retired_at_turn IS NULL
            """
        ),
        {
            "session_id": session_id,
            "name": name,
            "turn_id": introduced_at_turn,
        },
    )

    row = conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.working_set (
                session_id, name, artifact_hash, introduced_at_turn,
                retired_at_turn
            )
            VALUES (:session_id, :name, :artifact_hash, :turn_id, NULL)
            RETURNING id, session_id, name, artifact_hash,
                      introduced_at_turn, retired_at_turn
            """
        ),
        {
            "session_id": session_id,
            "name": name,
            "artifact_hash": artifact_hash,
            "turn_id": introduced_at_turn,
        },
    ).mappings().one()

    return _row_to_named(row)


def retire(
    name: str,
    retired_at_turn: uuid.UUID,
    *,
    session_id: uuid.UUID,
    conn: Connection,
) -> NamedArtifact:
    """Retire the active binding for ``name`` in this session.

    Raises ``UnknownNameError`` if no active binding exists.
    """
    _validate_name(name)

    row = conn.execute(
        text(
            f"""
            UPDATE {_COPILOT_STATE_SCHEMA}.working_set
            SET retired_at_turn = :turn_id
            WHERE session_id = :session_id
              AND name = :name
              AND retired_at_turn IS NULL
            RETURNING id, session_id, name, artifact_hash,
                      introduced_at_turn, retired_at_turn
            """
        ),
        {
            "session_id": session_id,
            "name": name,
            "turn_id": retired_at_turn,
        },
    ).mappings().first()

    if row is None:
        raise UnknownNameError(
            f"No active binding for name {name!r} in session "
            f"{session_id}."
        )

    return _row_to_named(row)


def resolve(
    name: str,
    *,
    session_id: uuid.UUID,
    conn: Connection,
    as_of_turn: Optional[uuid.UUID] = None,
) -> NamedArtifact:
    """Resolve ``name`` to its bound artifact in this session.

    When ``as_of_turn`` is None (default), resolves to the ACTIVE
    binding — the row with ``retired_at_turn IS NULL``.

    When ``as_of_turn`` is provided, resolves to the binding that
    WAS active when the named turn ran.  Used to replay an older
    turn's references without being confused by subsequent rebinds.
    The historical resolution checks ``introduced_at_turn`` was
    committed at-or-before the as-of turn AND (``retired_at_turn``
    is null OR was retired strictly AFTER the as-of turn).  We
    compare via ``turns.sequence_no`` to avoid wall-clock skew.

    Raises ``UnknownNameError`` if no binding satisfies the lookup.
    """
    _validate_name(name)

    if as_of_turn is None:
        row = conn.execute(
            text(
                f"""
                SELECT id, session_id, name, artifact_hash,
                       introduced_at_turn, retired_at_turn
                FROM {_COPILOT_STATE_SCHEMA}.working_set
                WHERE session_id = :session_id
                  AND name = :name
                  AND retired_at_turn IS NULL
                """
            ),
            {"session_id": session_id, "name": name},
        ).mappings().first()
    else:
        # Historical resolution by turn sequence_no.  Subquery to
        # extract the sequence number of the as-of turn; then pick
        # the working_set row whose introduced_at_turn's sequence is
        # <= as-of AND whose retired_at_turn (if any) is > as-of.
        row = conn.execute(
            text(
                f"""
                WITH as_of AS (
                    SELECT sequence_no
                    FROM {_COPILOT_STATE_SCHEMA}.turns
                    WHERE id = :as_of_turn
                )
                SELECT ws.id, ws.session_id, ws.name, ws.artifact_hash,
                       ws.introduced_at_turn, ws.retired_at_turn
                FROM {_COPILOT_STATE_SCHEMA}.working_set ws
                JOIN {_COPILOT_STATE_SCHEMA}.turns t_intro
                    ON t_intro.id = ws.introduced_at_turn
                LEFT JOIN {_COPILOT_STATE_SCHEMA}.turns t_retire
                    ON t_retire.id = ws.retired_at_turn
                WHERE ws.session_id = :session_id
                  AND ws.name = :name
                  AND t_intro.sequence_no
                      <= (SELECT sequence_no FROM as_of)
                  AND (
                      t_retire.id IS NULL
                      OR t_retire.sequence_no
                          > (SELECT sequence_no FROM as_of)
                  )
                ORDER BY t_intro.sequence_no DESC
                LIMIT 1
                """
            ),
            {
                "session_id": session_id,
                "name": name,
                "as_of_turn": as_of_turn,
            },
        ).mappings().first()

    if row is None:
        raise UnknownNameError(
            f"No binding for name {name!r} in session {session_id}"
            + (f" as of turn {as_of_turn}" if as_of_turn else "")
        )

    return _row_to_named(row)


def list_visible(
    *,
    session_id: uuid.UUID,
    conn: Connection,
) -> List[NamedArtifact]:
    """Return the currently-ACTIVE bindings for this session.

    Ordered by introduction sequence (oldest first) so the LLM
    sees the natural conversational order when rendering the
    working-set block of its prompt.

    Used to populate the WORKING_SET_BLOCK_TEMPLATE that prefixes
    the supervisor / domain-child prompt at turn time.
    """
    rows = conn.execute(
        text(
            f"""
            SELECT ws.id, ws.session_id, ws.name, ws.artifact_hash,
                   ws.introduced_at_turn, ws.retired_at_turn
            FROM {_COPILOT_STATE_SCHEMA}.working_set ws
            JOIN {_COPILOT_STATE_SCHEMA}.turns t_intro
                ON t_intro.id = ws.introduced_at_turn
            WHERE ws.session_id = :session_id
              AND ws.retired_at_turn IS NULL
            ORDER BY t_intro.sequence_no ASC, ws.id ASC
            """
        ),
        {"session_id": session_id},
    ).mappings().all()

    return [_row_to_named(r) for r in rows]


# ============================================================================
# Internals
# ============================================================================


def _row_to_named(row) -> NamedArtifact:
    """Wrap a Postgres row mapping in a ``NamedArtifact`` dataclass."""
    return NamedArtifact(
        id=int(row["id"]),
        session_id=row["session_id"],
        name=row["name"],
        artifact_hash=row["artifact_hash"],
        introduced_at_turn=row["introduced_at_turn"],
        retired_at_turn=row["retired_at_turn"],
    )


__all__ = [
    "NamedArtifact",
    "WorkingSetError",
    "InvalidNameError",
    "UnknownNameError",
    "add",
    "retire",
    "resolve",
    "list_visible",
]

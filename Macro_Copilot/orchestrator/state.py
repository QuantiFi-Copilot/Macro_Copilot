"""orchestrator/state.py — turn lifecycle state machine.

Phase 0 PR 8.

Every chat turn now goes through an explicit two-step DB lifecycle:

  1. ``begin_turn(conn, session_id, user_message) -> TurnContext``
     Inserts a row in ``copilot_state.turns`` with status='running'
     and ``sequence_no`` = (max existing for this session) + 1.
     The supervisor / domain agents run AGAINST this turn id; any
     working-set writes they make reference it via
     ``introduced_at_turn`` / ``retired_at_turn``.

  2. ``commit_turn(conn, turn_ctx, ...)``
     Sets status='completed' (or 'failed' / 'cancelled'), records
     ``assistant_response``, optionally writes a working-set entry
     for the terminal artifact (auto-named ``turn_<n>_result`` or
     the user-supplied ``save_as``), and stamps ``completed_at``.

These two ops together turn the conversation into an audited,
replayable record:

  - Mid-flight crashes leave a status='failed' row, not silent
    data loss.  The next turn's ``sequence_no`` is still
    monotonic.
  - The working-set's ``introduced_at_turn`` / ``retired_at_turn``
    columns make every name binding traceable to the user message
    that caused it — and replayable via
    ``state.working_set.resolve(..., as_of_turn=...)``.

Connection / transaction discipline
------------------------------------
Both ops take ``conn`` keyword-only (PR 3 convention).  The caller
owns the transaction boundary.  Production usage wraps each op
in ``engine.begin()`` so the turn row is atomically committed
(in begin_turn) and the commit_turn updates are atomic with any
working-set rows produced this turn.

Sequence-no race safety
-----------------------
``begin_turn`` computes ``sequence_no = max(...) + 1`` under the
implicit table lock provided by the INSERT itself.  Concurrent
``begin_turn`` calls for the SAME session are serialised by the
``uq_turns_session_sequence`` unique constraint: the second insert
fails with IntegrityError and the caller retries (or, more
realistically, can't happen because per-session WebSocket
serialises turns at the application layer).  We add an explicit
``FOR UPDATE`` lock on the sessions row in ``begin_turn`` to
serialise the read-then-write across concurrent transactions.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from sqlalchemy import text
from sqlalchemy.engine import Connection

from state import working_set as ws_module

logger = logging.getLogger("orchestrator.state")


# ============================================================================
# Constants
# ============================================================================

_COPILOT_STATE_SCHEMA = "copilot_state"

# Allowed terminal statuses for ``commit_turn``.  Mirrors the
# ``ck_turns_status`` CHECK constraint in migration 0001.
TurnStatus = Literal["completed", "failed", "cancelled"]


# ============================================================================
# Public dataclass — what callers see
# ============================================================================


@dataclass(frozen=True)
class TurnContext:
    """The handle ``begin_turn`` returns and ``commit_turn`` consumes.

    Carries the turn's id, sequence_no, session_id, started_at, and
    the user_message verbatim.  Frozen so the orchestrator cannot
    mutate the canonical record between begin and commit.
    """

    turn_id: uuid.UUID
    session_id: uuid.UUID
    sequence_no: int
    user_message: str
    started_at: datetime


# ============================================================================
# Public API
# ============================================================================


def create_session_if_needed(
    session_id: uuid.UUID,
    *,
    conn: Connection,
    user_id: Optional[str] = None,
    label: Optional[str] = None,
) -> None:
    """Ensure ``session_id`` has a row in ``copilot_state.sessions``.

    Idempotent: a second call with the same id is a no-op.  The
    INSERT uses ``ON CONFLICT (id) DO NOTHING`` so two concurrent
    callers don't race.

    Called from ``api/routes/chat.py`` when a WebSocket connects.
    Tests and CLI callers that don't go through the WebSocket can
    call this directly before their first ``begin_turn``.
    """
    conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.sessions (id, user_id, label)
            VALUES (:id, :user_id, :label)
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {"id": session_id, "user_id": user_id, "label": label},
    )


def begin_turn(
    user_message: str,
    *,
    session_id: uuid.UUID,
    conn: Connection,
) -> TurnContext:
    """Open a new turn for ``session_id``.

    Inserts a row in ``copilot_state.turns`` with status='running'
    and sequence_no = (max sequence_no for this session) + 1.  An
    explicit ``FOR UPDATE`` on the sessions row serialises concurrent
    begin_turn calls for the same session, even though the unique
    constraint on (session_id, sequence_no) would catch the race
    too — the explicit lock keeps the error path off the happy path.

    The caller's transaction is committed by ``engine.begin()`` /
    ``conn.commit()``; ``begin_turn`` itself does NOT commit.
    """
    if not user_message or not user_message.strip():
        raise ValueError("user_message must be non-empty")

    # Take a row lock on the session so concurrent begin_turns
    # serialise.  If the session doesn't exist, raise loudly — the
    # caller forgot ``create_session_if_needed``.
    sess_row = conn.execute(
        text(
            f"""
            SELECT id FROM {_COPILOT_STATE_SCHEMA}.sessions
            WHERE id = :id
            FOR UPDATE
            """
        ),
        {"id": session_id},
    ).first()
    if sess_row is None:
        raise RuntimeError(
            f"Session {session_id} does not exist; call "
            "create_session_if_needed first."
        )

    next_seq_row = conn.execute(
        text(
            f"""
            SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_seq
            FROM {_COPILOT_STATE_SCHEMA}.turns
            WHERE session_id = :session_id
            """
        ),
        {"session_id": session_id},
    ).mappings().one()
    next_seq = int(next_seq_row["next_seq"])

    row = conn.execute(
        text(
            f"""
            INSERT INTO {_COPILOT_STATE_SCHEMA}.turns (
                session_id, sequence_no, user_message, status
            )
            VALUES (:session_id, :sequence_no, :user_message, 'running')
            RETURNING id, started_at
            """
        ),
        {
            "session_id": session_id,
            "sequence_no": next_seq,
            "user_message": user_message,
        },
    ).mappings().one()

    ctx = TurnContext(
        turn_id=row["id"],
        session_id=session_id,
        sequence_no=next_seq,
        user_message=user_message,
        started_at=row["started_at"],
    )
    logger.info(
        "begin_turn: session=%s turn=%s seq=%d",
        session_id, ctx.turn_id, next_seq,
    )
    return ctx


def commit_turn(
    turn_ctx: TurnContext,
    *,
    conn: Connection,
    status: TurnStatus = "completed",
    assistant_response: Optional[str] = None,
    terminal_artifact_hash: Optional[str] = None,
    save_as: Optional[str] = None,
) -> Optional[str]:
    """Finalize ``turn_ctx``.

    - Sets ``status`` (completed / failed / cancelled).
    - Stamps ``completed_at = now()``.
    - Records ``assistant_response`` if provided.

    When ``status == "completed"`` AND ``terminal_artifact_hash`` is
    set, this also writes a working-set entry binding the auto-name
    ``turn_<sequence_no>_result`` to the terminal artifact.  When
    ``save_as`` is additionally provided, a SECOND working-set
    entry binds the user-supplied name to the same hash.  Two
    bindings to one artifact is allowed by the partial-unique gate
    (the constraint is per-NAME, not per-artifact_hash) and is the
    natural way to encode "the user said 'save as foo'" — the
    auto-name plus the explicit alias both resolve to the same
    artifact.

    Returns the auto-name string that was bound (e.g.
    ``"turn_3_result"``), or None if no artifact was provided.

    Failure cases:
        - Terminal artifact hash is set but the artifact doesn't
          exist in artifact_metadata: the INSERT fails with a
          foreign-key violation from
          ``fk_working_set_artifact``.  The caller's transaction
          will roll back, leaving the turn unfinalised.  Fix the
          ordering at the call site (put_artifact BEFORE commit_turn)
          rather than swallowing the FK error here.
    """
    if status not in ("completed", "failed", "cancelled"):
        raise ValueError(
            f"status must be one of completed/failed/cancelled; got {status!r}"
        )

    conn.execute(
        text(
            f"""
            UPDATE {_COPILOT_STATE_SCHEMA}.turns
            SET status = :status,
                assistant_response = :assistant_response,
                completed_at = :completed_at
            WHERE id = :id
            """
        ),
        {
            "status": status,
            "assistant_response": assistant_response,
            "completed_at": datetime.now(timezone.utc),
            "id": turn_ctx.turn_id,
        },
    )

    if status == "completed" and terminal_artifact_hash:
        auto_name = f"turn_{turn_ctx.sequence_no}_result"
        ws_module.add(
            auto_name,
            terminal_artifact_hash,
            turn_ctx.turn_id,
            session_id=turn_ctx.session_id,
            conn=conn,
        )
        if save_as:
            ws_module.add(
                save_as,
                terminal_artifact_hash,
                turn_ctx.turn_id,
                session_id=turn_ctx.session_id,
                conn=conn,
            )
        logger.info(
            "commit_turn: session=%s turn=%s seq=%d status=%s "
            "auto_name=%s save_as=%s artifact=%s...",
            turn_ctx.session_id, turn_ctx.turn_id, turn_ctx.sequence_no,
            status, auto_name, save_as,
            terminal_artifact_hash[:12],
        )
        return auto_name

    logger.info(
        "commit_turn: session=%s turn=%s seq=%d status=%s (no artifact)",
        turn_ctx.session_id, turn_ctx.turn_id, turn_ctx.sequence_no,
        status,
    )
    return None


def fail_turn(
    turn_ctx: TurnContext,
    *,
    conn: Connection,
    error_message: Optional[str] = None,
) -> None:
    """Convenience wrapper: mark a turn as failed.

    ``error_message`` is recorded in ``assistant_response`` so the
    next-turn prompt-builder can surface it.  Use this from any
    error-handling branch in the orchestrator pipeline; it does NOT
    write a working-set entry (failed turns produce no terminal
    artifact by definition).
    """
    commit_turn(
        turn_ctx,
        conn=conn,
        status="failed",
        assistant_response=error_message,
        terminal_artifact_hash=None,
        save_as=None,
    )


# ============================================================================
# Recent-conversation context (PR 13)
# ============================================================================
# Phase 0 built the per-turn persistence layer (sessions / turns /
# message_events / working_set + AsyncPostgresSaver for domain
# children).  But the *routing* layer — Supervisor.route() and the
# WorkflowRouter pre-gate — only ever saw the current user message,
# with no prior context.  A user asking "what about the Bund one?"
# after a "UST 2s10s" turn fell through to a CLARIFY response
# because the supervisor had no idea what "the one" referred to.
#
# This module-level helper closes that gap: ``load_recent_turns``
# reads the most recent N completed (and failed, see contract) turns
# from ``copilot_state.turns`` and returns them in chronological
# order for prompt injection.  ``orchestrator/session.py`` calls
# it once at the top of every ``_run_turn`` and prepends a
# ``RECENT CONVERSATION`` block to the augmented user message — the
# supervisor + workflow router + every domain child all see the
# prior-turn context naturally, without bespoke history-passing
# code at each call site.


@dataclass(frozen=True)
class RecentTurn:
    """One past turn surfaced to the next turn's routing layer.

    Only the user-visible fields land here — internal tool traces
    and event streams stay in ``copilot_state.message_events``
    (queryable for audit) but do not feed the next turn's prompt.

    ``assistant_response`` may be None for turns that never reached
    a commit_turn (in-flight turns).  ``load_recent_turns`` filters
    those out by default; callers that want them can opt in via
    ``include_in_flight``.
    """

    sequence_no: int
    user_message: str
    assistant_response: Optional[str]
    status: str


# Caps on what we surface back as "recent conversation" — keeps
# token cost predictable and avoids leaking arbitrarily long
# assistant transcripts into every subsequent turn's system
# prompt.  The contract is: this is human-readable signal to help
# routing, not a faithful conversation replay (that lives in
# ``message_events`` for audit).
_DEFAULT_TURN_LIMIT = 5
_MAX_ASSISTANT_RESPONSE_CHARS = 600


def load_recent_turns(
    session_id: uuid.UUID,
    *,
    conn: Connection,
    exclude_turn_id: Optional[uuid.UUID] = None,
    limit: int = _DEFAULT_TURN_LIMIT,
    include_in_flight: bool = False,
) -> list[RecentTurn]:
    """Load the most recent N turns for ``session_id``.

    Returns turns in chronological order (oldest first) so the
    prompt block reads top-to-bottom like a conversation transcript.

    Parameters
    ----------
    session_id :
        The session whose turns to fetch.
    conn :
        SQLAlchemy connection (PR 3 convention).  Caller owns
        transaction boundary; this is a SELECT-only operation.
    exclude_turn_id :
        Typical: the CURRENT turn's id (just opened by
        ``begin_turn``).  Excluding it prevents the in-flight
        user message from appearing in its own "recent" context.
    limit :
        Cap on the number of turns to return.  Default 5 — enough
        to carry one or two follow-up references without blowing
        out token budget.  The caller can request more for an
        operator-facing replay surface that needs deeper history.
    include_in_flight :
        When False (default), turns with ``status='running'`` are
        excluded.  Routing should not see partially-formed
        assistant responses.  When True, all rows are returned
        including ``running`` turns — used by tests verifying the
        filter behaviour.

    Returns
    -------
    list[RecentTurn]
        Ordered oldest → newest.  Empty when the session has no
        qualifying prior turns.
    """
    if limit < 1:
        return []

    # SQL pulls the LATEST ``limit`` rows by sequence_no descending,
    # then we reverse to chronological order at the Python layer.
    # An in-place SQL ``ORDER BY sequence_no ASC LIMIT N OFFSET ...``
    # would require knowing the total count first; the
    # DESC-LIMIT-then-reverse pattern is one round-trip.
    status_clause = "" if include_in_flight else (
        "AND status IN ('completed', 'failed', 'cancelled')"
    )
    exclude_clause = (
        "AND id <> :exclude_turn_id" if exclude_turn_id is not None else ""
    )
    params: dict = {"session_id": session_id, "limit": int(limit)}
    if exclude_turn_id is not None:
        params["exclude_turn_id"] = exclude_turn_id

    rows = conn.execute(
        text(
            f"""
            SELECT sequence_no, user_message, assistant_response, status
            FROM {_COPILOT_STATE_SCHEMA}.turns
            WHERE session_id = :session_id
              {status_clause}
              {exclude_clause}
            ORDER BY sequence_no DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()

    out = [
        RecentTurn(
            sequence_no=int(r["sequence_no"]),
            user_message=r["user_message"],
            assistant_response=_truncate_response(r["assistant_response"]),
            status=r["status"],
        )
        for r in rows
    ]
    # Reverse to chronological order.
    out.reverse()
    return out


def _truncate_response(value: Optional[str]) -> Optional[str]:
    """Cap an assistant_response at ``_MAX_ASSISTANT_RESPONSE_CHARS``.

    Long assistant responses get an ellipsis suffix so the next
    turn's routing prompt stays bounded.  Reading the full
    response is an audit-time concern; routing only needs the gist.
    """
    if value is None:
        return None
    if len(value) <= _MAX_ASSISTANT_RESPONSE_CHARS:
        return value
    return value[: _MAX_ASSISTANT_RESPONSE_CHARS - 1] + "…"


__all__ = [
    "TurnContext",
    "TurnStatus",
    "create_session_if_needed",
    "begin_turn",
    "commit_turn",
    "fail_turn",
    # PR 13 — recent-conversation context for routing layer.
    "RecentTurn",
    "load_recent_turns",
]

"""tests/state/test_recent_conversation_context.py — PR 13.

Closes the Phase-0 routing-memory gap: the supervisor + workflow
router used to see only the current user message with no prior
context, so follow-ups like "what about the Bund one?" hit
CLARIFY because nothing told the router what "the one" referred to.

PR 13 reads the last N completed turns from
``copilot_state.turns`` and prepends a RECENT CONVERSATION block
to the user_message before routing.  This file exercises:

  - ``load_recent_turns`` SQL contract (ordering, filtering, limit,
    exclude_turn_id, in-flight inclusion toggle).
  - ``render_recent_conversation_block`` + ``render_routing_prefix``
    rendering (empty / non-empty / multi-block composition).
  - End-to-end: a 3-turn session, the third turn's augmented
    message contains the prior two turns' user_message AND
    assistant_response.

Module-level skip when Postgres is unreachable; CI's state-layer
job provides the postgres:14 service container.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from orchestrator.prompts import (  # noqa: E402
    render_recent_conversation_block,
    render_routing_prefix,
    render_working_set_block,
)
from orchestrator.state import (  # noqa: E402
    RecentTurn,
    begin_turn,
    commit_turn,
    create_session_if_needed,
    load_recent_turns,
)


# ============================================================================
# DB availability
# ============================================================================


def _build_url() -> str:
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def _db_reachable(url: str) -> bool:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


_DB_URL = _build_url()
_DB_AVAILABLE = _db_reachable(_DB_URL)

pytestmark = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=(
        f"Postgres not reachable at {_DB_URL!r}.  CI's state-layer job "
        "provides the postgres:14 service container."
    ),
)


# ============================================================================
# Pure-render tests (no DB)
# ============================================================================


class TestRenderRecentConversationBlock:
    def test_empty_turns_renders_empty_string(self):
        assert render_recent_conversation_block([]) == ""

    def test_basic_two_turn_render(self):
        turns = [
            RecentTurn(
                sequence_no=1,
                user_message="What is UST 2s10s",
                assistant_response="50.4 bps",
                status="completed",
            ),
            RecentTurn(
                sequence_no=2,
                user_message="extend to 5y",
                assistant_response="Z-score -1.2",
                status="completed",
            ),
        ]
        block = render_recent_conversation_block(turns)
        # Both user messages + both responses surface.
        assert "What is UST 2s10s" in block
        assert "50.4 bps" in block
        assert "extend to 5y" in block
        assert "Z-score -1.2" in block
        # Header + guidance language survives.
        assert "RECENT CONVERSATION" in block
        assert "follow-up references" in block

    def test_failed_turn_carries_status_tag(self):
        turns = [
            RecentTurn(
                sequence_no=1,
                user_message="broken request",
                assistant_response="error: tool unavailable",
                status="failed",
            ),
        ]
        block = render_recent_conversation_block(turns)
        assert "[failed]" in block

    def test_no_response_with_non_completed_status_shows_placeholder(self):
        turns = [
            RecentTurn(
                sequence_no=1,
                user_message="cancelled mid-flight",
                assistant_response=None,
                status="cancelled",
            ),
        ]
        block = render_recent_conversation_block(turns)
        assert "[cancelled]" in block
        assert "(no response captured)" in block

    def test_completed_turn_with_no_response_does_not_surface_placeholder(self):
        """A completed turn whose assistant_response is None
        (e.g. a CLARIFY turn) renders the user_message line only
        — no fake "no response captured" tag."""
        turns = [
            RecentTurn(
                sequence_no=1,
                user_message="ambiguous request",
                assistant_response=None,
                status="completed",
            ),
        ]
        block = render_recent_conversation_block(turns)
        assert "ambiguous request" in block
        assert "no response captured" not in block


class TestRenderRoutingPrefix:
    def test_empty_both_collapses_to_empty(self):
        assert render_routing_prefix([], []) == ""

    def test_only_working_set_no_recent(self):
        prefix = render_routing_prefix([], ["tips_2y_v1"])
        assert "RECENT CONVERSATION" not in prefix
        assert "CURRENT WORKING SET" in prefix
        assert "tips_2y_v1" in prefix

    def test_only_recent_no_working_set(self):
        turns = [
            RecentTurn(1, "q", "a", "completed"),
        ]
        prefix = render_routing_prefix(turns, [])
        assert "RECENT CONVERSATION" in prefix
        assert "CURRENT WORKING SET" not in prefix

    def test_recent_appears_before_working_set(self):
        """Order is load-bearing for prompt readability:
        recent conversation (broader signal) first, working set
        (specific named handles) second."""
        turns = [RecentTurn(1, "q", "a", "completed")]
        prefix = render_routing_prefix(turns, ["alpha"])
        idx_recent = prefix.index("RECENT CONVERSATION")
        idx_ws = prefix.index("CURRENT WORKING SET")
        assert idx_recent < idx_ws


# ============================================================================
# DB-backed load_recent_turns tests
# ============================================================================


@pytest.fixture
def engine():
    from sqlalchemy import create_engine

    e = create_engine(_DB_URL)
    yield e
    e.dispose()


@pytest.fixture(autouse=True)
def wipe(engine):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.message_events"))
        conn.execute(text("DELETE FROM copilot_state.turns"))
        conn.execute(text("DELETE FROM copilot_state.sessions"))
    yield


@pytest.fixture
def session_id(engine):
    sid = uuid.uuid4()
    with engine.begin() as conn:
        create_session_if_needed(sid, conn=conn, user_id="tester")
    return sid


def _complete_turn(engine, session_id, user_msg, assistant_resp):
    """Open + commit one turn with a user message and an assistant
    response.  Returns the ``TurnContext`` for the closed turn."""
    with engine.begin() as conn:
        ctx = begin_turn(user_msg, session_id=session_id, conn=conn)
    with engine.begin() as conn:
        commit_turn(
            ctx,
            conn=conn,
            status="completed",
            assistant_response=assistant_resp,
        )
    return ctx


class TestLoadRecentTurnsBasics:
    def test_empty_session_returns_empty(self, engine, session_id):
        with engine.connect() as conn:
            turns = load_recent_turns(session_id, conn=conn)
        assert turns == []

    def test_single_turn_returned_in_chronological_order(
        self, engine, session_id,
    ):
        _complete_turn(engine, session_id, "hi", "hello")
        with engine.connect() as conn:
            turns = load_recent_turns(session_id, conn=conn)
        assert len(turns) == 1
        assert turns[0].user_message == "hi"
        assert turns[0].assistant_response == "hello"
        assert turns[0].status == "completed"
        assert turns[0].sequence_no == 1

    def test_multi_turn_order_oldest_first(self, engine, session_id):
        _complete_turn(engine, session_id, "turn 1", "ans 1")
        _complete_turn(engine, session_id, "turn 2", "ans 2")
        _complete_turn(engine, session_id, "turn 3", "ans 3")
        with engine.connect() as conn:
            turns = load_recent_turns(session_id, conn=conn)
        seqs = [t.sequence_no for t in turns]
        assert seqs == [1, 2, 3]
        assert turns[0].user_message == "turn 1"
        assert turns[-1].user_message == "turn 3"


class TestLoadRecentTurnsFiltering:
    def test_limit_caps_returned_count(self, engine, session_id):
        for i in range(1, 8):
            _complete_turn(engine, session_id, f"turn {i}", f"ans {i}")
        with engine.connect() as conn:
            turns = load_recent_turns(session_id, conn=conn, limit=3)
        # Most recent 3, in chronological order.
        assert [t.sequence_no for t in turns] == [5, 6, 7]

    def test_zero_or_negative_limit_returns_empty(self, engine, session_id):
        _complete_turn(engine, session_id, "x", "y")
        with engine.connect() as conn:
            assert load_recent_turns(session_id, conn=conn, limit=0) == []
            assert load_recent_turns(session_id, conn=conn, limit=-5) == []

    def test_exclude_turn_id_filters_target_row(self, engine, session_id):
        ctx_a = _complete_turn(engine, session_id, "a", "1")
        _complete_turn(engine, session_id, "b", "2")
        with engine.connect() as conn:
            turns = load_recent_turns(
                session_id, conn=conn, exclude_turn_id=ctx_a.turn_id,
            )
        assert [t.user_message for t in turns] == ["b"]

    def test_in_flight_turn_excluded_by_default(self, engine, session_id):
        # Open a turn without committing.
        with engine.begin() as conn:
            begin_turn("in flight", session_id=session_id, conn=conn)
        # Another committed turn alongside.
        _complete_turn(engine, session_id, "done", "ok")
        with engine.connect() as conn:
            turns = load_recent_turns(session_id, conn=conn)
        msgs = [t.user_message for t in turns]
        assert "in flight" not in msgs
        assert "done" in msgs

    def test_in_flight_turn_included_when_opted_in(self, engine, session_id):
        with engine.begin() as conn:
            begin_turn("in flight", session_id=session_id, conn=conn)
        with engine.connect() as conn:
            turns = load_recent_turns(
                session_id, conn=conn, include_in_flight=True,
            )
        assert any(t.user_message == "in flight" for t in turns)


class TestLoadRecentTurnsTruncation:
    def test_long_assistant_response_truncated(self, engine, session_id):
        long_response = "x" * 800
        _complete_turn(engine, session_id, "hi", long_response)
        with engine.connect() as conn:
            turns = load_recent_turns(session_id, conn=conn)
        # Truncated at the documented cap (600 chars, with ellipsis).
        assert turns[0].assistant_response is not None
        assert len(turns[0].assistant_response) <= 600
        assert turns[0].assistant_response.endswith("…")


class TestLoadRecentTurnsSessionIsolation:
    def test_other_sessions_invisible(self, engine, session_id):
        # Build another session and add turns to it.
        other = uuid.uuid4()
        with engine.begin() as conn:
            create_session_if_needed(other, conn=conn)
        _complete_turn(engine, other, "other turn", "other ans")
        _complete_turn(engine, session_id, "mine", "ans")
        with engine.connect() as conn:
            turns = load_recent_turns(session_id, conn=conn)
        msgs = [t.user_message for t in turns]
        assert msgs == ["mine"]


# ============================================================================
# Integration: the screenshot bug
# ============================================================================


class TestScreenshotBugRegression:
    """The user's screenshot showed:

        User: "What is the latest UST 2s10s spread"
        Assistant: "The UST 2s10s spread is currently 50.4 bps..."
        User: "what about the Bund one"
        Assistant: "What analysis do you want for Bund? Event
                    study, regime analysis, or something else?"

    The follow-up failed because the supervisor / workflow router
    had NO context about turn 1.  PR 13's fix prepends a recent-
    conversation block to the user_message before routing.

    This test reconstructs the exact scenario at the DATA layer:
    after turn 1 commits, ``load_recent_turns`` returns turn 1's
    user_message AND assistant_response, and the rendered block
    contains both — so a supervisor that sees ``augmented_message``
    HAS the context to resolve "the Bund one".
    """

    def test_followup_turn_sees_prior_turn(self, engine, session_id):
        _complete_turn(
            engine, session_id,
            "What is the latest UST 2s10s spread",
            "The UST 2s10s spread is currently 50.4 bps...",
        )
        # Open turn 2 (the follow-up).
        with engine.begin() as conn:
            t2 = begin_turn(
                "what about the Bund one",
                session_id=session_id, conn=conn,
            )
        # Routing layer call: load recent turns, excluding the
        # in-flight turn (turn 2 itself).
        with engine.connect() as conn:
            recent = load_recent_turns(
                session_id, conn=conn, exclude_turn_id=t2.turn_id,
            )
        assert len(recent) == 1
        assert recent[0].user_message == (
            "What is the latest UST 2s10s spread"
        )
        assert "50.4 bps" in recent[0].assistant_response

        # And the rendered block carries both for the LLM to read.
        block = render_recent_conversation_block(recent)
        assert "What is the latest UST 2s10s spread" in block
        assert "50.4 bps" in block
        assert "RECENT CONVERSATION" in block

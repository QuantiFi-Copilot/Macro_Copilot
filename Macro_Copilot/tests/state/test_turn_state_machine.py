"""tests/state/test_turn_state_machine.py — real-Postgres integration
tests for ``orchestrator/state.py``.

Phase 0 PR 8.

Tests cover:

  - ``create_session_if_needed`` is idempotent.
  - ``begin_turn`` inserts a 'running' row with monotonic sequence_no
    even when interleaved with other sessions.
  - ``commit_turn`` flips status, records assistant_response, and
    when terminal_artifact_hash + save_as are set writes BOTH the
    auto-named binding and the user alias.
  - ``commit_turn`` honours the closed-status set
    (completed / failed / cancelled).
  - ``fail_turn`` is a convenience wrapper for the failed status.
  - Terminal artifact must exist in artifact_metadata; FK violation
    aborts the commit transaction.

Module-level skip when Postgres is unreachable.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pandas as pd
import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from shared.artifacts.lineage import FetchStep, Lineage  # noqa: E402
from shared.artifacts.missingness import RawNoCleaning  # noqa: E402
from shared.artifacts.types import Series  # noqa: E402
from shared.artifacts.units import TimeSeriesUnits  # noqa: E402
from orchestrator.state import (  # noqa: E402
    begin_turn,
    commit_turn,
    create_session_if_needed,
    fail_turn,
)
from state import working_set as ws_module  # noqa: E402
from state.artifact_store import put_artifact  # noqa: E402
from state.object_storage import LocalFSBackend  # noqa: E402


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
        f"Postgres not reachable at {_DB_URL!r}.  Set DB_* env vars or run "
        "the CI state-layer job (which provides a postgres:14 service)."
    ),
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine():
    from sqlalchemy import create_engine

    e = create_engine(_DB_URL)
    yield e
    e.dispose()


@pytest.fixture(autouse=True)
def wipe_state_tables(engine):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
        conn.execute(text("DELETE FROM copilot_state.message_events"))
        conn.execute(text("DELETE FROM copilot_state.turns"))
        conn.execute(text("DELETE FROM copilot_state.sessions"))
        conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
    yield


@pytest.fixture
def storage(tmp_path):
    return LocalFSBackend(root=tmp_path / "artifacts")


@pytest.fixture
def session_id(engine) -> uuid.UUID:
    sid = uuid.uuid4()
    with engine.begin() as conn:
        create_session_if_needed(sid, conn=conn, user_id="tester")
    return sid


@pytest.fixture
def artifact_hash(engine, storage) -> str:
    step = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y", "slot": 1},
    )
    art = Series(
        series_key="UST.10Y.yield_mid",
        payload=pd.Series(
            [4.1], index=pd.date_range("2024-01-01", periods=1)
        ),
        units=TimeSeriesUnits.PERCENT,
        frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([step]),
    )
    with engine.begin() as conn:
        return put_artifact(art, conn=conn, object_storage=storage)


# ============================================================================
# create_session_if_needed
# ============================================================================


class TestCreateSession:
    def test_creates_row(self, engine):
        sid = uuid.uuid4()
        with engine.begin() as conn:
            create_session_if_needed(sid, conn=conn)
        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id FROM copilot_state.sessions WHERE id = :id"
                ),
                {"id": sid},
            ).first()
        assert row is not None

    def test_idempotent(self, engine):
        sid = uuid.uuid4()
        with engine.begin() as conn:
            create_session_if_needed(sid, conn=conn, label="first")
        with engine.begin() as conn:
            create_session_if_needed(sid, conn=conn, label="second")

        from sqlalchemy import text

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT label FROM copilot_state.sessions WHERE id = :id"
                ),
                {"id": sid},
            ).all()
        # ON CONFLICT DO NOTHING: only one row, label is whichever was
        # written first (first call's value).
        assert len(rows) == 1
        assert rows[0][0] == "first"


# ============================================================================
# begin_turn
# ============================================================================


class TestBeginTurn:
    def test_opens_running_turn(self, engine, session_id):
        with engine.begin() as conn:
            ctx = begin_turn(
                "hello world", session_id=session_id, conn=conn,
            )
        assert ctx.user_message == "hello world"
        assert ctx.sequence_no == 1
        assert ctx.session_id == session_id

        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, user_message, sequence_no "
                    "FROM copilot_state.turns WHERE id = :id"
                ),
                {"id": ctx.turn_id},
            ).mappings().one()
        assert row["status"] == "running"
        assert row["user_message"] == "hello world"
        assert row["sequence_no"] == 1

    def test_monotonic_sequence_no(self, engine, session_id):
        with engine.begin() as conn:
            c1 = begin_turn("one", session_id=session_id, conn=conn)
        with engine.begin() as conn:
            c2 = begin_turn("two", session_id=session_id, conn=conn)
        with engine.begin() as conn:
            c3 = begin_turn("three", session_id=session_id, conn=conn)
        assert [c1.sequence_no, c2.sequence_no, c3.sequence_no] == [1, 2, 3]

    def test_unknown_session_raises(self, engine):
        rogue_id = uuid.uuid4()
        with engine.begin() as conn:
            with pytest.raises(RuntimeError):
                begin_turn("hi", session_id=rogue_id, conn=conn)

    def test_empty_message_raises(self, engine, session_id):
        with engine.begin() as conn:
            with pytest.raises(ValueError):
                begin_turn("   ", session_id=session_id, conn=conn)


# ============================================================================
# commit_turn
# ============================================================================


class TestCommitTurn:
    def test_commit_completed_sets_status_and_response(
        self, engine, session_id
    ):
        with engine.begin() as conn:
            ctx = begin_turn(
                "what is 2s10s?", session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            commit_turn(
                ctx,
                conn=conn,
                status="completed",
                assistant_response="UST 2s10s is at +35bp.",
            )

        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, assistant_response, completed_at "
                    "FROM copilot_state.turns WHERE id = :id"
                ),
                {"id": ctx.turn_id},
            ).mappings().one()
        assert row["status"] == "completed"
        assert row["assistant_response"] == "UST 2s10s is at +35bp."
        assert row["completed_at"] is not None

    def test_commit_writes_auto_named_binding(
        self, engine, session_id, artifact_hash
    ):
        with engine.begin() as conn:
            ctx = begin_turn(
                "compute it", session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            auto_name = commit_turn(
                ctx,
                conn=conn,
                status="completed",
                assistant_response="done",
                terminal_artifact_hash=artifact_hash,
            )
        assert auto_name == "turn_1_result"

        with engine.connect() as conn:
            entry = ws_module.resolve(
                "turn_1_result",
                session_id=session_id, conn=conn,
            )
        assert entry.artifact_hash == artifact_hash

    def test_commit_writes_save_as_alias(
        self, engine, session_id, artifact_hash
    ):
        with engine.begin() as conn:
            ctx = begin_turn(
                "save as tips_2y_v3", session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            commit_turn(
                ctx,
                conn=conn,
                status="completed",
                assistant_response="bound tips_2y_v3",
                terminal_artifact_hash=artifact_hash,
                save_as="tips_2y_v3",
            )

        with engine.connect() as conn:
            auto = ws_module.resolve(
                "turn_1_result", session_id=session_id, conn=conn,
            )
            alias = ws_module.resolve(
                "tips_2y_v3", session_id=session_id, conn=conn,
            )
        # Both names resolve to the same artifact.
        assert auto.artifact_hash == artifact_hash
        assert alias.artifact_hash == artifact_hash

    def test_commit_failed_does_not_write_binding(
        self, engine, session_id, artifact_hash
    ):
        with engine.begin() as conn:
            ctx = begin_turn(
                "crash test", session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            commit_turn(
                ctx,
                conn=conn,
                status="failed",
                assistant_response="something broke",
                terminal_artifact_hash=artifact_hash,
            )
        # Even when terminal_artifact_hash is provided, a failed
        # status must NOT write a working-set entry.
        from sqlalchemy import text

        with engine.connect() as conn:
            count = conn.execute(
                text(
                    "SELECT COUNT(*) FROM copilot_state.working_set "
                    "WHERE session_id = :sid"
                ),
                {"sid": session_id},
            ).scalar_one()
        assert count == 0

    def test_invalid_status_raises(self, engine, session_id):
        with engine.begin() as conn:
            ctx = begin_turn(
                "x", session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            with pytest.raises(ValueError):
                commit_turn(ctx, conn=conn, status="wat")  # type: ignore[arg-type]

    def test_unknown_artifact_hash_fails_fk(self, engine, session_id):
        from sqlalchemy.exc import IntegrityError

        with engine.begin() as conn:
            ctx = begin_turn(
                "bogus", session_id=session_id, conn=conn,
            )
        bad_hash = "0" * 64
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                commit_turn(
                    ctx,
                    conn=conn,
                    status="completed",
                    terminal_artifact_hash=bad_hash,
                )


# ============================================================================
# fail_turn convenience
# ============================================================================


class TestFailTurn:
    def test_fail_turn_records_failed_status(
        self, engine, session_id
    ):
        with engine.begin() as conn:
            ctx = begin_turn(
                "ka boom", session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            fail_turn(
                ctx, conn=conn, error_message="LLM offline",
            )
        from sqlalchemy import text

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, assistant_response "
                    "FROM copilot_state.turns WHERE id = :id"
                ),
                {"id": ctx.turn_id},
            ).mappings().one()
        assert row["status"] == "failed"
        assert row["assistant_response"] == "LLM offline"

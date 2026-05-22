"""tests/state/test_working_set.py — real-Postgres integration tests
for the working_set substrate.

Phase 0 PR 8.

Tests cover:

  - ``add`` writes an active row with the right shape.
  - ``add`` of a name that already exists RETIRES the old row and
    inserts a new one in the same transaction (partial-unique index
    is honoured).
  - ``retire`` flips an active row to retired; ``retire`` of a name
    with no active binding raises ``UnknownNameError``.
  - ``resolve`` returns the active binding by default.
  - ``resolve(as_of_turn=...)`` returns the historical binding,
    surviving subsequent rebinds.
  - ``list_visible`` returns ACTIVE bindings in introduction order.
  - Name validation rejects malformed inputs.

Module-level skip when Postgres is unreachable; the CI state-layer
job provides a postgres:14 service container.
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
from state import working_set as ws_module  # noqa: E402
from state.artifact_store import put_artifact  # noqa: E402
from state.object_storage import LocalFSBackend  # noqa: E402
from state.working_set import (  # noqa: E402
    InvalidNameError,
    UnknownNameError,
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
    """Per-test cleanup of the working-set + turns + sessions tables
    AND the artifact_metadata they reference.  Order matters because
    of FKs."""
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
    from sqlalchemy import text

    sid = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO copilot_state.sessions (id) VALUES (:id)"
            ),
            {"id": sid},
        )
    return sid


@pytest.fixture
def make_turn(engine, session_id):
    """Insert a fresh turn row and return its UUID.  Each call gets
    a monotonically increasing sequence_no within the session."""
    from sqlalchemy import text

    seq = {"n": 0}

    def _make(user_message: str = "hello") -> uuid.UUID:
        seq["n"] += 1
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    """
                    INSERT INTO copilot_state.turns (
                        session_id, sequence_no, user_message, status
                    )
                    VALUES (:sid, :seq, :msg, 'completed')
                    RETURNING id
                    """
                ),
                {"sid": session_id, "seq": seq["n"], "msg": user_message},
            ).mappings().one()
        return row["id"]

    return _make


@pytest.fixture
def artifact_factory(engine, storage):
    """Build + persist a fresh Series-typed artifact; return its hash."""
    counter = {"n": 0}

    def _make() -> str:
        counter["n"] += 1
        step = FetchStep.build(
            name="fetch_single_tenor",
            version="1.0.0",
            params={
                "curve_family": "UST",
                "tenor": "10Y",
                "slot": counter["n"],
            },
        )
        art = Series(
            series_key="UST.10Y.yield_mid",
            payload=pd.Series(
                [4.0 + counter["n"] * 0.01],
                index=pd.date_range("2024-01-01", periods=1),
            ),
            units=TimeSeriesUnits.PERCENT,
            frequency="D",
            missingness_policy=RawNoCleaning(),
            lineage=Lineage.from_steps([step]),
        )
        with engine.begin() as conn:
            return put_artifact(art, conn=conn, object_storage=storage)

    return _make


# ============================================================================
# Add / resolve / list_visible
# ============================================================================


class TestAdd:
    def test_add_creates_active_row(
        self, engine, session_id, make_turn, artifact_factory
    ):
        turn_id = make_turn()
        h = artifact_factory()
        with engine.begin() as conn:
            entry = ws_module.add(
                "tips_2y_v1", h, turn_id,
                session_id=session_id, conn=conn,
            )
        assert entry.name == "tips_2y_v1"
        assert entry.artifact_hash == h
        assert entry.is_active
        assert entry.retired_at_turn is None
        assert entry.introduced_at_turn == turn_id

    def test_rebind_retires_old_row(
        self, engine, session_id, make_turn, artifact_factory
    ):
        h1, h2 = artifact_factory(), artifact_factory()
        t1, t2 = make_turn(), make_turn()
        with engine.begin() as conn:
            first = ws_module.add(
                "alpha", h1, t1, session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            second = ws_module.add(
                "alpha", h2, t2, session_id=session_id, conn=conn,
            )

        assert second.artifact_hash == h2
        assert second.is_active

        # The first binding should now be retired.
        with engine.connect() as conn:
            from sqlalchemy import text

            row = conn.execute(
                text(
                    "SELECT retired_at_turn FROM copilot_state.working_set "
                    "WHERE id = :id"
                ),
                {"id": first.id},
            ).mappings().one()
        assert row["retired_at_turn"] == t2

    def test_rebind_respects_partial_unique(
        self, engine, session_id, make_turn, artifact_factory
    ):
        """The partial-unique index ``uq_working_set_session_name_active``
        means at most one ACTIVE row per (session, name).  If our retire-
        then-insert is correct, this never trips; the test asserts the
        DB-level invariant directly."""
        h1, h2 = artifact_factory(), artifact_factory()
        t1, t2 = make_turn(), make_turn()
        with engine.begin() as conn:
            ws_module.add(
                "beta", h1, t1, session_id=session_id, conn=conn,
            )
            ws_module.add(
                "beta", h2, t2, session_id=session_id, conn=conn,
            )

        from sqlalchemy import text

        with engine.connect() as conn:
            count = conn.execute(
                text(
                    """
                    SELECT COUNT(*) AS c FROM copilot_state.working_set
                    WHERE session_id = :sid AND name = 'beta'
                      AND retired_at_turn IS NULL
                    """
                ),
                {"sid": session_id},
            ).scalar_one()
        assert count == 1


class TestNameValidation:
    @pytest.mark.parametrize("bad_name", [
        "",  # empty
        "1starts_with_digit",
        "has space",
        "has-hyphen",
        "has.dot",
        "x" * 65,  # too long
        ";DROP TABLE working_set;",
    ])
    def test_rejects_bad_names(
        self, engine, session_id, make_turn, artifact_factory, bad_name
    ):
        h = artifact_factory()
        t = make_turn()
        with engine.begin() as conn:
            with pytest.raises(InvalidNameError):
                ws_module.add(
                    bad_name, h, t,
                    session_id=session_id, conn=conn,
                )

    @pytest.mark.parametrize("good_name", [
        "a", "_x", "tips_2y_v1", "Foo123",
        "x" * 64,  # at boundary
    ])
    def test_accepts_good_names(
        self, engine, session_id, make_turn, artifact_factory, good_name
    ):
        h = artifact_factory()
        t = make_turn()
        with engine.begin() as conn:
            entry = ws_module.add(
                good_name, h, t,
                session_id=session_id, conn=conn,
            )
        assert entry.name == good_name


class TestResolve:
    def test_resolve_active_binding(
        self, engine, session_id, make_turn, artifact_factory
    ):
        h = artifact_factory()
        t = make_turn()
        with engine.begin() as conn:
            ws_module.add(
                "gamma", h, t, session_id=session_id, conn=conn,
            )
        with engine.connect() as conn:
            entry = ws_module.resolve(
                "gamma", session_id=session_id, conn=conn,
            )
        assert entry.artifact_hash == h
        assert entry.is_active

    def test_resolve_unknown_raises(
        self, engine, session_id
    ):
        with engine.connect() as conn:
            with pytest.raises(UnknownNameError):
                ws_module.resolve(
                    "nonexistent",
                    session_id=session_id, conn=conn,
                )

    def test_historical_resolve_survives_rebind(
        self, engine, session_id, make_turn, artifact_factory
    ):
        """Resolve as-of an older turn returns the binding that WAS
        active when that turn ran, regardless of subsequent rebinds.
        """
        h_v1, h_v2 = artifact_factory(), artifact_factory()
        t1 = make_turn("introduce delta = v1")
        t2 = make_turn("look at delta")  # uses v1
        t3 = make_turn("rebind delta = v2")
        t4 = make_turn("look at delta again")  # uses v2

        with engine.begin() as conn:
            ws_module.add(
                "delta", h_v1, t1, session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            ws_module.add(
                "delta", h_v2, t3, session_id=session_id, conn=conn,
            )

        with engine.connect() as conn:
            # as of t2 (before rebind) -> v1
            r_at_t2 = ws_module.resolve(
                "delta", session_id=session_id, conn=conn, as_of_turn=t2,
            )
            # as of t4 (after rebind) -> v2
            r_at_t4 = ws_module.resolve(
                "delta", session_id=session_id, conn=conn, as_of_turn=t4,
            )
            # current -> v2
            r_now = ws_module.resolve(
                "delta", session_id=session_id, conn=conn,
            )
        assert r_at_t2.artifact_hash == h_v1
        assert r_at_t4.artifact_hash == h_v2
        assert r_now.artifact_hash == h_v2


class TestRetire:
    def test_retire_flips_active_to_retired(
        self, engine, session_id, make_turn, artifact_factory
    ):
        h = artifact_factory()
        t1, t2 = make_turn(), make_turn()
        with engine.begin() as conn:
            ws_module.add(
                "epsilon", h, t1, session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            ws_module.retire(
                "epsilon", t2, session_id=session_id, conn=conn,
            )
        with engine.connect() as conn:
            with pytest.raises(UnknownNameError):
                ws_module.resolve(
                    "epsilon", session_id=session_id, conn=conn,
                )

    def test_retire_unknown_raises(
        self, engine, session_id, make_turn
    ):
        t = make_turn()
        with engine.begin() as conn:
            with pytest.raises(UnknownNameError):
                ws_module.retire(
                    "ghost", t, session_id=session_id, conn=conn,
                )


class TestListVisible:
    def test_orders_by_introduction(
        self, engine, session_id, make_turn, artifact_factory
    ):
        h_a, h_b, h_c = (artifact_factory(), artifact_factory(),
                         artifact_factory())
        t_a, t_b, t_c = make_turn(), make_turn(), make_turn()
        with engine.begin() as conn:
            ws_module.add("a", h_a, t_a, session_id=session_id, conn=conn)
            ws_module.add("b", h_b, t_b, session_id=session_id, conn=conn)
            ws_module.add("c", h_c, t_c, session_id=session_id, conn=conn)

        with engine.connect() as conn:
            names = [
                e.name for e in ws_module.list_visible(
                    session_id=session_id, conn=conn,
                )
            ]
        assert names == ["a", "b", "c"]

    def test_excludes_retired(
        self, engine, session_id, make_turn, artifact_factory
    ):
        h = artifact_factory()
        t_intro, t_retire = make_turn(), make_turn()
        with engine.begin() as conn:
            ws_module.add(
                "ephemeral", h, t_intro,
                session_id=session_id, conn=conn,
            )
        with engine.begin() as conn:
            ws_module.retire(
                "ephemeral", t_retire,
                session_id=session_id, conn=conn,
            )
        with engine.connect() as conn:
            visible = ws_module.list_visible(
                session_id=session_id, conn=conn,
            )
        assert visible == []

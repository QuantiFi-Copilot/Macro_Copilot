"""tests/state/test_multi_turn_reference.py — multi-turn integration
test for the working-set + turn lifecycle + resolver wiring.

Phase 0 PR 8.

Simulates a 3-turn conversation:

  Turn 1 — user introduces an artifact and aliases it ("save as
            tips_2y_v1").  commit_turn writes both
            ``turn_1_result`` and ``tips_2y_v1`` -> the same
            artifact hash.

  Turn 2 — user references the alias.  list_visible returns both
            names; the resolver (stubbed) reports the alias as a
            reference.  No new terminal artifact.  commit_turn
            records the assistant_response.

  Turn 3 — user rebinds the alias to a fresh artifact ("save as
            tips_2y_v1" with a different artifact hash).  The
            partial-unique index forces the prior binding to
            retire; resolve(name="tips_2y_v1") after this turn
            points at the new artifact, while
            resolve(name="tips_2y_v1", as_of_turn=turn1)
            still points at the original.

All persistence runs against a real Postgres (module-skip if
unreachable).  No real LLM call — the resolver's structured-output
model is patched to return a deterministic ``ReferenceResolution``
for each turn.
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


# DB skip-guard
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

from shared.artifacts.lineage import FetchStep, Lineage  # noqa: E402
from shared.artifacts.missingness import RawNoCleaning  # noqa: E402
from shared.artifacts.types import Series  # noqa: E402
from shared.artifacts.units import TimeSeriesUnits  # noqa: E402
from orchestrator.reference_resolver import (  # noqa: E402
    ReferenceResolution,
    ReferenceResolver,
)
from orchestrator.state import (  # noqa: E402
    begin_turn,
    commit_turn,
    create_session_if_needed,
)
from state import working_set as ws_module  # noqa: E402
from state.artifact_store import put_artifact  # noqa: E402
from state.object_storage import LocalFSBackend  # noqa: E402


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


def _make_artifact(engine, storage, slot: int) -> str:
    step = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={
            "curve_family": "UST",
            "tenor": "10Y",
            "slot": slot,
        },
    )
    art = Series(
        series_key="UST.10Y.yield_mid",
        payload=pd.Series(
            [4.0 + slot * 0.05],
            index=pd.date_range("2024-01-01", periods=1),
        ),
        units=TimeSeriesUnits.PERCENT,
        frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([step]),
    )
    with engine.begin() as conn:
        return put_artifact(art, conn=conn, object_storage=storage)


# ============================================================================
# Resolver stub
# ============================================================================


class _ScriptedResolver(ReferenceResolver):
    """Resolver that returns a pre-scripted sequence of
    ``ReferenceResolution`` objects in order.  Bypasses the real
    LLM call entirely."""

    def __init__(self, scripted: list[ReferenceResolution]):
        # Skip the parent __init__ — we don't need ChatAnthropic.
        self._scripted = scripted
        self._idx = 0

    async def resolve(self, user_message, *, visible_names, timeout_seconds=8.0):  # noqa: ARG002
        idx = self._idx
        self._idx += 1
        return self._scripted[idx]


# ============================================================================
# Three-turn integration test
# ============================================================================


async def test_three_turn_save_reference_rebind(
    engine, session_id, storage
):
    """End-to-end multi-turn flow that exercises every PR 8 surface:

    1. Turn 1: introduce an artifact, alias it with save_as.
    2. Turn 2: reference the alias from a fresh message.
    3. Turn 3: rebind the alias to a different artifact; verify
       historical resolution still finds the original.
    """
    # Pre-build two artifacts so we can rebind in turn 3.
    h_v1 = _make_artifact(engine, storage, slot=1)
    h_v2 = _make_artifact(engine, storage, slot=2)

    resolver = _ScriptedResolver(scripted=[
        # Turn 1: user said "save as tips_2y_v1"
        ReferenceResolution(
            save_as="tips_2y_v1", referenced_names=[],
        ),
        # Turn 2: user said "compare tips_2y_v1 with the front-end"
        ReferenceResolution(
            save_as=None, referenced_names=["tips_2y_v1"],
        ),
        # Turn 3: user said "rerun tips_2y_v1 with a shorter window;
        # save as tips_2y_v1"  (rebind same name to fresh artifact)
        ReferenceResolution(
            save_as="tips_2y_v1", referenced_names=["tips_2y_v1"],
        ),
    ])

    # --- Turn 1 -------------------------------------------------------
    with engine.begin() as conn:
        t1 = begin_turn(
            "pull UST 10Y and save as tips_2y_v1",
            session_id=session_id, conn=conn,
        )
    # Resolver inspects (currently empty) working-set; returns
    # save_as=tips_2y_v1.
    with engine.connect() as conn:
        visible = [
            n.name for n in ws_module.list_visible(
                session_id=session_id, conn=conn,
            )
        ]
    assert visible == []
    r1 = await resolver.resolve(
        t1.user_message, visible_names=visible,
    )
    assert r1.save_as == "tips_2y_v1"
    with engine.begin() as conn:
        commit_turn(
            t1,
            conn=conn,
            status="completed",
            assistant_response="UST 10Y at 4.05%.  Saved as tips_2y_v1.",
            terminal_artifact_hash=h_v1,
            save_as=r1.save_as,
        )

    # After turn 1: both turn_1_result and tips_2y_v1 are bound.
    with engine.connect() as conn:
        names_after_t1 = [
            n.name for n in ws_module.list_visible(
                session_id=session_id, conn=conn,
            )
        ]
    assert set(names_after_t1) == {"turn_1_result", "tips_2y_v1"}

    # --- Turn 2 -------------------------------------------------------
    with engine.begin() as conn:
        t2 = begin_turn(
            "compare tips_2y_v1 with the front-end",
            session_id=session_id, conn=conn,
        )
    with engine.connect() as conn:
        visible = [
            n.name for n in ws_module.list_visible(
                session_id=session_id, conn=conn,
            )
        ]
    r2 = await resolver.resolve(
        t2.user_message, visible_names=visible,
    )
    # The resolver reports referenced_names = ['tips_2y_v1'] — and
    # because it was in the visible list, sanitisation kept it.
    assert "tips_2y_v1" in visible
    assert r2.referenced_names == ["tips_2y_v1"]
    assert r2.save_as is None
    # The reference resolves to the same artifact bound in turn 1.
    with engine.connect() as conn:
        ref = ws_module.resolve(
            "tips_2y_v1", session_id=session_id, conn=conn,
        )
    assert ref.artifact_hash == h_v1

    with engine.begin() as conn:
        commit_turn(
            t2,
            conn=conn,
            status="completed",
            assistant_response="tips_2y_v1 vs front-end: spread is +27bp.",
            terminal_artifact_hash=None,
            save_as=None,
        )

    # --- Turn 3 -------------------------------------------------------
    with engine.begin() as conn:
        t3 = begin_turn(
            "rerun tips_2y_v1 with a shorter window; save as tips_2y_v1",
            session_id=session_id, conn=conn,
        )
    with engine.connect() as conn:
        visible = [
            n.name for n in ws_module.list_visible(
                session_id=session_id, conn=conn,
            )
        ]
    r3 = await resolver.resolve(
        t3.user_message, visible_names=visible,
    )
    assert r3.save_as == "tips_2y_v1"
    assert r3.referenced_names == ["tips_2y_v1"]

    with engine.begin() as conn:
        commit_turn(
            t3,
            conn=conn,
            status="completed",
            assistant_response="rebound tips_2y_v1.",
            terminal_artifact_hash=h_v2,
            save_as=r3.save_as,
        )

    # After turn 3:
    #   tips_2y_v1 now -> h_v2 (rebound)
    #   tips_2y_v1 as-of turn 1 -> h_v1 (historical)
    #   turn_1_result, turn_3_result both active
    with engine.connect() as conn:
        current = ws_module.resolve(
            "tips_2y_v1", session_id=session_id, conn=conn,
        )
        historical = ws_module.resolve(
            "tips_2y_v1",
            session_id=session_id, conn=conn, as_of_turn=t1.turn_id,
        )
        turn_3_result = ws_module.resolve(
            "turn_3_result", session_id=session_id, conn=conn,
        )

    assert current.artifact_hash == h_v2
    assert historical.artifact_hash == h_v1
    assert turn_3_result.artifact_hash == h_v2

    # Verify the turns table records three completed rows with
    # sequence_no 1, 2, 3 in this session.
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT sequence_no, status FROM copilot_state.turns
                WHERE session_id = :sid
                ORDER BY sequence_no
                """
            ),
            {"sid": session_id},
        ).all()
    assert [(r[0], r[1]) for r in rows] == [
        (1, "completed"),
        (2, "completed"),
        (3, "completed"),
    ]

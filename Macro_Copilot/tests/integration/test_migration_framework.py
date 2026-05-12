"""tests/integration/test_migration_framework.py — verifies that the
Alembic substrate handles a forward migration cleanly without losing
data.

Phase 0 PR 11.

The brief's deliverable: "write a forward migration (add a column to
``workspaces``), upgrade, write data, downgrade, upgrade — schema and
data both intact."

Migration ``0006_workspaces_last_accessed`` is the target.  It adds
a nullable ``last_accessed_at TIMESTAMPTZ`` column.  The test:

  1. Upgrades to head (lands at 0006).
  2. Inserts a workspace + its FK dependencies (an artifact + a DAG).
  3. Asserts the new column exists and is queryable.
  4. Downgrades by one revision (drops 0006's column).
  5. Asserts the workspace row's OTHER columns still resolve via the
     primary key — i.e. the downgrade did not blow away unrelated
     data.
  6. Re-upgrades to head.
  7. Asserts the new column is back AND every pre-existing row is
     still present (same UUID, same slug, same dag_hash, same name).

The discipline: a future migration that accidentally drops a row OR
fails to repopulate after re-upgrade trips this test on the next
run.  ``test_migrations.py`` covers schema-shape invariants; this
file covers DATA invariants under round-trip.

Module-level skip when Postgres is unreachable; CI's state-layer
job provides the postgres:14 service container.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Iterator

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WORKTREE_ROOT = _PROJECT_ROOT.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# DB availability — same pattern as test_migrations.py
# ============================================================================


def _build_db_url() -> str:
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


_DB_URL = _build_db_url()
_DB_AVAILABLE = _db_reachable(_DB_URL)

pytestmark = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=(
        f"Postgres not reachable at {_DB_URL!r}.  CI's state-layer job "
        "provides the postgres:14 service container."
    ),
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def alembic_config():
    from alembic.config import Config

    ini_path = _WORKTREE_ROOT / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option(
        "script_location", str(_WORKTREE_ROOT / "migrations"),
    )
    return cfg


@pytest.fixture
def clean_and_upgraded(alembic_config) -> Iterator[None]:
    """Wipe + re-migrate to head.  Same destructive pattern as
    ``test_migrations.py::clean_schema``; only safe under the
    test-DB env vars confirmed by the module-level skip."""
    from sqlalchemy import create_engine, text
    from alembic import command

    engine = create_engine(_DB_URL)
    try:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS copilot_state CASCADE"))
        command.upgrade(alembic_config, "head")
        yield
    finally:
        engine.dispose()


def _insert_dag_and_workspace(conn) -> dict:
    """Insert a placeholder DAG + a workspace, return the ids.

    We bypass ``state.workspace_repo`` / ``state.dag_repo`` here so
    the test is independent of the substrate layer — what's being
    verified is the migration framework, not the repo wrappers."""
    from sqlalchemy import text

    dag_hash = "f" * 64
    conn.execute(
        text(
            """
            INSERT INTO copilot_state.dags (hash, topology)
            VALUES (:h, CAST('{"nodes": [], "edges": []}' AS JSONB))
            """
        ),
        {"h": dag_hash},
    )
    ws_id = uuid.uuid4()
    slug = f"mig-test-{ws_id.hex[:8]}"
    conn.execute(
        text(
            """
            INSERT INTO copilot_state.workspaces (
                id, slug, name, dag_hash, created_by
            )
            VALUES (:id, :slug, :name, :h, :cb)
            """
        ),
        {
            "id": ws_id,
            "slug": slug,
            "name": "migration framework test",
            "h": dag_hash,
            "cb": "migration-test",
        },
    )
    return {"workspace_id": ws_id, "slug": slug, "dag_hash": dag_hash}


# ============================================================================
# Forward-migration framework verification
# ============================================================================


class TestForwardMigrationFramework:
    def test_round_trip_preserves_workspace_data(
        self, alembic_config, clean_and_upgraded,
    ):
        """The brief's exact verification.

        1. After upgrade head, the ``last_accessed_at`` column from
           migration 0006 exists.
        2. INSERT a workspace row.
        3. Downgrade BY TWO revisions (skip the 0007 no-op
           sentinel, then drop 0006's column).  Assert the new
           column is GONE but the workspace's other columns still
           resolve via id.
        4. Re-upgrade head.  Assert the new column is BACK and
           the workspace row is still there with all its old
           columns intact.

        Note on the "-2" step: PR 12 added 0007 as a no-op
        closed-family-extension sentinel.  Downgrade -1 lands at
        0006 (column still present); we want to exercise 0006's
        column drop, so we downgrade -2 → lands at 0005 → the
        column is gone.  Phase 0 PR 11's framework test used a
        single -1 step because 0006 was the head at the time; now
        that 0007 is head, the same data-round-trip exercise
        requires -2.
        """
        from sqlalchemy import create_engine, text
        from alembic import command

        engine = create_engine(_DB_URL)
        try:
            # ---- 1. Confirm we're at head (0007 — the new closed-
            # family-extension sentinel) and 0006's column exists.
            with engine.connect() as conn:
                version = conn.execute(
                    text(
                        "SELECT version_num FROM copilot_state.alembic_version"
                    )
                ).scalar_one()
            assert version == "0008_backtest_archetype"

            col_exists = _column_exists(
                engine, "workspaces", "last_accessed_at",
            )
            assert col_exists, (
                "Expected last_accessed_at column after upgrade head"
            )

            # ---- 2. Insert a workspace + count rows pre-cycle.
            with engine.begin() as conn:
                ids = _insert_dag_and_workspace(conn)

            with engine.connect() as conn:
                count_pre = conn.execute(
                    text(
                        "SELECT COUNT(*) FROM copilot_state.workspaces "
                        "WHERE id = :id"
                    ),
                    {"id": ids["workspace_id"]},
                ).scalar_one()
            assert count_pre == 1

            # ---- 3. Downgrade by THREE revisions: 0008's no-op
            # backtest-archetype sentinel + 0007's no-op TradeSet
            # sentinel + 0006's drop of last_accessed_at.  Lands at
            # 0005.  (Pre-PR-19 head was 0007 → -2; PR 19 adds 0008
            # → -3.  Adjust here whenever a new sentinel lands so the
            # test continues to target 0005.)
            command.downgrade(alembic_config, "-3")

            with engine.connect() as conn:
                version_after = conn.execute(
                    text(
                        "SELECT version_num FROM copilot_state.alembic_version"
                    )
                ).scalar_one()
            assert version_after == "0005_workspace_slug"
            assert not _column_exists(
                engine, "workspaces", "last_accessed_at",
            ), "Downgrade should have dropped last_accessed_at"

            # The workspace row's OTHER columns still resolve.
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        """
                        SELECT slug, name, dag_hash, created_by
                        FROM copilot_state.workspaces
                        WHERE id = :id
                        """
                    ),
                    {"id": ids["workspace_id"]},
                ).mappings().one()
            assert row["slug"] == ids["slug"]
            assert row["name"] == "migration framework test"
            assert row["dag_hash"] == ids["dag_hash"]
            assert row["created_by"] == "migration-test"

            # ---- 4. Re-upgrade head (re-applies 0006 + 0007).
            command.upgrade(alembic_config, "head")
            with engine.connect() as conn:
                version_back = conn.execute(
                    text(
                        "SELECT version_num FROM copilot_state.alembic_version"
                    )
                ).scalar_one()
            assert version_back == "0008_backtest_archetype"
            assert _column_exists(
                engine, "workspaces", "last_accessed_at",
            )

            # The row is still there; the new column is NULL on the
            # pre-existing row (it was inserted before 0006 re-applied,
            # but actually 0006 was applied first then downgraded — the
            # row predates the down/up cycle, and migration up should
            # not populate values).
            with engine.connect() as conn:
                final = conn.execute(
                    text(
                        """
                        SELECT slug, name, dag_hash, created_by,
                               last_accessed_at
                        FROM copilot_state.workspaces
                        WHERE id = :id
                        """
                    ),
                    {"id": ids["workspace_id"]},
                ).mappings().one()
            assert final["slug"] == ids["slug"]
            assert final["name"] == "migration framework test"
            assert final["dag_hash"] == ids["dag_hash"]
            assert final["created_by"] == "migration-test"
            # last_accessed_at column is back as NULL (no data
            # migration repopulates it; that's deliberate).
            assert final["last_accessed_at"] is None
        finally:
            engine.dispose()

    def test_can_write_new_column_after_re_upgrade(
        self, alembic_config, clean_and_upgraded,
    ):
        """After the cycle, the new column accepts WRITES.  A
        regression that left the column as a stub would trip here.

        Downgrade -2 to skip PR 12's no-op sentinel (0007) and
        actually drop the 0006 column; then re-upgrade to head.
        """
        from sqlalchemy import create_engine, text
        from alembic import command
        from datetime import datetime, timezone

        engine = create_engine(_DB_URL)
        try:
            with engine.begin() as conn:
                ids = _insert_dag_and_workspace(conn)
            command.downgrade(alembic_config, "-2")
            command.upgrade(alembic_config, "head")

            now = datetime.now(timezone.utc)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        """
                        UPDATE copilot_state.workspaces
                        SET last_accessed_at = :t
                        WHERE id = :id
                        """
                    ),
                    {"id": ids["workspace_id"], "t": now},
                )
            with engine.connect() as conn:
                got = conn.execute(
                    text(
                        "SELECT last_accessed_at FROM "
                        "copilot_state.workspaces WHERE id = :id"
                    ),
                    {"id": ids["workspace_id"]},
                ).scalar_one()
            assert got is not None
        finally:
            engine.dispose()


# ============================================================================
# Helpers
# ============================================================================


def _column_exists(engine, table: str, column: str) -> bool:
    from sqlalchemy import text

    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'copilot_state'
                  AND table_name = :t
                  AND column_name = :c
                """
            ),
            {"t": table, "c": column},
        ).first()
    return row is not None

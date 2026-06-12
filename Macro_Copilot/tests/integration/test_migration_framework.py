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
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _resolve_worktree_root() -> Path | None:
    """Locate the directory containing ``alembic.ini`` + ``migrations/``.

    Same container-aware resolution as
    ``tests/state/test_migrations.py::_resolve_worktree_root``.  On the
    host the Alembic config lives one level above the project
    (``<repo-root>/alembic.ini`` next to ``<repo-root>/Macro_Copilot/``).
    Inside the api-server container the project is mounted at ``/app``
    and the repo root at ``/repo-root`` (docker-compose: ``.:/app`` +
    ``..:/repo-root``) — NOT parent/child — so the previous naive
    ``parents[N].parent`` walk resolved to ``/migrations`` and the
    ``clean_and_upgraded`` fixture dropped the schema, failed the
    upgrade, and poisoned every later DB-dependent test in the session.

    Resolution order (first hit wins):

    1. ``MACRO_REPO_ROOT`` env var.
    2. Parents of this test file (host checkouts).
    3. CWD and its parents.
    4. ``/repo-root`` (the docker-compose container mount).
    """
    candidates: list[Path] = []
    env_root = os.getenv("MACRO_REPO_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    candidates.extend(Path(__file__).resolve().parents)
    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])
    candidates.append(Path("/repo-root"))
    for cand in candidates:
        if (cand / "alembic.ini").is_file() and (cand / "migrations").is_dir():
            return cand
    return None


_WORKTREE_ROOT = _resolve_worktree_root()  # contains alembic.ini + migrations/


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


@pytest.fixture(scope="module", autouse=True)
def restore_schema_to_head() -> Iterator[None]:
    """Guarantee the real ``copilot_state`` schema is rebuilt after this
    module runs — even when individual tests fail mid-migration.

    Mirrors ``tests/state/test_migrations.py::restore_schema_to_head``:
    ``clean_and_upgraded`` DROPs the shared schema before upgrading, so
    a failure between the drop and the re-upgrade used to cascade
    errors across every later suite that reads ``copilot_state.*``.
    This module-scoped finalizer runs ``alembic upgrade head`` exactly
    once after the last test in the module, pass or fail.
    """
    yield
    if _WORKTREE_ROOT is None:
        # alembic.ini was never found → alembic_config failed at setup
        # and clean_and_upgraded never dropped anything.  No restore
        # needed — and none possible without the config.
        return
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_WORKTREE_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_WORKTREE_ROOT / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture
def alembic_config():
    from alembic.config import Config

    if _WORKTREE_ROOT is None:
        pytest.fail(
            "alembic.ini + migrations/ not found.  Looked in "
            "$MACRO_REPO_ROOT, the parents of this test file, the CWD "
            "and its parents, and /repo-root (the docker-compose "
            "container mount)."
        )
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
    test-DB env vars confirmed by the module-level skip.

    The upgrade runs inside try/finally only at module teardown (see
    ``restore_schema_to_head``); if the upgrade itself raises here the
    module-scoped finalizer still rebuilds the schema."""
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
        3. Downgrade BY FIVE revisions (unwind 0010..0007, then
           drop 0006's column → lands at 0005).  Assert the new
           column is GONE but the workspace's other columns still
           resolve via id.
        4. Re-upgrade head.  Assert the new column is BACK and
           the workspace row is still there with all its old
           columns intact.

        Note on the step count: Phase 0 PR 11 used -1 (0006 was
        head); each later migration adds one (0007 → -2, 0008 → -3,
        0009 → -4, 0010_workspace_run_audit → -5) so the cycle
        keeps targeting 0005, the revision below 0006's column add.
        """
        from sqlalchemy import create_engine, text
        from alembic import command

        engine = create_engine(_DB_URL)
        try:
            # ---- 1. Confirm we're at head (0010 — workspaces
            # run_audit JSONB column, this consolidation) and 0006's
            # column still exists.
            with engine.connect() as conn:
                version = conn.execute(
                    text(
                        "SELECT version_num FROM copilot_state.alembic_version"
                    )
                ).scalar_one()
            assert version == "0010_workspace_run_audit"

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

            # ---- 3. Downgrade by FIVE revisions to land at 0005:
            # 0010's drop of run_audit (real column drop, this
            # consolidation) + 0009's drop of bound_slot_values +
            # template_id (real column drop) + 0008's no-op
            # backtest-archetype sentinel + 0007's no-op TradeSet
            # sentinel + 0006's drop of last_accessed_at.  (Pre-PR-19
            # head was 0007 → -2; PR 19 added 0008 → -3; PR-B added
            # 0009 → -4; migration 0010_workspace_run_audit → -5.
            # Adjust here whenever a new migration lands so the test
            # continues to target 0005.)
            command.downgrade(alembic_config, "-5")

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

            # ---- 4. Re-upgrade head (re-applies 0006 through 0010).
            command.upgrade(alembic_config, "head")
            with engine.connect() as conn:
                version_back = conn.execute(
                    text(
                        "SELECT version_num FROM copilot_state.alembic_version"
                    )
                ).scalar_one()
            # Head pin updated for migration 0010 (run_audit), this
            # consolidation.
            assert version_back == "0010_workspace_run_audit"
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

        Downgrade -5 to land at 0005 and actually drop the 0006
        column (head moved 0007 → 0010 across PR 19 / PR-B / the
        run_audit consolidation; -2 would now only cycle 0009+0010
        and never touch last_accessed_at); then re-upgrade to head.
        """
        from sqlalchemy import create_engine, text
        from alembic import command
        from datetime import datetime, timezone

        engine = create_engine(_DB_URL)
        try:
            with engine.begin() as conn:
                ids = _insert_dag_and_workspace(conn)
            command.downgrade(alembic_config, "-5")
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

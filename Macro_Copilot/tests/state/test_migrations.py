"""tests/state/test_migrations.py — Alembic round-trip migration tests.

These tests exercise the migration framework against a real Postgres
instance.  They are intentionally NOT pure unit tests: a migration's
correctness is a property of the SQL it emits against a live database,
not a property of the Python AST.

Test database resolution
------------------------
Each test reads the DB connection from environment variables
(``DB_USER`` / ``DB_PASSWORD`` / ``DB_HOST`` / ``DB_PORT`` / ``DB_NAME``)
using the same convention as ``database.database.get_db_engine`` and
``migrations/env.py``.

If no Postgres is reachable (the env vars are unset OR connecting fails),
the entire module is **skipped** with a clear reason rather than reporting
a false negative.  This lets developers run the full test suite without
spinning up a DB locally; CI's dedicated ``migrations`` job provides the
Postgres service container for the actual gate.

What gets tested
----------------
1. ``alembic upgrade head`` against a clean DB creates the expected set of
   tables in the ``copilot_state`` schema, with the alembic_version row
   pointing at the latest head.
2. ``alembic downgrade base`` removes every table this project's migrations
   added — leaving the ``copilot_state`` schema with only the
   ``alembic_version`` table (the namespace itself stays, see env.py).
3. ``alembic upgrade head`` after a downgrade is idempotent — the same
   table set comes back, no exceptions.
4. Schema isolation: applying our migrations does NOT touch the
   ``macro_data`` schema (which is managed by a separate hand-written
   schema.sql and is NOT Alembic's concern).
5. Multi-step chain: ``base → 0001 → 0002 → 0001 → base`` succeeds with
   each step a clean transactional commit.  This is what the
   ``0002_noop_sentinel`` revision exists to enable.

Closes ``docs/technical_debt.md`` — n/a (this is new framework work, not
the resolution of an existing tech-debt item).  Acceptance criterion for
Phase 0 PR 4.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Iterator

import pytest


# Make the project's `database.database` import path resolvable.  The
# conftest.py at Macro_Copilot/tests/ already inserts Macro_Copilot/ into
# sys.path; this is a belt-and-braces add-on.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _resolve_worktree_root() -> Path | None:
    """Locate the directory containing ``alembic.ini`` + ``migrations/``.

    On the host the Alembic config lives ONE level above the project
    (``<repo-root>/alembic.ini`` next to ``<repo-root>/Macro_Copilot/``),
    so walking this file's parents finds it.  Inside the api-server
    container, however, the project is mounted at ``/app`` and the repo
    root at ``/repo-root`` (see docker-compose.yml: ``.:/app`` +
    ``..:/repo-root``) — they are NOT parent/child there, which is why
    a naive ``parents[N]`` walk used to resolve to ``/alembic.ini``.

    Resolution order (first hit wins):

    1. ``MACRO_REPO_ROOT`` env var — explicit override for CI or
       unusual layouts.
    2. Parents of this test file (host checkouts).
    3. CWD and its parents (pytest invoked from elsewhere on host).
    4. ``/repo-root`` — the docker-compose container mount.

    Returns ``None`` when no candidate contains both ``alembic.ini``
    and a ``migrations/`` directory.
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


# Expected table names in the copilot_state schema after `upgrade head`.
# `alembic_version` is created by Alembic itself.  Order does not matter
# here; the test asserts set equality.
EXPECTED_TABLES_AFTER_UPGRADE = frozenset({
    "alembic_version",
    "application_version",
    "methodology_versions",
    "artifact_metadata",
    "dags",
    "dag_nodes",
    "dag_edges",
    "sessions",
    "turns",
    "message_events",
    "workspaces",
    "workspace_variants",
    "working_set",
})

# After `downgrade base` runs, the only table left in the schema is
# the Alembic version-tracking table.  The migration's `downgrade`
# explicitly does NOT drop the schema itself — see
# 0001_initial_copilot_state.py for the rationale.
EXPECTED_TABLES_AFTER_DOWNGRADE = frozenset({"alembic_version"})


COPILOT_STATE_SCHEMA = "copilot_state"


# ============================================================================
# Postgres fixture — skip cleanly when no DB is reachable
# ============================================================================


def _build_db_url() -> str:
    """Build a SQLAlchemy URL using the same env-var convention as
    ``database.database.get_db_engine`` and ``migrations/env.py``."""
    user = os.getenv("DB_USER", "quantuser")
    password = os.getenv("DB_PASSWORD", "myStrongPass")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    db_name = os.getenv("DB_NAME", "macrodata")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db_name}"


def _postgres_reachable(url: str) -> bool:
    """Return True if a connection can be opened against ``url``.  Used
    by the module-level skip so the test does not falsely fail when run
    in environments without a DB."""
    try:
        from sqlalchemy import create_engine

        engine = create_engine(url)
        with engine.connect() as conn:
            from sqlalchemy import text

            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


_DB_URL = _build_db_url()
_DB_AVAILABLE = _postgres_reachable(_DB_URL)


pytestmark = pytest.mark.skipif(
    not _DB_AVAILABLE,
    reason=(
        f"Postgres not reachable at {_DB_URL!r}.  Set DB_USER / DB_PASSWORD "
        "/ DB_HOST / DB_PORT / DB_NAME to point at a writable Postgres, "
        "or run `docker compose up tsdb` in the project root.  CI's "
        "migrations job provides this via a Postgres service container."
    ),
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(scope="module", autouse=True)
def restore_schema_to_head() -> Iterator[None]:
    """Guarantee the real ``copilot_state`` schema is rebuilt after this
    module runs — even when individual tests fail mid-migration.

    These tests intentionally DROP and rebuild ``copilot_state`` (see
    ``clean_schema``).  A run that ended on a failing test used to
    leave the schema dropped (or at ``downgrade base``), which cascaded
    hundreds of errors across every other suite that reads
    ``copilot_state.*``.  This module-scoped finalizer runs
    ``alembic upgrade head`` exactly once after the last test in the
    module, pass or fail — pytest executes fixture finalizers even when
    tests raise.

    If the restore itself fails it raises (reported as a teardown
    error): a silently-broken schema is precisely the failure mode this
    fixture exists to prevent.
    """
    yield
    if _WORKTREE_ROOT is None:
        # alembic.ini was never found, so ``alembic_config`` failed at
        # setup and ``clean_schema`` (function-scoped, set up after the
        # module-scoped config) never dropped anything.  No restore
        # needed — and none is possible without the config.
        return
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_WORKTREE_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_WORKTREE_ROOT / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="module")
def alembic_config():
    """Build an Alembic ``Config`` rooted at the worktree's ``alembic.ini``
    and pinned at the test-time DB URL.

    ``alembic.ini`` carries a placeholder URL; ``env.py`` overrides it
    from env vars.  We override here once more, defensively, so the test
    cannot accidentally write to a different DB than the one
    ``_postgres_reachable`` confirmed.
    """
    from alembic.config import Config

    if _WORKTREE_ROOT is None:
        pytest.fail(
            "alembic.ini + migrations/ not found.  Looked in "
            "$MACRO_REPO_ROOT, the parents of this test file, the CWD "
            "and its parents, and /repo-root (the docker-compose "
            "container mount).  Tests must run from a checkout whose "
            "repo root contains alembic.ini (Phase 0 PR 4 introduced "
            "it), or set MACRO_REPO_ROOT to that directory."
        )
    cfg = Config(str(_WORKTREE_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_WORKTREE_ROOT / "migrations"))
    return cfg


@pytest.fixture
def clean_schema() -> Iterator[None]:
    """Wipe the copilot_state schema completely before each test so each
    test starts from a known-empty state.  ``DROP SCHEMA … CASCADE``
    removes all tables AND the alembic_version table inside the schema,
    which is exactly what we want — the next ``alembic upgrade head``
    re-creates the schema via env.py's ``CREATE SCHEMA IF NOT EXISTS``.

    This is a destructive operation on the DB pointed at by ``_DB_URL``.
    The module-level skip plus the env-var-controlled connection ensures
    the test cannot run against a production database without
    intentional configuration.
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(_DB_URL)
    with engine.begin() as conn:
        conn.execute(
            text(f"DROP SCHEMA IF EXISTS {COPILOT_STATE_SCHEMA} CASCADE")
        )
    engine.dispose()
    yield
    # No per-test teardown — subsequent tests reset via this same
    # fixture, and the module-scoped ``restore_schema_to_head``
    # finalizer rebuilds the schema (``alembic upgrade head``) after the
    # last test, even when tests fail.


def _table_names_in_schema(schema: str) -> set[str]:
    """Return the set of table names present in ``schema``."""
    from sqlalchemy import create_engine, text

    engine = create_engine(_DB_URL)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema"
                ),
                {"schema": schema},
            ).fetchall()
        return {row[0] for row in rows}
    finally:
        engine.dispose()


# ============================================================================
# Tests
# ============================================================================


class TestUpgradeHead:
    """`alembic upgrade head` produces the expected end-state."""

    def test_creates_all_expected_tables(
        self, alembic_config, clean_schema
    ) -> None:
        """After ``upgrade head`` against a clean DB, every table in
        ``EXPECTED_TABLES_AFTER_UPGRADE`` is present in
        ``copilot_state``."""
        from alembic import command

        command.upgrade(alembic_config, "head")

        actual = _table_names_in_schema(COPILOT_STATE_SCHEMA)
        assert actual == EXPECTED_TABLES_AFTER_UPGRADE, (
            f"Schema diff after upgrade head:\n"
            f"  expected: {sorted(EXPECTED_TABLES_AFTER_UPGRADE)}\n"
            f"  actual:   {sorted(actual)}\n"
            f"  missing:  {sorted(EXPECTED_TABLES_AFTER_UPGRADE - actual)}\n"
            f"  extra:    {sorted(actual - EXPECTED_TABLES_AFTER_UPGRADE)}"
        )

    def test_alembic_version_points_at_head(
        self, alembic_config, clean_schema
    ) -> None:
        """The ``alembic_version`` row tracks the latest head revision."""
        from alembic import command
        from sqlalchemy import create_engine, text

        command.upgrade(alembic_config, "head")

        engine = create_engine(_DB_URL)
        try:
            with engine.connect() as conn:
                version = conn.execute(
                    text(
                        f"SELECT version_num FROM "
                        f"{COPILOT_STATE_SCHEMA}.alembic_version"
                    )
                ).scalar_one()
        finally:
            engine.dispose()

        # Latest head is the workspaces run_audit sidecar (0010,
        # orchestration-upgrade Phase D / D9 — the non-hashed
        # intent-audit blob the build page renders "what I understood /
        # checked / fixed" from).
        # Chains: 0001 -> 0002 -> 0003 -> 0004 -> 0005 -> 0006 -> 0007 ->
        #         0008 -> 0009 -> 0010.
        assert version == "0010_workspace_run_audit", (
            f"Expected alembic_version to point at "
            f"0010_workspace_run_audit, got {version!r}.  "
            "Either a new migration landed without updating this test, "
            "or the head chain is broken."
        )

    def test_macro_data_schema_untouched(
        self, alembic_config, clean_schema
    ) -> None:
        """``alembic upgrade head`` does not create, drop, or modify any
        table in the ``macro_data`` schema."""
        before = _table_names_in_schema("macro_data")

        from alembic import command

        command.upgrade(alembic_config, "head")

        after = _table_names_in_schema("macro_data")
        assert before == after, (
            f"Alembic migrations touched the macro_data schema.\n"
            f"  before: {sorted(before)}\n"
            f"  after:  {sorted(after)}\n"
            "Migrations under this project's Alembic config MUST only "
            "affect copilot_state + langgraph_checkpoint (as namespace)."
        )

    def test_langgraph_checkpoint_schema_namespace_exists(
        self, alembic_config, clean_schema
    ) -> None:
        """After ``alembic upgrade head``, the ``langgraph_checkpoint``
        Postgres schema exists (created by migration
        ``0003_langgraph_checkpoint_schema``) — empty, ready for
        ``AsyncPostgresSaver.setup()`` to create its tables inside it
        at API server startup.

        Phase 0 PR 5 introduced this namespace.  The actual checkpoint
        tables are NOT Alembic-managed; they're created by the
        LangGraph framework's ``setup()`` and tested separately in
        ``tests/state/test_postgres_checkpointer.py``."""
        from alembic import command
        from sqlalchemy import create_engine, text

        command.upgrade(alembic_config, "head")

        engine = create_engine(_DB_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name = 'langgraph_checkpoint'"
                    )
                ).fetchone()
        finally:
            engine.dispose()
        assert row is not None, (
            "The langgraph_checkpoint schema namespace was not created "
            "by alembic upgrade head.  Migration "
            "0003_langgraph_checkpoint_schema is required for "
            "AsyncPostgresSaver.setup() to land its tables in the right "
            "schema."
        )


class TestDowngradeBase:
    """`alembic downgrade base` undoes every migration."""

    def test_leaves_only_alembic_version_table(
        self, alembic_config, clean_schema
    ) -> None:
        """After ``downgrade base``, the only table left in
        ``copilot_state`` is ``alembic_version``.  The schema namespace
        itself stays (intentional — see 0001's downgrade rationale)."""
        from alembic import command

        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "base")

        actual = _table_names_in_schema(COPILOT_STATE_SCHEMA)
        assert actual == EXPECTED_TABLES_AFTER_DOWNGRADE, (
            f"After downgrade base, expected only "
            f"{sorted(EXPECTED_TABLES_AFTER_DOWNGRADE)}, got "
            f"{sorted(actual)}."
        )

    def test_alembic_version_empty_after_downgrade(
        self, alembic_config, clean_schema
    ) -> None:
        """After ``downgrade base`` the ``alembic_version`` table exists
        but contains zero rows (no migrations are applied)."""
        from alembic import command
        from sqlalchemy import create_engine, text

        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "base")

        engine = create_engine(_DB_URL)
        try:
            with engine.connect() as conn:
                row_count = conn.execute(
                    text(
                        f"SELECT count(*) FROM "
                        f"{COPILOT_STATE_SCHEMA}.alembic_version"
                    )
                ).scalar_one()
        finally:
            engine.dispose()

        assert row_count == 0, (
            f"alembic_version should be empty after downgrade base; "
            f"got {row_count} rows."
        )


class TestRoundTrip:
    """The full upgrade → downgrade → upgrade cycle is idempotent."""

    def test_full_round_trip_idempotent(
        self, alembic_config, clean_schema
    ) -> None:
        """``base → head → base → head`` produces the same end-state as
        a single ``base → head``."""
        from alembic import command

        command.upgrade(alembic_config, "head")
        after_first_upgrade = _table_names_in_schema(COPILOT_STATE_SCHEMA)

        command.downgrade(alembic_config, "base")
        command.upgrade(alembic_config, "head")
        after_round_trip = _table_names_in_schema(COPILOT_STATE_SCHEMA)

        assert after_first_upgrade == after_round_trip, (
            f"Round-trip migration produced different end-state.\n"
            f"  after upgrade #1:   {sorted(after_first_upgrade)}\n"
            f"  after round-trip:   {sorted(after_round_trip)}"
        )

    def test_multi_step_chain_in_both_directions(
        self, alembic_config, clean_schema
    ) -> None:
        """The 2-step chain ``base → 0001 → 0002`` is reversible step
        by step.  This is what ``0002_noop_sentinel`` exists to prove
        — that future multi-step migrations can be stepped through
        cleanly in either direction.
        """
        from alembic import command

        # base → 0001 (just the initial migration, NOT all the way to head)
        command.upgrade(alembic_config, "0001_initial_copilot_state")
        # 0001 → 0002 (no-op sentinel; should change nothing observable)
        command.upgrade(alembic_config, "0002_noop_sentinel")
        tables_at_head = _table_names_in_schema(COPILOT_STATE_SCHEMA)
        assert tables_at_head == EXPECTED_TABLES_AFTER_UPGRADE

        # 0002 → 0001 (downgrade the sentinel — no-op semantically)
        command.downgrade(alembic_config, "0001_initial_copilot_state")
        tables_at_0001 = _table_names_in_schema(COPILOT_STATE_SCHEMA)
        # The no-op downgrade should leave the table set unchanged.
        assert tables_at_0001 == EXPECTED_TABLES_AFTER_UPGRADE

        # 0001 → base (full teardown)
        command.downgrade(alembic_config, "base")
        tables_at_base = _table_names_in_schema(COPILOT_STATE_SCHEMA)
        assert tables_at_base == EXPECTED_TABLES_AFTER_DOWNGRADE


class TestSchemaInvariants:
    """Spot-check key columns + constraints survive the migration."""

    def test_artifact_payload_exclusivity_check_present(
        self, alembic_config, clean_schema
    ) -> None:
        """``artifact_metadata`` has a CHECK enforcing that exactly one
        of ``payload_uri`` or ``inline_payload`` is non-null.  This is
        load-bearing for the inline-vs-object-storage split planned in
        Phase 0 week 4."""
        from alembic import command
        from sqlalchemy import create_engine, text

        command.upgrade(alembic_config, "head")

        engine = create_engine(_DB_URL)
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = "
                        f"'{COPILOT_STATE_SCHEMA}.artifact_metadata'::regclass "
                        "AND conname = 'ck_artifact_metadata_payload_exactly_one'"
                    )
                ).fetchone()
        finally:
            engine.dispose()
        assert row is not None, (
            "ck_artifact_metadata_payload_exactly_one CHECK constraint "
            "missing — the inline/uri exclusivity invariant is not "
            "enforced at the DB layer."
        )

    def test_workspaces_schema_version_default_is_1(
        self, alembic_config, clean_schema
    ) -> None:
        """``workspaces.schema_version`` defaults to 1.  This is the
        forward-compat slot for workspace data-format evolution."""
        from alembic import command
        from sqlalchemy import create_engine, text

        command.upgrade(alembic_config, "head")

        engine = create_engine(_DB_URL)
        try:
            with engine.connect() as conn:
                default = conn.execute(
                    text(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_schema = :schema "
                        "AND table_name = 'workspaces' "
                        "AND column_name = 'schema_version'"
                    ),
                    {"schema": COPILOT_STATE_SCHEMA},
                ).scalar_one()
        finally:
            engine.dispose()
        assert default == "1", (
            f"Expected workspaces.schema_version DEFAULT 1; got {default!r}."
        )

    def test_alembic_version_table_lives_in_copilot_state(
        self, alembic_config, clean_schema
    ) -> None:
        """``alembic_version`` is created inside ``copilot_state``, not
        in the default ``public`` schema.  This co-locates all of this
        layer's metadata with its tables.

        NOTE: the shared dev database (``macrodata``) is multi-tenant —
        Superset's own Alembic manages a ``public.alembic_version``
        table there (see docker/superset_config.py pointing its
        metadata DB at the same Postgres).  The invariant under test is
        therefore NOT "public has no alembic_version table" but rather
        "OUR migration run neither creates that table nor writes our
        revision ids into it".
        """
        from alembic import command
        from alembic.script import ScriptDirectory
        from sqlalchemy import create_engine, text

        in_public_before = "alembic_version" in _table_names_in_schema(
            "public"
        )

        command.upgrade(alembic_config, "head")

        in_public_after = "alembic_version" in _table_names_in_schema(
            "public"
        )
        in_copilot_state = "alembic_version" in _table_names_in_schema(
            COPILOT_STATE_SCHEMA
        )
        assert in_copilot_state, (
            "alembic_version is not in copilot_state — the env.py "
            "version_table_schema config is not being applied."
        )
        assert in_public_after == in_public_before, (
            "alembic upgrade head CREATED public.alembic_version — this "
            "pollutes the shared default schema; the env.py "
            "version_table_schema config is not being applied."
        )

        if in_public_after:
            # A foreign app (e.g. Superset) owns public.alembic_version
            # in the shared dev DB.  Verify none of OUR revision ids
            # were written into it — that would mean our env.py tracked
            # this project's migrations in the wrong schema.
            our_revisions = {
                rev.revision
                for rev in ScriptDirectory.from_config(
                    alembic_config
                ).walk_revisions()
            }
            engine = create_engine(_DB_URL)
            try:
                with engine.connect() as conn:
                    public_rows = {
                        row[0]
                        for row in conn.execute(
                            text(
                                "SELECT version_num FROM "
                                "public.alembic_version"
                            )
                        ).fetchall()
                    }
            finally:
                engine.dispose()
            leaked = public_rows & our_revisions
            assert not leaked, (
                f"This project's revision ids {sorted(leaked)} appear in "
                "public.alembic_version — our migrations are being "
                "version-tracked in the shared public schema instead of "
                "copilot_state."
            )

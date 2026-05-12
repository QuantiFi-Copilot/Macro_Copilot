"""tests/state/test_workspace_repo.py — real-Postgres tests for
workspace CRUD + slug derivation.

Phase 0 PR 10.

Tests cover:

  - ``slugify`` is order-stable, idempotent, and resilient to
    adversarial inputs (SQL injection vectors, path traversal,
    unicode, control chars).
  - ``derive_slug`` always appends an 8-char UUID suffix and
    matches the ``[a-z0-9-]+`` pattern.
  - ``create_workspace`` writes the row with the expected fields
    + a slug of the documented shape.
  - 50 workspaces sharing the same ``name`` produce 50 distinct
    slugs (the 8-char UUID suffix is the collision-avoidance
    mechanism; this test is the empirical guarantee).
  - The partial unique index ``ix_workspaces_slug_active`` blocks
    a deliberately-duplicated slug INSERT.
  - ``get_workspace`` / ``get_workspace_by_slug`` round-trip.
  - ``rename_workspace`` updates name but NOT slug — the
    load-bearing URL-stability invariant.
  - ``fork_workspace`` writes a child workspace + a
    ``workspace_variants`` row with the override summary.
  - Name validation rejects empty / whitespace / too-long /
    control-char strings.

Module-level skip when Postgres is unreachable.
"""

from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from state.workspace_repo import (  # noqa: E402
    InvalidNameError,
    UnknownWorkspaceError,
    create_workspace,
    derive_slug,
    fork_workspace,
    get_workspace,
    get_workspace_by_slug,
    rename_workspace,
    slugify,
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
        f"Postgres not reachable at {_DB_URL!r}.  Set DB_* env vars or "
        "run the CI state-layer job."
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
def wipe(engine):
    from sqlalchemy import text

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM copilot_state.workspace_variants"))
        conn.execute(text("DELETE FROM copilot_state.workspaces"))
        conn.execute(text("DELETE FROM copilot_state.dag_edges"))
        conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
        conn.execute(text("DELETE FROM copilot_state.dags"))
    yield


@pytest.fixture
def dag_hash(engine) -> str:
    """Insert a minimal placeholder DAG so workspaces can FK to it.

    We bypass ``persist_dag_from_lineage`` here so this file is
    independent of the lineage substrate — workspace_repo tests
    should not require the artifact stack to spin up."""
    from sqlalchemy import text

    h = "d" * 64
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO copilot_state.dags (hash, topology)
                VALUES (:h, CAST(:t AS JSONB))
                """
            ),
            {"h": h, "t": '{"nodes": [], "edges": []}'},
        )
    return h


# ============================================================================
# slugify — adversarial-input safety
# ============================================================================


class TestSlugifyResilience:
    @pytest.mark.parametrize("inp,expected", [
        ("tips_2y_v3", "tips-2y-v3"),
        ("UST 2s10s", "ust-2s10s"),
        ("  spaced  ", "spaced"),
        ("---leading-trailing---", "leading-trailing"),
        ("über cafe", "uber-cafe"),
        ("日本語", "ws"),         # all chars dropped -> fallback prefix
        ("", "ws"),
        (None, "ws"),
        ("   ", "ws"),
        ("; DROP TABLE workspaces;", "drop-table-workspaces"),
        ("../../../etc/passwd", "etc-passwd"),
        ("a.b.c@d.e", "a-b-c-d-e"),
    ])
    def test_known_inputs(self, inp, expected):
        assert slugify(inp) == expected

    def test_idempotent_under_repeated_application(self):
        for s in ["tips", "tips-2y-v3", "ws", "  weird  "]:
            once = slugify(s)
            twice = slugify(once)
            assert once == twice

    def test_caps_to_88_chars(self):
        # Long input — slugify should cap the name portion.
        s = slugify("a" * 500)
        assert len(s) <= 88

    def test_only_safe_chars(self):
        # Even adversarial input produces a slug-shaped string.
        s = slugify("<>{}|^`")
        assert re.fullmatch(r"[a-z0-9-]+", s)


# ============================================================================
# derive_slug — name + uuid suffix
# ============================================================================


class TestDeriveSlug:
    def test_always_8_char_uuid_suffix(self):
        u = uuid.uuid4()
        slug = derive_slug("my workspace", u)
        # Last 9 chars should be ``-`` + the 8-char hex prefix.
        assert slug.endswith("-" + u.hex[:8])

    def test_unnamed_uses_ws_prefix(self):
        u = uuid.uuid4()
        slug = derive_slug(None, u)
        assert slug == f"ws-{u.hex[:8]}"

    def test_slug_shape(self):
        u = uuid.uuid4()
        slug = derive_slug("UST 2s10s thing", u)
        assert re.fullmatch(r"[a-z0-9-]+", slug)


# ============================================================================
# create_workspace
# ============================================================================


class TestCreateWorkspace:
    def test_returns_record_with_slug(self, engine, dag_hash):
        with engine.begin() as conn:
            rec = create_workspace(
                dag_hash, conn=conn, name="tips 2y v3",
                created_by="alice",
            )
        assert rec.dag_hash == dag_hash
        assert rec.name == "tips 2y v3"
        assert rec.created_by == "alice"
        assert rec.slug.endswith("-" + rec.id.hex[:8])
        assert rec.slug.startswith("tips-2y-v3-")
        # Sanity: timestamps populated.
        assert rec.created_at is not None
        assert rec.updated_at is not None

    def test_unnamed_workspace_gets_ws_prefix(self, engine, dag_hash):
        with engine.begin() as conn:
            rec = create_workspace(dag_hash, conn=conn)
        assert rec.name is None
        assert rec.slug == f"ws-{rec.id.hex[:8]}"

    def test_fifty_same_name_produce_distinct_slugs(
        self, engine, dag_hash,
    ):
        """The 8-char UUID suffix gives a 2^32 namespace per name.
        Fifty draws should produce fifty distinct slugs in practice."""
        slugs = set()
        with engine.begin() as conn:
            for _ in range(50):
                rec = create_workspace(
                    dag_hash, conn=conn, name="duplicate name",
                )
                slugs.add(rec.slug)
        assert len(slugs) == 50

    def test_partial_unique_index_blocks_duplicate_slug(
        self, engine, dag_hash,
    ):
        """Force the same UUID twice (pin via the test-only
        ``workspace_uuid`` arg) and assert the DB rejects the second
        insert.  Proves the partial unique index from migration
        0005 actually enforces."""
        from sqlalchemy.exc import IntegrityError

        u = uuid.uuid4()
        with engine.begin() as conn:
            create_workspace(
                dag_hash, conn=conn, name="dup",
                workspace_uuid=u,
            )
        # Same UUID -> same slug -> partial-unique violation.
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                create_workspace(
                    dag_hash, conn=conn, name="dup",
                    workspace_uuid=u,
                )

    def test_dag_fk_enforced(self, engine):
        from sqlalchemy.exc import IntegrityError

        bogus = "0" * 64
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                create_workspace(bogus, conn=conn, name="x")

    @pytest.mark.parametrize("bad", [
        "",
        "   ",
        "\n\n",
        "x\x00y",
        "x\x01y",
        "x\x7fy",
        "a" * 257,  # > _MAX_DISPLAY_NAME_LEN
    ])
    def test_rejects_bad_names(self, engine, dag_hash, bad):
        with engine.begin() as conn:
            with pytest.raises(InvalidNameError):
                create_workspace(dag_hash, conn=conn, name=bad)


# ============================================================================
# get_workspace / get_workspace_by_slug
# ============================================================================


class TestGet:
    def test_get_by_id_round_trip(self, engine, dag_hash):
        with engine.begin() as conn:
            created = create_workspace(
                dag_hash, conn=conn, name="alpha",
            )
        with engine.connect() as conn:
            recovered = get_workspace(created.id, conn=conn)
        assert recovered.id == created.id
        assert recovered.slug == created.slug
        assert recovered.name == "alpha"

    def test_get_by_slug_round_trip(self, engine, dag_hash):
        with engine.begin() as conn:
            created = create_workspace(
                dag_hash, conn=conn, name="beta",
            )
        with engine.connect() as conn:
            recovered = get_workspace_by_slug(created.slug, conn=conn)
        assert recovered.id == created.id

    def test_get_by_id_unknown_raises(self, engine):
        with engine.connect() as conn:
            with pytest.raises(UnknownWorkspaceError):
                get_workspace(uuid.uuid4(), conn=conn)

    def test_get_by_slug_unknown_raises(self, engine):
        with engine.connect() as conn:
            with pytest.raises(UnknownWorkspaceError):
                get_workspace_by_slug("nonexistent-12345678", conn=conn)

    def test_get_by_slug_rejects_malformed(self, engine):
        with engine.connect() as conn:
            with pytest.raises(UnknownWorkspaceError):
                get_workspace_by_slug("HAS UPPERCASE", conn=conn)
            with pytest.raises(UnknownWorkspaceError):
                get_workspace_by_slug("has.dot", conn=conn)


# ============================================================================
# rename_workspace — the URL-stability guarantee
# ============================================================================


class TestRename:
    def test_rename_changes_name_not_slug(self, engine, dag_hash):
        """The load-bearing URL-stability invariant: after a rename,
        the OLD slug still resolves to this workspace."""
        with engine.begin() as conn:
            created = create_workspace(
                dag_hash, conn=conn, name="original name",
            )
        original_slug = created.slug

        with engine.begin() as conn:
            renamed = rename_workspace(
                created.id, "new name entirely different", conn=conn,
            )
        assert renamed.id == created.id
        assert renamed.name == "new name entirely different"
        assert renamed.slug == original_slug, (
            "rename must NOT change the slug; the URL is the load-bearing "
            "handle"
        )

        # Final proof: lookup by the original slug still succeeds.
        with engine.connect() as conn:
            via_old_slug = get_workspace_by_slug(original_slug, conn=conn)
        assert via_old_slug.id == created.id
        assert via_old_slug.name == "new name entirely different"

    def test_rename_to_none_allowed(self, engine, dag_hash):
        with engine.begin() as conn:
            created = create_workspace(
                dag_hash, conn=conn, name="named",
            )
        with engine.begin() as conn:
            renamed = rename_workspace(
                created.id, None, conn=conn,
            )
        assert renamed.name is None
        assert renamed.slug == created.slug

    def test_rename_unknown_raises(self, engine):
        with engine.begin() as conn:
            with pytest.raises(UnknownWorkspaceError):
                rename_workspace(uuid.uuid4(), "x", conn=conn)

    def test_rename_validates_name(self, engine, dag_hash):
        with engine.begin() as conn:
            created = create_workspace(dag_hash, conn=conn, name="ok")
        with engine.begin() as conn:
            with pytest.raises(InvalidNameError):
                rename_workspace(created.id, "  ", conn=conn)


# ============================================================================
# fork_workspace
# ============================================================================


class TestFork:
    def test_fork_creates_child_with_parent_link(self, engine, dag_hash):
        from sqlalchemy import text

        # Same dag for parent + child; in production the variant_dag_hash
        # would be a sibling DAG with overrides applied.
        with engine.begin() as conn:
            parent = create_workspace(
                dag_hash, conn=conn, name="parent",
            )
        with engine.begin() as conn:
            child = fork_workspace(
                parent.id,
                conn=conn,
                variant_dag_hash=dag_hash,
                override_summary={"changed": ["conventions.x"]},
                name="parent forked",
                created_by="bob",
            )
        assert child.parent_workspace_id == parent.id
        assert child.slug.startswith("parent-forked-")
        assert child.created_by == "bob"

        # A workspace_variants row was written.
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    """
                    SELECT workspace_id, override_summary
                    FROM copilot_state.workspace_variants
                    WHERE workspace_id = :id
                    """
                ),
                {"id": child.id},
            ).mappings().all()
        assert len(rows) == 1
        assert rows[0]["override_summary"] == {
            "changed": ["conventions.x"],
        }

    def test_fork_unknown_parent_raises(self, engine, dag_hash):
        with engine.begin() as conn:
            with pytest.raises(UnknownWorkspaceError):
                fork_workspace(
                    uuid.uuid4(),
                    conn=conn,
                    variant_dag_hash=dag_hash,
                    override_summary={},
                )

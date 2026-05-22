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
    list_workspaces,
    rename_workspace,
    slugify,
    touch_workspace_access,
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


# ============================================================================
# list_workspaces + touch_workspace_access — sidebar list surface (PR A)
# ============================================================================


class TestListWorkspaces:
    def test_empty_returns_empty_list(self, engine):
        """No workspaces in the table → list returns []."""
        with engine.connect() as conn:
            out = list_workspaces(conn=conn)
        assert out == []

    def test_recent_orders_by_last_accessed_then_updated(
        self, engine, dag_hash,
    ):
        """``recent`` filter uses COALESCE(last_accessed, updated)
        DESC, so a freshly-touched older workspace beats a younger
        never-opened one."""
        from sqlalchemy import text

        # Create three workspaces in temporal order: A → B → C.
        with engine.begin() as conn:
            a = create_workspace(dag_hash, conn=conn, name="A workspace")
        with engine.begin() as conn:
            b = create_workspace(dag_hash, conn=conn, name="B workspace")
        with engine.begin() as conn:
            c = create_workspace(dag_hash, conn=conn, name="C workspace")

        # Touch ``A`` last — its last_accessed_at jumps to ``now()``,
        # so the recent ordering should be ``A, C, B`` (A by
        # last_accessed, C and B by updated_at descending).
        with engine.begin() as conn:
            touch_workspace_access(a.slug, conn=conn)

        with engine.connect() as conn:
            recent = list_workspaces(conn=conn, filter="recent")
        ids = [w.id for w in recent]
        assert ids[0] == a.id, "Touched workspace must lead the recent list"
        assert b.id in ids and c.id in ids

    def test_all_orders_by_updated_at(self, engine, dag_hash):
        """``all`` ignores last_accessed_at; pure updated_at DESC."""
        with engine.begin() as conn:
            a = create_workspace(dag_hash, conn=conn, name="alpha")
        with engine.begin() as conn:
            b = create_workspace(dag_hash, conn=conn, name="bravo")
        with engine.begin() as conn:
            # Touch alpha — would float it to top under ``recent`` but
            # NOT under ``all``.
            touch_workspace_access(a.slug, conn=conn)

        with engine.connect() as conn:
            ordered = list_workspaces(conn=conn, filter="all")
        # bravo created after alpha, so under updated_at-only it
        # remains in front despite alpha being touched.
        assert ordered[0].id == b.id
        assert ordered[1].id == a.id

    def test_limit_clamped(self, engine, dag_hash):
        """``limit`` is clamped to [1, 200] server-side."""
        with engine.begin() as conn:
            for i in range(5):
                create_workspace(dag_hash, conn=conn, name=f"ws-{i}")

        with engine.connect() as conn:
            # Way too large: clamped to 200, so all 5 still come back.
            out_big = list_workspaces(conn=conn, limit=10_000)
            # Way too small: clamped up to 1.
            out_zero = list_workspaces(conn=conn, limit=0)
            # Negative: clamped to 1.
            out_neg = list_workspaces(conn=conn, limit=-3)

        assert len(out_big) == 5
        assert len(out_zero) == 1
        assert len(out_neg) == 1

    def test_offset_paginates(self, engine, dag_hash):
        """``offset`` skips that many rows; cursor pagination works."""
        with engine.begin() as conn:
            for i in range(6):
                create_workspace(dag_hash, conn=conn, name=f"ws-{i:02d}")

        with engine.connect() as conn:
            page_a = list_workspaces(conn=conn, limit=3, offset=0)
            page_b = list_workspaces(conn=conn, limit=3, offset=3)

        page_a_ids = {w.id for w in page_a}
        page_b_ids = {w.id for w in page_b}
        assert len(page_a) == 3 and len(page_b) == 3
        assert page_a_ids.isdisjoint(page_b_ids), (
            "Pagination pages overlap — limit/offset is broken"
        )

    def test_unknown_filter_falls_through_to_recent(
        self, engine, dag_hash,
    ):
        """Forward-compat keys (``pinned`` / ``shared``) and unknown
        strings degrade gracefully to recent ordering rather than
        raising — the sidebar's section taxonomy is allowed to be
        ahead of the substrate."""
        with engine.begin() as conn:
            create_workspace(dag_hash, conn=conn, name="ws-1")

        with engine.connect() as conn:
            out_pinned = list_workspaces(conn=conn, filter="pinned")
            out_unknown = list_workspaces(conn=conn, filter="weird-future-key")
            out_recent = list_workspaces(conn=conn, filter="recent")

        # All three return the same set (one row in the table).
        assert {w.id for w in out_pinned} == {w.id for w in out_recent}
        assert {w.id for w in out_unknown} == {w.id for w in out_recent}


class TestTouchWorkspaceAccess:
    def test_touch_known_slug_updates_last_accessed(
        self, engine, dag_hash,
    ):
        from sqlalchemy import text

        with engine.begin() as conn:
            ws = create_workspace(dag_hash, conn=conn, name="touchable")

        with engine.connect() as conn:
            before = conn.execute(
                text(
                    """
                    SELECT last_accessed_at FROM copilot_state.workspaces
                    WHERE id = :id
                    """
                ),
                {"id": ws.id},
            ).scalar()
        assert before is None  # never opened

        with engine.begin() as conn:
            touch_workspace_access(ws.slug, conn=conn)

        with engine.connect() as conn:
            after = conn.execute(
                text(
                    """
                    SELECT last_accessed_at FROM copilot_state.workspaces
                    WHERE id = :id
                    """
                ),
                {"id": ws.id},
            ).scalar()
        assert after is not None
        assert after > _now_minus(minutes=1)

    def test_touch_unknown_slug_is_noop(self, engine, dag_hash):
        """Touching a slug that doesn't exist must NOT raise — the
        route handler relies on this so a stale URL is observable
        as a 404 from the read path, not a 500 from the write."""
        with engine.begin() as conn:
            # Should not raise.
            touch_workspace_access("nonexistent-deadbeef", conn=conn)

    def test_touch_malformed_slug_is_noop(self, engine, dag_hash):
        """Slug shape validation rejects malformed input without
        touching the DB — keeps SQLi vectors out of the UPDATE
        regardless of param binding."""
        with engine.begin() as conn:
            touch_workspace_access("SELECT * FROM workspaces", conn=conn)
            touch_workspace_access("", conn=conn)
            touch_workspace_access(None, conn=conn)  # type: ignore[arg-type]


# ----------------------------------------------------------------------------
# Test helpers (only used by the touch tests above)
# ----------------------------------------------------------------------------


def _now_minus(*, minutes: int):
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) - timedelta(minutes=minutes)

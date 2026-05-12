"""tests/state/test_methodology_versions.py — substrate tests for the
methodology / application-version registries.

Phase 0 PR 9.

Tests cover:

  - ``register_yaml`` is idempotent under content (whitespace,
    key-order, comment formatting do NOT create new rows).
  - ``register_yaml`` DOES create a new row when semantically
    different content arrives.
  - ``get_yaml`` round-trips the parsed dict losslessly.
  - In-process cache shortcuts the DB on second call.
  - ``register_application_version`` is idempotent on git_commit.
  - SHA validation rejects malformed strings.
  - ``current_application_version_id`` resolves to a real
    application_version row.

Real-Postgres integration tests; module-level skip when DB is
unreachable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from state.methodology_versions import (  # noqa: E402
    canonicalize_yaml_content,
    clear_caches,
    current_application_version_id,
    get_application_version,
    get_yaml,
    get_yaml_by_hash,
    register_application_version,
    register_yaml,
    _UNKNOWN_COMMIT_SENTINEL,
    _hash_yaml_content,
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
        "the CI state-layer job."
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
    """Clean methodology_versions + application_version per test.

    Drop in FK-safe order: artifact_metadata references both, so the
    ``application_version_id`` FK and the ``methodology_version_ids``
    array must be cleared from any rows first.  Other state tables
    that PR 8 populates also need to go.
    """
    from sqlalchemy import text

    clear_caches()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
        conn.execute(text("DELETE FROM copilot_state.message_events"))
        conn.execute(text("DELETE FROM copilot_state.turns"))
        conn.execute(text("DELETE FROM copilot_state.sessions"))
        conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
        conn.execute(text("DELETE FROM copilot_state.methodology_versions"))
        conn.execute(text("DELETE FROM copilot_state.application_version"))
    yield


# ============================================================================
# YAML registry
# ============================================================================


_YAML_V1 = """\
tool:
  name: zscore_custom
  domain: ois
  description: Custom z-score
conventions:
  z_score_window_days:
    value: 252
    source: industry_standard_1y_window
    rationale: 252 trading days = ~1 year of business days
methodology:
  what_it_does: Computes z-score of a series over a rolling window
"""

_YAML_V1_REFORMATTED = """\
# A comment that should not affect identity
methodology:
  what_it_does: Computes z-score of a series over a rolling window
tool:
  description: Custom z-score
  domain: ois
  name: zscore_custom

conventions:
  z_score_window_days:
    rationale: 252 trading days = ~1 year of business days
    source: industry_standard_1y_window
    value: 252
"""

_YAML_V2_VALUE_CHANGED = """\
tool:
  name: zscore_custom
  domain: ois
  description: Custom z-score
conventions:
  z_score_window_days:
    value: 200
    source: industry_standard_1y_window
    rationale: 252 trading days = ~1 year of business days
methodology:
  what_it_does: Computes z-score of a series over a rolling window
"""


class TestRegisterYaml:
    def test_round_trip_returns_id(self, engine):
        with engine.begin() as conn:
            vid = register_yaml(
                _YAML_V1, yaml_path="/tmp/v1.yml", conn=conn,
            )
            rec = get_yaml(vid, conn=conn)
        assert rec.id == vid
        assert rec.yaml_path == "/tmp/v1.yml"
        # Parsed content survives the JSON round-trip.
        assert rec.yaml_content["tool"]["name"] == "zscore_custom"
        assert (
            rec.yaml_content["conventions"]["z_score_window_days"]["value"]
            == 252
        )

    def test_idempotent_under_reformatting(self, engine):
        """The same YAML content in two different surface forms
        registers to the SAME row."""
        with engine.begin() as conn:
            v1 = register_yaml(
                _YAML_V1, yaml_path="/tmp/v1.yml", conn=conn,
            )
            v2 = register_yaml(
                _YAML_V1_REFORMATTED, yaml_path="/tmp/v1_alt.yml", conn=conn,
            )
        assert v1 == v2

    def test_new_row_on_value_change(self, engine):
        with engine.begin() as conn:
            v1 = register_yaml(
                _YAML_V1, yaml_path="/tmp/v1.yml", conn=conn,
            )
            v2 = register_yaml(
                _YAML_V2_VALUE_CHANGED,
                yaml_path="/tmp/v2.yml", conn=conn,
            )
        assert v1 != v2

    def test_in_process_cache_short_circuits(self, engine):
        from sqlalchemy import text

        with engine.begin() as conn:
            register_yaml(_YAML_V1, yaml_path="/tmp/v1.yml", conn=conn)
            row_count_after_first = conn.execute(
                text(
                    "SELECT COUNT(*) FROM copilot_state.methodology_versions"
                )
            ).scalar_one()
        # Second registration of identical content should NOT add a row.
        with engine.begin() as conn:
            register_yaml(_YAML_V1, yaml_path="/tmp/elsewhere.yml", conn=conn)
            row_count_after_second = conn.execute(
                text(
                    "SELECT COUNT(*) FROM copilot_state.methodology_versions"
                )
            ).scalar_one()
        assert row_count_after_first == row_count_after_second == 1

    def test_rejects_non_dict_yaml(self, engine):
        with engine.begin() as conn:
            with pytest.raises(ValueError):
                register_yaml(
                    "- not\n- a\n- mapping",
                    yaml_path="/tmp/list.yml", conn=conn,
                )

    def test_rejects_empty_yaml(self, engine):
        with engine.begin() as conn:
            with pytest.raises(ValueError):
                register_yaml(
                    "", yaml_path="/tmp/empty.yml", conn=conn,
                )

    def test_get_yaml_by_hash_finds_existing(self, engine):
        with engine.begin() as conn:
            vid = register_yaml(
                _YAML_V1, yaml_path="/tmp/v1.yml", conn=conn,
            )
            rec = get_yaml(vid, conn=conn)
        with engine.connect() as conn:
            found = get_yaml_by_hash(rec.yaml_content_hash, conn=conn)
        assert found is not None
        assert found.id == vid

    def test_get_yaml_by_hash_returns_none_for_missing(self, engine):
        with engine.connect() as conn:
            assert get_yaml_by_hash("0" * 64, conn=conn) is None

    def test_get_yaml_unknown_id_raises(self, engine):
        with engine.connect() as conn:
            with pytest.raises(KeyError):
                get_yaml(999_999, conn=conn)


class TestCanonicalize:
    def test_whitespace_invariant_hash(self):
        a = {"a": 1, "b": {"c": 2}}
        b = {"b": {"c": 2}, "a": 1}
        ha = _hash_yaml_content(canonicalize_yaml_content(a))
        hb = _hash_yaml_content(canonicalize_yaml_content(b))
        assert ha == hb

    def test_semantic_change_differs(self):
        a = {"a": 1, "b": 2}
        b = {"a": 1, "b": 3}
        assert _hash_yaml_content(canonicalize_yaml_content(a)) != \
            _hash_yaml_content(canonicalize_yaml_content(b))


# ============================================================================
# application_version registry
# ============================================================================


class TestRegisterApplicationVersion:
    def test_idempotent_under_commit(self, engine):
        commit = "a" * 40
        with engine.begin() as conn:
            id1 = register_application_version(commit, conn=conn)
        with engine.begin() as conn:
            id2 = register_application_version(commit, conn=conn)
        assert id1 == id2

    def test_different_commits_different_ids(self, engine):
        with engine.begin() as conn:
            id1 = register_application_version("a" * 40, conn=conn)
            id2 = register_application_version("b" * 40, conn=conn)
        assert id1 != id2

    @pytest.mark.parametrize("bad", [
        "",
        "a" * 39,
        "a" * 41,
        "g" * 40,  # non-hex
        "A" * 40 + "X",  # too long
    ])
    def test_rejects_malformed_commit(self, engine, bad):
        with engine.begin() as conn:
            with pytest.raises(ValueError):
                register_application_version(bad, conn=conn)

    def test_get_application_version_round_trip(self, engine):
        commit = "0" + "f" * 39
        with engine.begin() as conn:
            vid = register_application_version(commit, conn=conn)
            rec = get_application_version(vid, conn=conn)
        assert rec.git_commit == commit
        assert rec.id == vid

    def test_current_application_version_id_returns_real_row(self, engine):
        """``current_application_version_id`` resolves SOMETHING (the
        real HEAD when run from a checkout, the unknown-sentinel
        otherwise) and registers it.  Either way the returned id
        corresponds to a real row."""
        with engine.begin() as conn:
            vid = current_application_version_id(conn=conn)
            rec = get_application_version(vid, conn=conn)
        assert rec.id == vid
        # Either a real 40-hex git SHA OR the sentinel.
        assert len(rec.git_commit) == 40
        # Must be lowercase hex (or the sentinel's 'u' chars).
        assert rec.git_commit == _UNKNOWN_COMMIT_SENTINEL or all(
            c in "0123456789abcdef" for c in rec.git_commit
        )

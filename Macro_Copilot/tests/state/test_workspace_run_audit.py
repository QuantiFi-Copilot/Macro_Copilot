"""tests/state/test_workspace_run_audit.py — Phase D / D9 lock tests.

The open-DAG intent-audit chain persists as the NON-HASHED
``workspaces.run_audit`` sidecar (migration 0010) so the build page
can render "what I understood / checked / fixed".  Three layers
pinned here:

  1. ``create_workspace(run_audit=...)`` round-trips the blob through
     INSERT → SELECT (by id and by slug) byte-faithfully, and NULL
     stays NULL (legacy rows + non-audit lanes).
  2. P4 safety — persisting an audit blob does not touch the
     workspace's ``dag_hash`` (the content-addressed identity the
     sidecar must never contaminate).
  3. ``orchestrator.session._build_run_audit`` — the payload builder:
     versioned shape, IntentChain dump, expected_answer_shape value
     coercion, recompose_trace dump; ``None`` when the outcome has no
     chain; never raises (best-effort sidecar).
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from state.workspace_repo import (  # noqa: E402
    create_workspace,
    get_workspace,
    get_workspace_by_slug,
)


# ============================================================================
# DB availability (same convention as test_workspace_repo.py)
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
        "start the docker-compose tsdb service."
    ),
)


@pytest.fixture()
def engine():
    from sqlalchemy import create_engine

    e = create_engine(_DB_URL)
    yield e
    e.dispose()


@pytest.fixture()
def dag_hash(engine):
    """Insert a minimal parent DAG row (workspaces.dag_hash is FK →
    dags.hash, ON DELETE RESTRICT) and clean it + its workspaces up."""
    from sqlalchemy import text

    # ``ck_dags_hash_len`` — dag hashes are 64-char (sha256 hex).
    h = uuid.uuid4().hex + uuid.uuid4().hex
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO copilot_state.dags (hash, topology) "
                "VALUES (:h, CAST(:topology AS JSONB))"
            ),
            {"h": h, "topology": '{"nodes": [], "edges": []}'},
        )
    yield h
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM copilot_state.workspaces WHERE dag_hash = :h"),
            {"h": h},
        )
        conn.execute(
            text("DELETE FROM copilot_state.dags WHERE hash = :h"),
            {"h": h},
        )


_AUDIT = {
    "schema_version": 1,
    "intent_chain": {
        "user_prompt": "average US 2s10s over 5y",
        "gate": {"status": "PASS", "reason": "clean coverage"},
    },
    "expected_answer_shape": "scalar",
    "recompose_trace": [
        {
            "attempt_index": 1,
            "failed_status": "ASSEMBLY_REFUSE",
            "reason": "terminal shape mismatch: Series vs scalar",
        },
    ],
}


# ============================================================================
# 1 + 2 — repo round-trip + P4 safety
# ============================================================================


class TestRunAuditRoundTrip:
    def test_round_trip_by_id_and_slug(self, engine, dag_hash):
        with engine.begin() as conn:
            ws = create_workspace(
                dag_hash, conn=conn, name="audit rt", run_audit=_AUDIT,
            )
        assert ws.run_audit == _AUDIT
        with engine.connect() as conn:
            by_id = get_workspace(ws.id, conn=conn)
            by_slug = get_workspace_by_slug(ws.slug, conn=conn)
        assert by_id.run_audit == _AUDIT
        assert by_slug.run_audit == _AUDIT

    def test_null_stays_null(self, engine, dag_hash):
        with engine.begin() as conn:
            ws = create_workspace(dag_hash, conn=conn, name="no audit")
        assert ws.run_audit is None
        with engine.connect() as conn:
            assert get_workspace(ws.id, conn=conn).run_audit is None

    def test_sidecar_never_touches_dag_hash(self, engine, dag_hash):
        # P4 — the audit blob is referencing metadata; the workspace
        # points at the SAME content-addressed DAG with or without it.
        with engine.begin() as conn:
            with_audit = create_workspace(
                dag_hash, conn=conn, name="a", run_audit=_AUDIT,
            )
            without_audit = create_workspace(
                dag_hash, conn=conn, name="b",
            )
        assert with_audit.dag_hash == dag_hash
        assert without_audit.dag_hash == dag_hash


# ============================================================================
# 3 — the session-side payload builder
# ============================================================================


class TestBuildRunAudit:
    def _outcome(self, **overrides):
        from orchestrator.session import _build_run_audit  # noqa: F401

        class _Chain:
            def model_dump(self, mode="json"):
                return {"user_prompt": "p", "gate": {"status": "PASS"}}

        class _Step:
            def model_dump(self, mode="json"):
                return {
                    "attempt_index": 1,
                    "failed_status": "GATE_REFUSE",
                    "reason": "r",
                }

        base = dict(
            intent_chain=_Chain(),
            route_decision=SimpleNamespace(expected_answer_shape="scalar"),
            recompose_trace=(_Step(),),
        )
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_builds_versioned_payload(self):
        from orchestrator.session import _build_run_audit

        audit = _build_run_audit(self._outcome())
        assert audit is not None
        assert audit["schema_version"] == 1
        assert audit["intent_chain"]["user_prompt"] == "p"
        assert audit["expected_answer_shape"] == "scalar"
        assert audit["recompose_trace"] == [
            {
                "attempt_index": 1,
                "failed_status": "GATE_REFUSE",
                "reason": "r",
            },
        ]

    def test_enum_shape_coerced_to_value(self):
        from enum import Enum

        from orchestrator.session import _build_run_audit

        class _Shape(str, Enum):
            SCALAR = "scalar"

        audit = _build_run_audit(
            self._outcome(
                route_decision=SimpleNamespace(
                    expected_answer_shape=_Shape.SCALAR,
                ),
            ),
        )
        assert audit is not None
        assert audit["expected_answer_shape"] == "scalar"

    def test_no_chain_returns_none(self):
        from orchestrator.session import _build_run_audit

        assert _build_run_audit(self._outcome(intent_chain=None)) is None

    def test_serialization_failure_returns_none_not_raise(self):
        from orchestrator.session import _build_run_audit

        class _Broken:
            def model_dump(self, mode="json"):
                raise RuntimeError("boom")

        audit = _build_run_audit(self._outcome(intent_chain=_Broken()))
        assert audit is None

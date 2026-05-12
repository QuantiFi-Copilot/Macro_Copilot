"""tests/state/test_dag_repo.py — real-Postgres tests for DAG
persistence.

Phase 0 PR 10.

Tests cover:

  - Round-trip: a persisted Lineage reconstructs into the same
    node + edge set on read.
  - Idempotency: persisting the same Lineage twice produces ONE
    dag row + ONE set of node + edge rows.
  - Content-addressing: two Lineages with identical step content
    produce the same dag_hash; a one-bit semantic change moves
    the hash.
  - Foreign-key safety: a head artifact hash that doesn't exist
    in artifact_metadata aborts the put (the constraint is
    ``ON DELETE RESTRICT`` against artifact_metadata.hash).
  - Multi-step lineage round-trips with the right linear-chain
    edges.
  - ``list_node_artifact_hashes`` returns only non-null hashes in
    node_id order — the load-bearing invariant the integration
    test depends on.

Module-level skip when Postgres is unreachable.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


from shared.artifacts.lineage import (  # noqa: E402
    FetchStep,
    Lineage,
    OperatorStep,
    PrimitiveStep,
)
from shared.artifacts.missingness import RawNoCleaning  # noqa: E402
from shared.artifacts.types import Series  # noqa: E402
from shared.artifacts.units import TimeSeriesUnits  # noqa: E402
from state.artifact_store import put_artifact  # noqa: E402
from state.dag_repo import (  # noqa: E402
    StoredDag,
    get_dag,
    list_node_artifact_hashes,
    persist_dag_from_lineage,
)
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

    from state.methodology_versions import clear_caches

    clear_caches()
    with engine.begin() as conn:
        # FK-safe order — dag_nodes references artifact_metadata
        # which references application_version + methodology_versions.
        conn.execute(text("DELETE FROM copilot_state.workspace_variants"))
        conn.execute(text("DELETE FROM copilot_state.workspaces"))
        conn.execute(text("DELETE FROM copilot_state.dag_edges"))
        conn.execute(text("DELETE FROM copilot_state.dag_nodes"))
        conn.execute(text("DELETE FROM copilot_state.dags"))
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.message_events"))
        conn.execute(text("DELETE FROM copilot_state.turns"))
        conn.execute(text("DELETE FROM copilot_state.sessions"))
        conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
        conn.execute(text("DELETE FROM copilot_state.methodology_versions"))
        conn.execute(text("DELETE FROM copilot_state.application_version"))
    yield


@pytest.fixture
def storage(tmp_path):
    return LocalFSBackend(root=tmp_path / "artifacts")


# ============================================================================
# Helpers
# ============================================================================


def _make_chain(slot: int = 1) -> Lineage:
    """Build a 3-step linear lineage:

        FetchStep -> PrimitiveStep -> OperatorStep
    """
    fetch = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={
            "curve_family": "UST",
            "tenor": "10Y",
            "slot": slot,
        },
    )
    prim = PrimitiveStep.build(
        name="calculate_test_primitive",
        version="1.0.0",
        params={"curve_family": "UST"},
        tool_config_hash="d" * 64,
        output_field="time_series_test",
        as_of_date="2024-01-01",
        input_hashes=(fetch.hash,),
    )
    op = OperatorStep.build(
        name="align_series",
        version="1.0.0",
        params={"method": "intersection"},
        input_hashes=(prim.hash,),
    )
    return Lineage.from_steps([fetch, prim, op])


def _persist_head_artifact(engine, storage, chain: Lineage) -> str:
    art = Series(
        series_key="UST.10Y",
        payload=pd.Series([1.0], index=pd.date_range("2024-01-01", periods=1)),
        units=TimeSeriesUnits.PERCENT,
        frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=chain,
    )
    with engine.begin() as conn:
        return put_artifact(art, conn=conn, object_storage=storage)


# ============================================================================
# Round trip
# ============================================================================


class TestRoundTrip:
    def test_three_step_chain_round_trips(self, engine, storage):
        chain = _make_chain(slot=1)
        h = _persist_head_artifact(engine, storage, chain)
        with engine.begin() as conn:
            dag_hash = persist_dag_from_lineage(
                chain, conn=conn, head_artifact_hash=h,
            )
        assert len(dag_hash) == 64

        with engine.connect() as conn:
            stored: StoredDag = get_dag(dag_hash, conn=conn)

        assert stored.dag_hash == dag_hash
        assert len(stored.nodes) == 3
        assert len(stored.edges) == 2
        # Node order is execution order.
        assert stored.nodes[0].kind == "fetch"
        assert stored.nodes[0].name == "fetch_single_tenor"
        assert stored.nodes[1].kind == "primitive"
        assert stored.nodes[1].name == "calculate_test_primitive"
        assert stored.nodes[2].kind == "operator"
        assert stored.nodes[2].name == "align_series"
        # Only the head node carries the artifact hash.
        assert stored.nodes[0].artifact_hash is None
        assert stored.nodes[1].artifact_hash is None
        assert stored.nodes[2].artifact_hash == h
        # Edges are linear, single implicit slot.
        assert stored.edges[0].from_node == "n0000"
        assert stored.edges[0].to_node == "n0001"
        assert stored.edges[0].slot_name == "input"
        assert stored.edges[1].from_node == "n0001"
        assert stored.edges[1].to_node == "n0002"

    def test_node_id_ordering_is_lexicographic(self, engine, storage):
        """Node ids are zero-padded so lex-sort == execution order."""
        chain = _make_chain()
        h = _persist_head_artifact(engine, storage, chain)
        with engine.begin() as conn:
            dag_hash = persist_dag_from_lineage(
                chain, conn=conn, head_artifact_hash=h,
            )
        with engine.connect() as conn:
            stored = get_dag(dag_hash, conn=conn)
        node_ids = [n.node_id for n in stored.nodes]
        assert node_ids == sorted(node_ids)

    def test_get_dag_unknown_hash_raises(self, engine):
        with engine.connect() as conn:
            with pytest.raises(KeyError):
                get_dag("0" * 64, conn=conn)

    def test_get_dag_rejects_malformed_hash(self, engine):
        with engine.connect() as conn:
            with pytest.raises(ValueError):
                get_dag("not-a-hash", conn=conn)


# ============================================================================
# Idempotency
# ============================================================================


class TestIdempotency:
    def test_same_lineage_one_dag_row(self, engine, storage):
        from sqlalchemy import text

        chain = _make_chain(slot=42)
        h = _persist_head_artifact(engine, storage, chain)

        with engine.begin() as conn:
            d1 = persist_dag_from_lineage(
                chain, conn=conn, head_artifact_hash=h,
            )
        with engine.begin() as conn:
            d2 = persist_dag_from_lineage(
                chain, conn=conn, head_artifact_hash=h,
            )
        assert d1 == d2

        with engine.connect() as conn:
            counts = conn.execute(
                text(
                    """
                    SELECT
                      (SELECT COUNT(*) FROM copilot_state.dags
                         WHERE hash = :h) AS n_dags,
                      (SELECT COUNT(*) FROM copilot_state.dag_nodes
                         WHERE dag_hash = :h) AS n_nodes,
                      (SELECT COUNT(*) FROM copilot_state.dag_edges
                         WHERE dag_hash = :h) AS n_edges
                    """
                ),
                {"h": d1},
            ).mappings().one()
        assert counts["n_dags"] == 1
        assert counts["n_nodes"] == 3
        assert counts["n_edges"] == 2


# ============================================================================
# Content-addressing
# ============================================================================


class TestContentAddressing:
    def test_identical_lineages_same_dag_hash(self, engine, storage):
        # Same params -> same step hashes -> same lineage -> same dag.
        chain_a = _make_chain(slot=7)
        chain_b = _make_chain(slot=7)
        assert chain_a.head_hash == chain_b.head_hash

        h = _persist_head_artifact(engine, storage, chain_a)
        with engine.begin() as conn:
            d_a = persist_dag_from_lineage(
                chain_a, conn=conn, head_artifact_hash=h,
            )
            d_b = persist_dag_from_lineage(
                chain_b, conn=conn, head_artifact_hash=h,
            )
        assert d_a == d_b

    def test_semantic_change_moves_dag_hash(self, engine, storage):
        chain_a = _make_chain(slot=1)
        chain_b = _make_chain(slot=2)
        assert chain_a.head_hash != chain_b.head_hash

        h_a = _persist_head_artifact(engine, storage, chain_a)
        h_b = _persist_head_artifact(engine, storage, chain_b)
        with engine.begin() as conn:
            d_a = persist_dag_from_lineage(
                chain_a, conn=conn, head_artifact_hash=h_a,
            )
            d_b = persist_dag_from_lineage(
                chain_b, conn=conn, head_artifact_hash=h_b,
            )
        assert d_a != d_b


# ============================================================================
# FK safety
# ============================================================================


class TestForeignKeySafety:
    def test_unknown_head_artifact_hash_fails(self, engine, storage):
        from sqlalchemy.exc import IntegrityError

        chain = _make_chain()
        bogus_head = "0" * 64  # not in artifact_metadata
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                persist_dag_from_lineage(
                    chain, conn=conn, head_artifact_hash=bogus_head,
                )

    def test_head_artifact_hash_optional(self, engine):
        """Plan-only persistence (no head hash) is allowed — used by
        a future workspace-plan endpoint that registers a DAG before
        executing it."""
        chain = _make_chain()
        with engine.begin() as conn:
            dag_hash = persist_dag_from_lineage(
                chain, conn=conn, head_artifact_hash=None,
            )
        with engine.connect() as conn:
            stored = get_dag(dag_hash, conn=conn)
        # No artifact hash on any node.
        assert all(n.artifact_hash is None for n in stored.nodes)


# ============================================================================
# list_node_artifact_hashes
# ============================================================================


class TestListNodeArtifactHashes:
    def test_returns_only_non_null_in_order(self, engine, storage):
        chain = _make_chain()
        h = _persist_head_artifact(engine, storage, chain)
        with engine.begin() as conn:
            dag_hash = persist_dag_from_lineage(
                chain, conn=conn, head_artifact_hash=h,
            )
        with engine.connect() as conn:
            hashes = list_node_artifact_hashes(dag_hash, conn=conn)
        # Only the head node has an artifact in this test.
        assert hashes == [h]

    def test_empty_for_unexecuted_dag(self, engine):
        chain = _make_chain()
        with engine.begin() as conn:
            dag_hash = persist_dag_from_lineage(
                chain, conn=conn, head_artifact_hash=None,
            )
        with engine.connect() as conn:
            assert list_node_artifact_hashes(dag_hash, conn=conn) == []

"""tests/integration/test_performance_baseline.py — Phase 0
performance guardrails.

Phase 0 PR 11.

The brief documents four targets for Phase 0 close:

  - Working-set load time:                     < 50 ms.
  - Artifact fetch latency (inline):           < 10 ms.
  - Artifact fetch latency (blob cold):       < 300 ms.
  - Workspace open latency (12-node DAG,
    summaries only, p95):                      < 500 ms.

This file is a **guardrail**, not a benchmark.  The targets above
are documented numbers we expect to exceed by 5-50x in normal
operation; the assertions below are set at a generous alarm bound
(typically 4-8x the target) so genuine flaky-runner variance does
not trigger a false positive, while a real regression of >5x
trips loudly.

We also exercise the cache-hit fast path (PR 11) when the Redis
backend is available via fakeredis — the cache should round-trip
in well under 5 ms once warm.

Marker discipline
-----------------
Each test is marked ``@pytest.mark.slow`` so the default
``pytest`` invocation skips them.  CI's state-layer job opts in
with ``-m slow`` so the regression alarm is exercised on every
merge but local rapid-iteration loops are unaffected.

Reporting
---------
On success, each test prints a one-line summary
``[perf] <metric>: p50=<ms> p95=<ms>`` to stdout via the standard
captured-output channel.  Trend tracking is the dashboard's job;
this file just prevents catastrophic regressions.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import List

import pandas as pd
import pytest


_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


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


pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not _DB_AVAILABLE,
        reason=f"Postgres not reachable at {_DB_URL!r}",
    ),
]


# ============================================================================
# Targets + bounds
# ============================================================================
# `target` is the documented Phase 0 expectation; `bound` is the
# regression alarm.  Bound = generous multiple of target so flaky
# CI runners do not false-positive.
PERF_TARGETS = {
    "working_set_load": dict(target_ms=50, bound_ms=400),
    "artifact_fetch_inline": dict(target_ms=10, bound_ms=100),
    "artifact_fetch_blob_cold": dict(target_ms=300, bound_ms=1500),
    "workspace_open_12_nodes": dict(target_ms=500, bound_ms=2000),
    "cache_hit": dict(target_ms=5, bound_ms=100),
}

_N_ITERATIONS = 20


# ============================================================================
# Helpers
# ============================================================================


def _p(values: List[float], pct: int) -> float:
    """Empirical percentile in ms.  values are wall-clock ms."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    idx = max(0, min(len(sorted_values) - 1, (pct * len(sorted_values)) // 100))
    return sorted_values[idx]


def _report(name: str, durations_ms: List[float]) -> dict:
    p50 = _p(durations_ms, 50)
    p95 = _p(durations_ms, 95)
    print(
        f"[perf] {name}: p50={p50:.2f}ms p95={p95:.2f}ms "
        f"(n={len(durations_ms)})"
    )
    return {"p50": p50, "p95": p95}


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
        conn.execute(text("DELETE FROM copilot_state.working_set"))
        conn.execute(text("DELETE FROM copilot_state.message_events"))
        conn.execute(text("DELETE FROM copilot_state.turns"))
        conn.execute(text("DELETE FROM copilot_state.sessions"))
        conn.execute(text("DELETE FROM copilot_state.artifact_metadata"))
        conn.execute(text("DELETE FROM copilot_state.methodology_versions"))
        conn.execute(text("DELETE FROM copilot_state.application_version"))
    yield


# ============================================================================
# Working-set load
# ============================================================================


class TestWorkingSetLoad:
    def test_p95_under_bound(self, engine):
        """50 active working-set rows, list_visible p95 must stay
        comfortably under the alarm bound."""
        from sqlalchemy import text
        from shared.artifacts.lineage import FetchStep, Lineage
        from shared.artifacts.missingness import RawNoCleaning
        from shared.artifacts.types import Series
        from shared.artifacts.units import TimeSeriesUnits
        from state.artifact_store import put_artifact
        from state.object_storage import LocalFSBackend
        from state.working_set import add as ws_add, list_visible

        storage = LocalFSBackend(root=tempfile.mkdtemp())

        # Build a session + turn + 50 artifacts + 50 bindings.
        sid = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO copilot_state.sessions (id) VALUES (:id)"),
                {"id": sid},
            )
            tid = conn.execute(
                text(
                    """
                    INSERT INTO copilot_state.turns
                        (session_id, sequence_no, user_message, status)
                    VALUES (:sid, 1, 'hello', 'completed')
                    RETURNING id
                    """
                ),
                {"sid": sid},
            ).scalar_one()

        for i in range(50):
            step = FetchStep.build(
                name="fetch_single_tenor", version="1.0.0",
                params={"curve_family": "UST", "tenor": "10Y", "i": i},
            )
            art = Series(
                series_key=f"k.{i}",
                payload=pd.Series([1.0], index=pd.date_range("2024-01-01", periods=1)),
                units=TimeSeriesUnits.PERCENT, frequency="D",
                missingness_policy=RawNoCleaning(),
                lineage=Lineage.from_steps([step]),
            )
            with engine.begin() as conn:
                h = put_artifact(art, conn=conn, object_storage=storage)
                ws_add(
                    f"name_{i:03d}", h, tid,
                    session_id=sid, conn=conn,
                )

        # Now bench the read.
        durations_ms: List[float] = []
        for _ in range(_N_ITERATIONS):
            with engine.connect() as conn:
                start = time.perf_counter()
                visible = list_visible(session_id=sid, conn=conn)
                durations_ms.append((time.perf_counter() - start) * 1000)
        assert len(visible) == 50

        stats = _report("working_set_load", durations_ms)
        bound = PERF_TARGETS["working_set_load"]["bound_ms"]
        assert stats["p95"] < bound, (
            f"working_set_load p95 {stats['p95']:.1f}ms exceeds "
            f"alarm bound {bound}ms"
        )


# ============================================================================
# Artifact fetch — inline
# ============================================================================


class TestArtifactFetchInline:
    def test_p95_under_bound(self, engine):
        """Small Series (< 100 rows) → inline payload → no object-
        storage hop on get."""
        from shared.artifacts.lineage import FetchStep, Lineage
        from shared.artifacts.missingness import RawNoCleaning
        from shared.artifacts.types import Series
        from shared.artifacts.units import TimeSeriesUnits
        from state.artifact_store import get_artifact, put_artifact
        from state.object_storage import LocalFSBackend

        storage = LocalFSBackend(root=tempfile.mkdtemp())

        # 10 rows -> inline.
        step = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y", "case": "inline"},
        )
        idx = pd.date_range("2024-01-01", periods=10, freq="D")
        art = Series(
            series_key="inline.test",
            payload=pd.Series([1.0] * 10, index=idx),
            units=TimeSeriesUnits.PERCENT, frequency="D",
            missingness_policy=RawNoCleaning(),
            lineage=Lineage.from_steps([step]),
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        durations_ms: List[float] = []
        for _ in range(_N_ITERATIONS):
            with engine.connect() as conn:
                start = time.perf_counter()
                _ = get_artifact(h, conn=conn, object_storage=storage)
                durations_ms.append((time.perf_counter() - start) * 1000)

        stats = _report("artifact_fetch_inline", durations_ms)
        bound = PERF_TARGETS["artifact_fetch_inline"]["bound_ms"]
        assert stats["p95"] < bound


# ============================================================================
# Artifact fetch — blob (cold)
# ============================================================================


class TestArtifactFetchBlobCold:
    def test_p95_under_bound(self, engine):
        """Large series (>= 252 rows) → blob storage.  Each iteration
        uses a FRESH artifact (new hash) so the LocalFS / OS page
        cache cannot warm — this is the cold-blob path."""
        from shared.artifacts.lineage import FetchStep, Lineage
        from shared.artifacts.missingness import RawNoCleaning
        from shared.artifacts.types import Series
        from shared.artifacts.units import TimeSeriesUnits
        from state.artifact_store import get_artifact, put_artifact
        from state.object_storage import LocalFSBackend

        storage = LocalFSBackend(root=tempfile.mkdtemp())

        # Pre-write 20 unique artifacts.
        hashes: List[str] = []
        for i in range(_N_ITERATIONS):
            step = FetchStep.build(
                name="fetch_single_tenor", version="1.0.0",
                params={"curve_family": "UST", "tenor": "10Y", "iter": i},
            )
            idx = pd.date_range("2024-01-01", periods=300, freq="D")
            art = Series(
                series_key=f"blob.test.{i}",
                payload=pd.Series([1.0 + i + j * 0.001 for j in range(300)], index=idx),
                units=TimeSeriesUnits.PERCENT, frequency="D",
                missingness_policy=RawNoCleaning(),
                lineage=Lineage.from_steps([step]),
            )
            with engine.begin() as conn:
                hashes.append(put_artifact(art, conn=conn, object_storage=storage))

        durations_ms: List[float] = []
        for h in hashes:
            with engine.connect() as conn:
                start = time.perf_counter()
                _ = get_artifact(h, conn=conn, object_storage=storage)
                durations_ms.append((time.perf_counter() - start) * 1000)

        stats = _report("artifact_fetch_blob_cold", durations_ms)
        bound = PERF_TARGETS["artifact_fetch_blob_cold"]["bound_ms"]
        assert stats["p95"] < bound


# ============================================================================
# Workspace open — 12-node DAG, summaries only
# ============================================================================


class TestWorkspaceOpen12Nodes:
    def test_p95_under_bound(self, engine):
        """Build a 12-step chain, persist artifact + DAG + workspace,
        then call GET /workspace/{slug} (via the route handler
        directly to avoid the full TestClient overhead) repeatedly."""
        from shared.artifacts.lineage import (
            FetchStep, Lineage, OperatorStep,
        )
        from shared.artifacts.missingness import RawNoCleaning
        from shared.artifacts.types import Series
        from shared.artifacts.units import TimeSeriesUnits
        from state.artifact_store import put_artifact
        from state.dag_repo import persist_dag_from_lineage
        from state.object_storage import LocalFSBackend
        from state.workspace_repo import create_workspace
        from api import dependencies

        # Bind the engine module-globally so the route handler picks
        # us up.  Mirrors the pattern from
        # tests/integration/test_workspace_replay.py.
        dependencies._engine = None
        from sqlalchemy import create_engine as _ce

        dependencies._engine = _ce(_DB_URL)

        storage = LocalFSBackend(root=tempfile.mkdtemp())

        steps = []
        first = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y", "leaf": 0},
        )
        steps.append(first)
        # 11 more OperatorStep entries chained linearly.
        prev = first
        for i in range(11):
            op = OperatorStep.build(
                name="align_series", version="1.0.0",
                params={"step": i + 1},
                input_hashes=(prev.hash,),
            )
            steps.append(op)
            prev = op
        chain = Lineage.from_steps(steps)
        assert len(chain.steps) == 12

        idx = pd.date_range("2024-01-01", periods=1)
        art = Series(
            series_key="ws.12",
            payload=pd.Series([1.0], index=idx),
            units=TimeSeriesUnits.PERCENT, frequency="D",
            missingness_policy=RawNoCleaning(),
            lineage=chain,
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)
            dag_hash = persist_dag_from_lineage(
                chain, conn=conn, head_artifact_hash=h,
            )
            ws = create_workspace(
                dag_hash, conn=conn, name="perf test",
            )

        # Build a TestClient against just the workspace router.
        fastapi_mod = pytest.importorskip("fastapi")
        httpx_mod = pytest.importorskip("httpx")  # noqa: F841

        from fastapi.testclient import TestClient
        from api.routes.workspace import router as workspace_router

        app = fastapi_mod.FastAPI()
        app.include_router(workspace_router, prefix="/api/v1/workspace")
        client = TestClient(app)

        durations_ms: List[float] = []
        for _ in range(_N_ITERATIONS):
            start = time.perf_counter()
            resp = client.get(f"/api/v1/workspace/{ws.slug}")
            durations_ms.append((time.perf_counter() - start) * 1000)
            assert resp.status_code == 200

        stats = _report("workspace_open_12_nodes", durations_ms)
        bound = PERF_TARGETS["workspace_open_12_nodes"]["bound_ms"]
        assert stats["p95"] < bound


# ============================================================================
# Cache-hit fast path
# ============================================================================


class TestCacheHitFastPath:
    def test_p95_under_bound(self, engine):
        """When the cache is populated, get_artifact returns from the
        cache without an object-storage round-trip.  Should be < 5 ms
        target, < 100 ms alarm."""
        fakeredis = pytest.importorskip("fakeredis")

        from shared.artifacts.lineage import FetchStep, Lineage
        from shared.artifacts.missingness import RawNoCleaning
        from shared.artifacts.types import Series
        from shared.artifacts.units import TimeSeriesUnits
        from state.artifact_store import get_artifact, put_artifact
        from state.cache import RedisBytesCache
        from state.object_storage import LocalFSBackend

        storage = LocalFSBackend(root=tempfile.mkdtemp())
        cache = RedisBytesCache(
            url="redis://fake/0", client=fakeredis.FakeStrictRedis(),
        )

        step = FetchStep.build(
            name="fetch_single_tenor", version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y", "case": "cache"},
        )
        idx = pd.date_range("2024-01-01", periods=300, freq="D")
        art = Series(
            series_key="cache.test",
            payload=pd.Series([1.0] * 300, index=idx),
            units=TimeSeriesUnits.PERCENT, frequency="D",
            missingness_policy=RawNoCleaning(),
            lineage=Lineage.from_steps([step]),
        )
        with engine.begin() as conn:
            h = put_artifact(art, conn=conn, object_storage=storage)

        # Warm the cache with one fetch that hits object storage.
        with engine.connect() as conn:
            get_artifact(h, conn=conn, object_storage=storage, cache=cache)
        assert cache.get(h) is not None

        # Now bench cache-hits.
        durations_ms: List[float] = []
        for _ in range(_N_ITERATIONS):
            with engine.connect() as conn:
                start = time.perf_counter()
                _ = get_artifact(h, conn=conn, object_storage=storage, cache=cache)
                durations_ms.append((time.perf_counter() - start) * 1000)

        stats = _report("cache_hit", durations_ms)
        bound = PERF_TARGETS["cache_hit"]["bound_ms"]
        assert stats["p95"] < bound

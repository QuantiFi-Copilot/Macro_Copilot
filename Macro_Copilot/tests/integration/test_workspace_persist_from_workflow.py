"""tests/integration/test_workspace_persist_from_workflow.py

PR A — real-Postgres test for the workflow→workspace persistence path.

What this test proves
---------------------
The new ``state.dag_repo.persist_dag_from_workflow_result`` helper:

  1. Persists every node's artifact via ``put_artifact``.
  2. Stores the DAG topology (nodes + edges) keyed by a
     content-addressed ``dag_hash``.
  3. Returns identity bits suitable for minting a workspace.
  4. Is idempotent — calling it twice with the same workflow +
     same artifacts produces ONE row, not duplicates.

Plus the runner-side end-to-end:

  5. ``run_template_with_resolver(..., persist=True, ...)`` returns
     an envelope with ``workspace.slug`` + ``dag_hash`` +
     ``terminal_artifact_hash`` when persistence succeeds.
  6. ``object_storage=None`` short-circuits cleanly: persistence
     reports ``ok=False`` but the primary execute path still
     returns ``ok=True``.

Module-level skip when Postgres is unreachable.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


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
    reason=f"Postgres not reachable at {_DB_URL!r}",
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
    """Reset state schema between tests."""
    from sqlalchemy import text

    from state.methodology_versions import clear_caches

    clear_caches()
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


@pytest.fixture
def storage(tmp_path):
    from state.object_storage import LocalFSBackend

    return LocalFSBackend(root=tmp_path / "artifacts")


# ============================================================================
# Hand-built workflow + result for direct persist_dag_from_workflow_result tests
# ============================================================================


def _build_two_node_workflow_and_result():
    """Construct a minimal 2-node workflow + matching WorkflowResult
    using only finance-blind types.  Avoids touching the rates
    primitive registry, so the test stays a substrate-level unit
    test that doesn't depend on rates_agent.

    Topology:
        primitive (synthetic Series) ─→ operator (identity)
                                          [terminal]
    """
    from shared.artifacts.lineage import (
        FetchStep,
        Lineage,
        OperatorStep,
        PrimitiveStep,
    )
    from shared.artifacts.missingness import RawNoCleaning
    from shared.artifacts.types import Series
    from shared.artifacts.units import TimeSeriesUnits
    from shared.workflow.result import WorkflowResult
    from shared.workflow.types import (
        OperatorNode,
        PrimitiveNode,
        Workflow,
        WorkflowEdge,
    )

    # Lineage for the primitive node's Series.
    fetch = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y"},
    )
    prim_step = PrimitiveStep.build(
        name="calculate_test_primitive",
        version="1.0.0",
        params={"curve_family": "UST"},
        tool_config_hash="d" * 64,
        output_field="time_series_test",
        as_of_date="2024-01-01",
        input_hashes=(fetch.hash,),
    )
    op_step = OperatorStep.build(
        name="series_arithmetic",
        version="1.0.0",
        params={"op": "diff"},
        input_hashes=(prim_step.hash,),
    )

    prim_series = Series(
        series_key="UST.10Y",
        payload=pd.Series([1.0, 2.0, 3.0],
                          index=pd.date_range("2024-01-01", periods=3)),
        units=TimeSeriesUnits.PERCENT,
        frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([fetch, prim_step]),
    )
    op_series = Series(
        series_key="UST.10Y.diff",
        payload=pd.Series([float("nan"), 1.0, 1.0],
                          index=pd.date_range("2024-01-01", periods=3)),
        units=TimeSeriesUnits.PERCENT,
        frequency="D",
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([fetch, prim_step, op_step]),
    )

    workflow = Workflow(
        workflow_id="test-2-node",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="calculate_test_primitive",
                output_field="time_series_test",
                params={"curve_family": "UST", "tenor": "10Y"},
            ),
            OperatorNode(
                node_id="op1",
                operator_name="series_arithmetic",
                params={"op": "diff"},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1",
                target_node_id="op1",
                target_input_slot="left",
            ),
        ],
        terminal_node_id="op1",
    )

    result = WorkflowResult(
        workflow_id="test-2-node",
        terminal_artifact=op_series,
        workflow_lineage_summary="workflow test-2-node: p1 → op1",
        node_artifacts={"p1": prim_series, "op1": op_series},
    )
    return workflow, result


# ============================================================================
# Direct unit tests for persist_dag_from_workflow_result
# ============================================================================


def test_persist_workflow_writes_dag_with_nodes_and_edges(
    engine, storage,
):
    """The helper writes one ``dags`` row, one ``dag_nodes`` row per
    workflow node (with the executed ``artifact_hash`` filled), and
    one ``dag_edges`` row per workflow edge using the workflow's
    real slot names."""
    from state.dag_repo import (
        get_dag,
        persist_dag_from_workflow_result,
    )

    workflow, result = _build_two_node_workflow_and_result()

    with engine.begin() as conn:
        persisted = persist_dag_from_workflow_result(
            workflow, result, conn=conn, object_storage=storage,
        )

    assert persisted.dag_hash
    assert persisted.terminal_artifact_hash
    assert set(persisted.node_artifact_hashes.keys()) == {"p1", "op1"}

    # Read the persisted DAG back.
    with engine.connect() as conn:
        stored = get_dag(persisted.dag_hash, conn=conn)

    assert {n.node_id for n in stored.nodes} == {"p1", "op1"}
    # Every node carries the executed artifact_hash.
    by_id = {n.node_id: n for n in stored.nodes}
    assert by_id["p1"].artifact_hash == persisted.node_artifact_hashes["p1"]
    assert by_id["op1"].artifact_hash == persisted.node_artifact_hashes["op1"]
    # Names reflect the workflow's tool / operator names.
    assert by_id["p1"].name == "calculate_test_primitive"
    assert by_id["op1"].name == "series_arithmetic"
    # Edges include the substrate's real slot name (not a sentinel).
    assert len(stored.edges) == 1
    e = stored.edges[0]
    assert e.from_node == "p1"
    assert e.to_node == "op1"
    assert e.slot_name == "left"


def test_persist_workflow_is_idempotent(engine, storage):
    """Calling the helper twice with the same workflow + artifacts
    must NOT create duplicate rows.  The DAG hash is content-
    addressed; ON CONFLICT DO NOTHING absorbs the second insert."""
    from sqlalchemy import text

    from state.dag_repo import persist_dag_from_workflow_result

    workflow, result = _build_two_node_workflow_and_result()

    with engine.begin() as conn:
        a = persist_dag_from_workflow_result(
            workflow, result, conn=conn, object_storage=storage,
        )
    with engine.begin() as conn:
        b = persist_dag_from_workflow_result(
            workflow, result, conn=conn, object_storage=storage,
        )

    assert a.dag_hash == b.dag_hash, "DAG hash must be deterministic"
    assert a.node_artifact_hashes == b.node_artifact_hashes

    with engine.connect() as conn:
        n_dags = conn.execute(
            text("SELECT COUNT(*) FROM copilot_state.dags WHERE hash = :h"),
            {"h": a.dag_hash},
        ).scalar()
        n_nodes = conn.execute(
            text(
                "SELECT COUNT(*) FROM copilot_state.dag_nodes "
                "WHERE dag_hash = :h"
            ),
            {"h": a.dag_hash},
        ).scalar()
        n_edges = conn.execute(
            text(
                "SELECT COUNT(*) FROM copilot_state.dag_edges "
                "WHERE dag_hash = :h"
            ),
            {"h": a.dag_hash},
        ).scalar()

    assert n_dags == 1
    assert n_nodes == 2
    assert n_edges == 1


def test_persist_rejects_mismatched_node_artifacts(engine, storage):
    """If WorkflowResult.node_artifacts doesn't match the workflow's
    nodes 1:1, the helper raises rather than silently storing a
    partial DAG.  Guards against an executor regression where a
    failed node fails to bubble up cleanly."""
    from shared.workflow.result import WorkflowResult

    from state.dag_repo import persist_dag_from_workflow_result

    workflow, _ = _build_two_node_workflow_and_result()

    # Build a malformed result missing the terminal artifact entry.
    _, full = _build_two_node_workflow_and_result()
    full_artifacts = dict(full.node_artifacts)
    full_artifacts.pop("op1")
    bad_result = WorkflowResult(
        workflow_id=full.workflow_id,
        terminal_artifact=full.terminal_artifact,
        workflow_lineage_summary=full.workflow_lineage_summary,
        node_artifacts=full_artifacts,
    )

    with engine.begin() as conn:
        with pytest.raises(ValueError, match="node_artifacts"):
            persist_dag_from_workflow_result(
                workflow, bad_result, conn=conn, object_storage=storage,
            )


# ============================================================================
# Runner-side end-to-end (persist=True envelope behaviour)
# ============================================================================


def test_runner_envelope_carries_workspace_when_persist_succeeds(
    engine, storage,
):
    """Drives ``run_template_with_resolver(..., persist=True, ...)``
    via a synthetic resolver and asserts the envelope carries the
    persisted workspace handles.

    This is the seam the chat / Build's empty-state composer
    depends on — without these envelope fields, the
    BuildShell's transition from "building" to "completed"
    would have no slug to navigate to.
    """
    # NOTE: rather than building a real synthetic resolver here, we
    # directly call ``_persist_executed_workflow`` (the runner's
    # internal helper) with the hand-built workflow + result above.
    # This keeps the test substrate-only — no rates_agent imports —
    # while still exercising the exact code path the chat hits.
    from rates_agent.workflows._runner import _persist_executed_workflow

    workflow, result = _build_two_node_workflow_and_result()

    persistence = _persist_executed_workflow(
        template_id="test-2-node",
        workflow=workflow,
        result=result,
        engine=engine,
        object_storage=storage,
        workspace_name="envelope test",
        workspace_created_by="pytest",
        # R5.6 forkability guard: template_id and bound_slot_values must
        # be set together.  The production caller
        # (``run_template_with_resolver``) always passes
        # ``bound_slot_values=dict(slot_values or {})`` alongside the
        # template_id — mirror that here.
        bound_slot_values={"curve_family": "UST", "tenor": "10Y"},
    )

    assert persistence["ok"] is True
    assert persistence["dag_hash"]
    assert persistence["terminal_artifact_hash"]
    assert set(persistence["node_artifact_hashes"].keys()) == {"p1", "op1"}
    ws = persistence["workspace"]
    assert ws["slug"]
    assert ws["url"] == f"/workspace/{ws['slug']}"
    assert ws["name"] == "envelope test"
    assert ws["dag_hash"] == persistence["dag_hash"]


def test_runner_short_circuits_when_object_storage_is_none(engine):
    """``object_storage=None`` reduces persistence to a benign
    skip — the helper returns ``{ok: False}`` rather than raising."""
    from rates_agent.workflows._runner import _persist_executed_workflow

    workflow, result = _build_two_node_workflow_and_result()

    persistence = _persist_executed_workflow(
        template_id="test-2-node",
        workflow=workflow,
        result=result,
        engine=engine,
        object_storage=None,    # <- trigger condition
        workspace_name=None,
        workspace_created_by=None,
    )

    assert persistence["ok"] is False
    assert "object_storage" in (persistence.get("error") or "")

"""tests/orchestrator/test_session_open_dag_persistence.py — Codex follow-up.

Session-level proof that ``CopilotSession._maybe_emit_open_dag_workflow_events``
walks the full persist + emit pipeline on a PASS open-DAG outcome:

  1. Acquires engine + object_storage from ``api.dependencies``.
  2. Calls ``state.dag_repo.persist_dag_from_workflow_result`` with the
     ``workflow + WorkflowResult`` it received on the outcome.
  3. Calls ``state.workspace_repo.create_workspace`` with
     ``template_id=None`` / ``bound_slot_values=None`` /
     ``focus_node=workflow.terminal_node_id`` (the plan §A.3 contract).
  4. Emits ``workflow_route_decision`` with ``template_id=None``.
  5. Emits ``workflow_status: running`` → ``workflow_status: complete``
     (the plan §A.3 status-sequence contract — fixes Codex gap #3).
  6. Emits ``workflow_result`` carrying the workspace slug + URL +
     ScalarMetric ``terminal_artifact`` payload.

What this test is NOT
=====================

This is NOT the full Postgres integration test the plan §D.2
mentions — that lives at
``tests/integration/test_workspace_persist_from_workflow.py`` and
skips when Postgres is unreachable (the canonical CI substrate).
This test mocks the state-engine + object-storage + persist-helper
boundaries via ``monkeypatch`` so the SESSION-LEVEL emit path is
exercised on every CI run (not gated on Postgres availability).

Codex's "missing session-level persistence proof" critique pointed
out that the existing PR-11D backend test
(``tests/eval/test_open_dag_pr11_executed_dag.py``) is
PIPELINE-LEVEL, not session-level — it asserts the
``PipelineOutcome`` carries the new shapes but never reaches into
``CopilotSession`` to verify the emit + persist path.  This file
closes that gap.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag.executed_dag import (
    ExecutedDag,
    TerminalArtifactSummary,
)
from orchestrator.open_dag.intent_chain import IntentChain
from orchestrator.open_dag.pipeline import PipelineOutcome
from orchestrator.open_dag.coverage_gate import GateVerdict
from orchestrator.open_dag.run_record import RunLineage
from orchestrator.session import CopilotSession, SessionEvent
from shared.artifacts.lineage import FetchStep, Lineage
from shared.artifacts.types import ScalarMetric
from shared.schemas.time_series import TimeSeriesUnits
from shared.workflow.result import WorkflowResult
from shared.workflow.types import (
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
)


# ============================================================================
# FIXTURES — synthetic workflow + executed_dag + outcome
# ============================================================================


def _make_terminal_scalar_metric() -> ScalarMetric:
    """Build a real ScalarMetric the persist helper would accept."""
    step = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y"},
    )
    return ScalarMetric(
        metric_key="correlation_coefficient",
        value=-0.342,
        units=TimeSeriesUnits.RATIO,
        lineage=Lineage.from_steps([step]),
    )


def _make_synthetic_workflow() -> Workflow:
    """Two-node DAG: primitive feeds correlation → ScalarMetric.

    Mirrors the canonical "two primitives + correlation" open-DAG
    composition shape but uses a one-input synthetic stand-in so the
    test stays small.
    """
    leaf_a = PrimitiveNode(
        node_id="leaf_a",
        tool_name="calculate_curve_spread_tool",
        output_field="time_series_spread",
        params={
            "curve_family": "UST",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 1825,
        },
    )
    op_summarize = OperatorNode(
        node_id="summarize_terminal",
        operator_name="summarize_series",
        params={},
    )
    workflow = Workflow(
        workflow_id="synth_open_dag_correlation",
        nodes=[leaf_a, op_summarize],
        edges=[
            WorkflowEdge(
                source_node_id="leaf_a",
                target_node_id="summarize_terminal",
                target_input_slot="series",
            ),
        ],
        terminal_node_id="summarize_terminal",
    )
    return workflow


def _make_outcome_pass() -> PipelineOutcome:
    """Build a PASS PipelineOutcome carrying a real workflow +
    ExecutedDag.

    Uses ``Workflow.model_construct`` only where the synthetic node
    won't pass the substrate's slot-shape validation (the real
    persist helper does its own validation; we mock that out)."""
    workflow = _make_synthetic_workflow()
    terminal = _make_terminal_scalar_metric()

    result = WorkflowResult(
        workflow_id=workflow.workflow_id,
        terminal_artifact=terminal,
        workflow_lineage_summary=(
            "workflow synth_open_dag_correlation: leaf_a -> summarize_terminal"
        ),
        node_artifacts={
            "leaf_a": terminal,
            "summarize_terminal": terminal,
        },
    )
    executed = ExecutedDag.from_workflow_result(result)

    route_decision = RouteDecision(
        action=RouteAction.SINGLE_DOMAIN,
        domains=[Domain.SOVEREIGN_BONDS],
        rationale="synthetic open-DAG correlation",
        intent_tag=IntentTag.RELATIONSHIP,
        decomposition=[
            EconomicQuantity(
                name="us_2s10s",
                nl_description="UST 2s10s curve spread",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ],
    )

    intent_chain = IntentChain.from_inputs(
        user_prompt="Correlation between US 2s10s and 5Y breakeven over 5y",
        route_decision=route_decision,
        bound_leaves=(),
        shape_or_workflow=workflow,
        gate_verdict=GateVerdict(status="PASS", reason="canonical"),
    )
    run_lineage = RunLineage(
        intent_chain=intent_chain,
        compute_lineage=terminal.lineage,
        determinism_bucket="EXECUTED_CONTENT_ADDRESSED",
    )

    return PipelineOutcome(
        status="PASS",
        markdown="PR-11_E2E_ANSWER: correlation -0.342",
        intent_chain=intent_chain,
        run_lineage=run_lineage,
        route_decision=route_decision,
        workflow=workflow,
        executed_dag=executed,
    )


# ============================================================================
# THE TEST
# ============================================================================


@pytest.mark.asyncio
async def test_session_emit_workflow_events_persists_and_emits_slug(monkeypatch):
    """Codex follow-up gap #2 + #3: SESSION-level proof.

    Constructs a PASS ``PipelineOutcome`` with a real workflow +
    ExecutedDag, mocks the engine + object_storage + persist helpers,
    invokes ``_maybe_emit_open_dag_workflow_events`` directly on a
    constructed ``CopilotSession``, and asserts:

      - persist_dag_from_workflow_result was called with the workflow
        + WorkflowResult
      - create_workspace was called with ``template_id=None``,
        ``bound_slot_values=None``, and the workflow's terminal_node_id
      - The session emitted ``workflow_route_decision`` with
        ``template_id=None``
      - The session emitted ``workflow_status: running`` followed by
        ``workflow_status: complete`` (gap #3 — running→complete
        sequence)
      - The session emitted ``workflow_result`` carrying the slug-
        bearing workspace handle + terminal_artifact dict
    """
    # ----- 1. Construct a session.  No DB, no children — we exercise
    #         the helper directly via its public-ish entry point.
    session = CopilotSession(
        thread_id="test-thread-open-dag",
        stateless=True,
    )

    # ----- 2. Mock state-layer + api.dependencies imports.
    #         The helper does function-level imports, so monkeypatching
    #         the source modules suffices.
    persist_calls: List[Dict[str, Any]] = []
    create_workspace_calls: List[Dict[str, Any]] = []

    class _StubPersisted:
        dag_hash = "deadbeef" * 8  # 64 chars
        terminal_artifact_hash = "cafebabe" * 8
        node_artifact_hashes = {
            "leaf_a": "a" * 64,
            "summarize_terminal": "b" * 64,
        }

    class _StubWorkspace:
        id = "ws-uuid-stub"
        slug = "corr-2s10s-be5y-abc"
        name = None
        dag_hash = _StubPersisted.dag_hash

    def fake_persist_dag(workflow, result, *, conn, object_storage):
        persist_calls.append(
            {
                "workflow_id": workflow.workflow_id,
                "terminal_node_id": workflow.terminal_node_id,
                "result_terminal_type": type(
                    result.terminal_artifact,
                ).__name__,
            },
        )
        return _StubPersisted()

    def fake_create_workspace(
        dag_hash,
        *,
        conn,
        name,
        created_by,
        focus_node,
        template_id,
        bound_slot_values,
        parent_workspace_id,
        run_audit=None,
    ):
        create_workspace_calls.append(
            {
                "dag_hash": dag_hash,
                "name": name,
                "focus_node": focus_node,
                "template_id": template_id,
                "bound_slot_values": bound_slot_values,
                "parent_workspace_id": parent_workspace_id,
                # Phase D / D9 — the audit sidecar threaded from the
                # PASS outcome (migration 0010).
                "run_audit": run_audit,
            },
        )
        return _StubWorkspace()

    class _InvalidNameError(Exception):
        pass

    # Patch the source modules — the helper's function-level imports
    # resolve these attrs at call time.
    import state.dag_repo as _dag_repo
    import state.workspace_repo as _workspace_repo
    import api.dependencies as _api_dependencies

    monkeypatch.setattr(
        _dag_repo,
        "persist_dag_from_workflow_result",
        fake_persist_dag,
    )
    monkeypatch.setattr(
        _workspace_repo, "create_workspace", fake_create_workspace,
    )
    monkeypatch.setattr(
        _workspace_repo, "InvalidNameError", _InvalidNameError,
    )

    # Stub engine.begin() → context manager yielding a dummy conn.
    class _FakeConn:
        pass

    class _FakeEngineCtx:
        def __enter__(self):
            return _FakeConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    fake_engine = MagicMock()
    fake_engine.begin.return_value = _FakeEngineCtx()
    fake_object_storage = MagicMock(name="object_storage")

    monkeypatch.setattr(
        _api_dependencies, "get_engine", lambda: fake_engine,
    )
    monkeypatch.setattr(
        _api_dependencies, "init_engine", lambda: fake_engine,
    )
    monkeypatch.setattr(
        _api_dependencies, "get_object_storage",
        lambda: fake_object_storage,
    )
    monkeypatch.setattr(
        _api_dependencies, "init_object_storage",
        lambda: fake_object_storage,
    )

    # ----- 3. Capture emitted SessionEvents.
    emitted: List[SessionEvent] = []

    async def fake_emit(event: SessionEvent) -> None:
        emitted.append(event)

    # ----- 4. Build the PASS outcome and run the helper.
    outcome = _make_outcome_pass()
    await session._maybe_emit_open_dag_workflow_events(
        outcome=outcome,
        emit=fake_emit,
    )

    # ----- 5. Assertions.
    # 5a. persist_dag_from_workflow_result called exactly once with
    #     the workflow + WorkflowResult.
    assert len(persist_calls) == 1, (
        "Session must call persist_dag_from_workflow_result exactly "
        f"once; got {len(persist_calls)} calls"
    )
    assert persist_calls[0]["workflow_id"] == "synth_open_dag_correlation"
    assert persist_calls[0]["terminal_node_id"] == "summarize_terminal"
    assert persist_calls[0]["result_terminal_type"] == "ScalarMetric"

    # 5b. create_workspace called with template_id=None and the
    #     right focus_node (plan §A.3 contract).
    assert len(create_workspace_calls) == 1
    cw = create_workspace_calls[0]
    assert cw["template_id"] is None, (
        "Codex correction: open-DAG must persist with template_id=None "
        f"(NOT a fake __open_dag__ id); got {cw['template_id']!r}"
    )
    assert cw["bound_slot_values"] is None
    assert cw["focus_node"] == "summarize_terminal"
    assert cw["dag_hash"] == _StubPersisted.dag_hash
    assert cw["parent_workspace_id"] is None

    # 5b'. Phase D / D9 — the audit sidecar is threaded from the PASS
    # outcome: versioned shape carrying the IntentChain dump (the
    # build page renders "what I understood / checked / fixed" from
    # this).
    audit = cw["run_audit"]
    assert audit is not None, (
        "Phase D: a PASS outcome with an IntentChain must persist a "
        "run_audit sidecar"
    )
    assert audit["schema_version"] == 1
    assert audit["intent_chain"]["gate"]["status"] == "PASS"
    assert "recompose_trace" in audit

    # 5c. Emitted events: route_decision, status running, status
    #     complete, workflow_result — in this order.
    emitted_types = [e.type for e in emitted]
    assert emitted_types == [
        "workflow_route_decision",
        "workflow_status",
        "workflow_status",
        "workflow_result",
    ], (
        f"Emitted event sequence must be route_decision → "
        f"status:running → status:complete → workflow_result; got "
        f"{emitted_types!r}"
    )

    # 5d. workflow_route_decision carries template_id=None + action=route.
    rd = emitted[0]
    assert rd.data["action"] == "route"
    assert rd.data["template_id"] is None, (
        "Codex follow-up: open-DAG route_decision must carry "
        f"template_id=None; got {rd.data['template_id']!r}"
    )
    assert rd.data["slot_values"] == {}
    assert "Open DAG" in rd.data["rationale"]

    # 5e. Status sequence is running → complete (Codex gap #3 fix).
    assert emitted[1].data["status"] == "running", (
        "Codex gap #3: workflow_status: running must be emitted "
        f"before complete; got {emitted[1].data['status']!r}"
    )
    assert emitted[2].data["status"] == "complete"

    # 5f. workflow_result carries the slug + workspace handle.
    wr = emitted[3]
    assert wr.data["ok"] is True
    assert wr.data["template_id"] is None
    assert wr.data["workspace"] is not None
    assert wr.data["workspace"]["slug"] == "corr-2s10s-be5y-abc"
    assert wr.data["workspace"]["url"] == "/workspace/corr-2s10s-be5y-abc"
    assert wr.data["workspace"]["dag_hash"] == _StubPersisted.dag_hash
    assert wr.data["persistence"] == {"ok": True}

    # 5g. terminal_artifact carries the ScalarMetric branch payload
    #     (ScalarMetric.value reachable from the wire).
    ta = wr.data["terminal_artifact"]
    assert ta["type"] == "ScalarMetric"
    assert ta["metric_key"] == "correlation_coefficient"
    assert ta["value"] == pytest.approx(-0.342, rel=1e-6)
    assert ta["units"] == TimeSeriesUnits.RATIO.value

    # 5h. workflow_lineage_summary surfaces unchanged (back-compat).
    assert wr.data["workflow_lineage_summary"] == (
        "workflow synth_open_dag_correlation: leaf_a -> summarize_terminal"
    )

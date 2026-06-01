"""tests/eval/test_open_dag_pr11_executed_dag.py — PR-11D acceptance.

End-to-end assertions for the PR-11A surface additions on the canonical
open-DAG correlation query.  The pre-PR-11 canonical test
(``test_canonical_cross_domain_correlation_REAL_executor`` in
``test_open_dag_end_to_end.py``) verifies the pipeline reaches strict
PASS with the real executor + real primitives + real correlation
operator; this file adds the new PR-11A assertions on top:

  - ``outcome.workflow`` is populated (the assembled ``Workflow`` is
    surfaced on the outcome — Codex correction: no mutable
    ``pipeline.last_workflow``; the workflow is carried back via the
    typed outcome so the session layer can call
    ``persist_dag_from_workflow_result`` without reaching into pipeline
    state).
  - ``outcome.executed_dag`` is populated and carries the full
    ``WorkflowResult`` (the prior ``(Lineage, summary_str)`` tuple
    discarded ``node_artifacts`` and blocked open-DAG persistence).
  - ``executed_dag.terminal_summary`` carries the ScalarMetric
    ``metric_key`` + ``value`` + ``units`` so the frontend
    ``workflow_result`` card can render the actual correlation number
    (not a hash).
  - ``executed_dag.workflow_lineage_summary`` matches the
    legacy-helper-produced string (back-compat with
    ``executed_summary_from_result``).
  - ``summarize_terminal`` (the rates runner helper both lanes use)
    emits the ScalarMetric branch with the same fields the
    ``TerminalArtifactSummary`` carries.
  - ``node_artifacts`` covers every workflow node 1:1 (the persistence
    helper's precondition).
  - The terminal artifact is a real ``ScalarMetric`` whose
    ``metric_key`` matches the substrate operator's emitter.

Mocking discipline
==================

Same as the pre-PR-11 canonical test: LLM-driven sub-components
(Router / Composer / Gate / AnswerRenderer) are mocked; the substrate
``execute_workflow`` runs for REAL through the REAL production
primitives + the REAL correlation operator.  Engine-bound primitive
boundaries are mocked in the ``_real_e2e_resolver`` fixture.

Why this lives in tests/eval/
-----------------------------

The pre-PR-11 canonical test lives here and ships the
``_real_e2e_resolver`` fixture + the canonical shape builders.  Adding
a sibling file in the same directory lets the new acceptance test
reuse those fixtures via direct import without re-implementing the
synthetic resolver scaffolding.
"""

from __future__ import annotations

from typing import List

import pytest

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag import (
    BoundLeaf,
    ExecutedDag,
    Frequency,
    GateVerdict,
    OpenDagPipeline,
    TerminalArtifactSummary,
    build_default_executor_callback,
    executed_summary_from_result,
    terminal_summary_from_result,
)
from rates_agent.workflows._runner import summarize_terminal
from shared.artifacts.registry import ArtifactTypeName
from shared.artifacts.types import ScalarMetric
from shared.schemas.time_series import TimeSeriesUnits
from shared.workflow.result import WorkflowResult

# Reuse the canonical real-executor fixtures + shape builder.
from tests.eval.test_open_dag_end_to_end import (  # noqa: E402
    _REAL_BEI_TENOR,
    _REAL_CURVE_FAMILY,
    _REAL_LINKER_CURVE_FAMILY,
    _REAL_LONG_TENOR,
    _REAL_LOOKBACK_DAYS,
    _REAL_SHORT_TENOR,
    _build_cross_domain_correlation_shape,
    _real_e2e_resolver,  # fixture
)

__all__: list = []


@pytest.mark.asyncio
async def test_pr11_outcome_carries_workflow_and_executed_dag(
    _real_e2e_resolver,
):
    """PR-11A acceptance: on strict PASS, ``PipelineOutcome`` carries
    both the assembled ``Workflow`` AND the ``ExecutedDag`` bundle.

    Replicates the canonical cross-domain correlation query through
    the REAL substrate executor + REAL production primitives, then
    asserts the NEW PR-11A surfaces on top of the existing pipeline
    contract.
    """
    cross_domain_shape = _build_cross_domain_correlation_shape()

    class _Router:
        async def route(self, prompt: str) -> RouteDecision:
            return RouteDecision(
                action=RouteAction.MULTI_DOMAIN,
                domains=[
                    Domain.SOVEREIGN_BONDS,
                    Domain.INFLATION_INDEXED_BONDS,
                ],
                rationale="canonical cross-domain",
                intent_tag=IntentTag.RELATIONSHIP,
                decomposition=[
                    EconomicQuantity(
                        name="us_2s10s",
                        nl_description="UST 2s10s curve spread",
                        domain_hint=Domain.SOVEREIGN_BONDS,
                    ),
                    EconomicQuantity(
                        name="us_5y_breakeven",
                        nl_description="USD 5Y breakeven",
                        domain_hint=Domain.INFLATION_INDEXED_BONDS,
                    ),
                ],
            )

    class _Shape:
        async def compose(self, **kw):
            return cross_domain_shape

    class _PassGate:
        async def check(self, **kw):
            return GateVerdict(status="PASS", reason="canonical")

    rendered_lineage_hashes: List[str] = []

    class _Renderer:
        async def render(self, **kw) -> str:
            rendered_lineage_hashes.append(kw.get("lineage_head_hash", ""))
            return f"PR11_E2E_ANSWER: {kw.get('executed_summary', '')}"

    async def sovereign_selector_cb(*, leaf_id, request, timeout_s):
        return BoundLeaf(
            leaf_id=leaf_id,
            domain="sovereign_bonds",
            mcp_tool_name="calculate_curve_spread_tool",
            resolver_tool_key="calculate_curve_spread_tool",
            params={
                "curve_family": _REAL_CURVE_FAMILY,
                "short_tenor": _REAL_SHORT_TENOR,
                "long_tenor": _REAL_LONG_TENOR,
                "lookback_days": _REAL_LOOKBACK_DAYS,
            },
            output_field="time_series_spread",
            declared_output_artifact_type=ArtifactTypeName.SERIES,
            declared_units=TimeSeriesUnits.BPS,
            declared_frequency=Frequency.DAILY,
            declared_semantic_role=request.semantic_role,
            declared_output_meaning=request.requested_output_meaning,
            fit_confidence=0.95,
        )

    async def inflation_selector_cb(*, leaf_id, request, timeout_s):
        return BoundLeaf(
            leaf_id=leaf_id,
            domain="inflation_indexed_bonds",
            mcp_tool_name="calculate_breakeven_inflation_simple_tool",
            resolver_tool_key="calculate_breakeven_inflation_simple_tool",
            params={
                "nominal_curve_family": _REAL_CURVE_FAMILY,
                "linker_curve_family": _REAL_LINKER_CURVE_FAMILY,
                "tenor": _REAL_BEI_TENOR,
                "lookback_days": _REAL_LOOKBACK_DAYS,
            },
            output_field="time_series_breakeven",
            declared_output_artifact_type=ArtifactTypeName.SERIES,
            declared_units=TimeSeriesUnits.BPS,
            declared_frequency=Frequency.DAILY,
            declared_semantic_role=request.semantic_role,
            declared_output_meaning=request.requested_output_meaning,
            fit_confidence=0.95,
        )

    pipeline = OpenDagPipeline(
        router=_Router(),
        composer=_Shape(),
        coverage_gate=_PassGate(),
        answer_renderer=_Renderer(),
        selectors={
            Domain.SOVEREIGN_BONDS: sovereign_selector_cb,
            Domain.INFLATION_INDEXED_BONDS: inflation_selector_cb,
        },
        primitive_resolver=_real_e2e_resolver,
        executor_callback=build_default_executor_callback(
            engine=None,
            primitive_resolver=_real_e2e_resolver,
        ),
    )

    outcome = await pipeline.run(
        "Correlation between US 2s10s and 5Y breakeven over 5y",
    )

    assert outcome.status == "PASS", (
        f"PR-11D: canonical open-DAG E2E must reach strict PASS; got "
        f"status={outcome.status} markdown={outcome.markdown[:200]}"
    )

    # PR-11A surface #1: outcome carries the assembled Workflow.
    assert outcome.workflow is not None, (
        "PR-11A: outcome.workflow must be populated on PASS so the "
        "session layer can call persist_dag_from_workflow_result without "
        "reaching back into pipeline state."
    )
    workflow = outcome.workflow
    assert workflow.terminal_node_id, (
        "PR-11A: workflow.terminal_node_id must be set (used as "
        "focus_node when creating the persisted workspace)."
    )

    # PR-11A surface #2: outcome carries the full ExecutedDag bundle.
    assert outcome.executed_dag is not None, (
        "PR-11A: outcome.executed_dag must be populated on PASS so the "
        "session layer can persist node_artifacts (the prior "
        "(Lineage, summary_str) tuple discarded this and blocked "
        "open-DAG persistence)."
    )
    executed: ExecutedDag = outcome.executed_dag
    assert isinstance(executed.workflow_result, WorkflowResult), (
        "PR-11A: executed_dag.workflow_result must be the full "
        "WorkflowResult (not just a lineage)."
    )

    # node_artifacts covers every workflow node 1:1 — the persist
    # helper's precondition (state.dag_repo.persist_dag_from_workflow_result
    # raises ValueError otherwise).
    workflow_node_ids = {node.node_id for node in workflow.nodes}
    result_node_ids = set(executed.workflow_result.node_artifacts.keys())
    assert workflow_node_ids == result_node_ids, (
        f"PR-11A: node_artifacts must cover every workflow node 1:1.  "
        f"Workflow={sorted(workflow_node_ids)!r}; "
        f"Result={sorted(result_node_ids)!r}."
    )

    # PR-11A surface #3: terminal_summary carries the ScalarMetric
    # fields the frontend workflow_result card renders.
    summary: TerminalArtifactSummary = executed.terminal_summary
    assert summary.artifact_type == "ScalarMetric", (
        f"PR-11D: canonical correlation terminal must be ScalarMetric; "
        f"got {summary.artifact_type!r}"
    )
    assert summary.metric_key, (
        "PR-11A: TerminalArtifactSummary.metric_key must be populated "
        "for ScalarMetric terminals (load-bearing for the frontend "
        "chip)."
    )
    assert summary.value is not None, (
        "PR-11A: TerminalArtifactSummary.value must carry the actual "
        "correlation number (the prior summary string dropped it — "
        "Codex correction)."
    )
    assert summary.units, (
        "PR-11A: TerminalArtifactSummary.units must be populated for "
        "ScalarMetric (RATIO for correlation per the operator's "
        "config.yaml)."
    )
    assert -1.0 <= summary.value <= 1.0, (
        f"PR-11D: correlation coefficient must lie in [-1, 1]; got "
        f"{summary.value!r}"
    )

    # PR-11A surface #4: workflow_lineage_summary matches the legacy
    # helper's output (back-compat — same string the L6 AnswerRenderer
    # consumed pre-PR-11).
    legacy_summary = executed_summary_from_result(executed.workflow_result)
    assert legacy_summary.endswith(executed.workflow_lineage_summary), (
        "PR-11A: executed_dag.workflow_lineage_summary must match the "
        "legacy executed_summary_from_result output (back-compat)."
    )

    # PR-11A surface #5: TerminalArtifactSummary.from_terminal_artifact
    # round-trips against the real terminal.
    summary_round_trip = TerminalArtifactSummary.from_terminal_artifact(
        executed.workflow_result.terminal_artifact,
    )
    assert summary_round_trip == summary, (
        "PR-11A: TerminalArtifactSummary.from_terminal_artifact must "
        "produce the identical summary on a fresh call (idempotent)."
    )

    # PR-11A surface #6: terminal_summary_from_result helper agrees.
    helper_summary = terminal_summary_from_result(executed.workflow_result)
    assert helper_summary == summary, (
        "PR-11A: terminal_summary_from_result must agree with the "
        "executed_dag.terminal_summary captured at execute time."
    )


@pytest.mark.asyncio
async def test_pr11_summarize_terminal_scalar_metric_branch(
    _real_e2e_resolver,
):
    """PR-11A.A.1 + PR-11D.D.1 acceptance: the rates runner's
    ``summarize_terminal`` (used by both the template lane AND the
    open-DAG lane to build the ``workflow_result`` event's
    ``terminal_artifact`` payload) carries the ScalarMetric branch.

    Pre-PR-11 it fell through to ``{"type": "ScalarMetric", "summary":
    "unknown artifact"}`` because of the no-branch fallback — the
    frontend chat card would have rendered no value.  After PR-11 it
    emits ``{type, metric_key, value, units}``.
    """
    # Build a known-shape ScalarMetric.  The canonical correlation
    # operator emits one via the substrate executor, but for an
    # isolated round-trip of the summarize_terminal helper we
    # construct one synthetically here.
    from shared.artifacts.lineage import FetchStep, Lineage
    from shared.schemas.time_series import TimeSeriesUnits

    step = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y"},
    )
    lineage = Lineage.from_steps([step])

    sm = ScalarMetric(
        metric_key="correlation_coefficient",
        value=-0.342,
        units=TimeSeriesUnits.RATIO,
        lineage=lineage,
    )

    payload = summarize_terminal(sm)
    assert payload["type"] == "ScalarMetric"
    assert payload["metric_key"] == "correlation_coefficient"
    assert payload["units"] == TimeSeriesUnits.RATIO.value
    assert payload["value"] == pytest.approx(-0.342, rel=1e-9)
    # No "unknown artifact" fallthrough.
    assert "summary" not in payload or payload.get("summary") != "unknown artifact"


def test_pr11_terminal_artifact_summary_rejects_unknown_artifact():
    """PR-11A: TerminalArtifactSummary.from_terminal_artifact raises
    on any runtime type outside the closed family.

    Defensive — would have been caught upstream at the artifact-store
    boundary, but the explicit rejection makes a future closed-family
    extension fail loud instead of silently mis-summarising.

    We give the fake artifact a real-shaped lineage stub so the
    rejection path under test is the isinstance dispatch's final
    ``raise ValueError``, not an earlier AttributeError on
    ``artifact.lineage``.
    """
    from shared.artifacts.lineage import FetchStep, Lineage

    step = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y"},
    )
    lineage_stub = Lineage.from_steps([step])

    class _NotAnArtifact:
        def __init__(self, lineage):
            self.lineage = lineage

    with pytest.raises(ValueError, match="unknown terminal artifact type"):
        TerminalArtifactSummary.from_terminal_artifact(
            _NotAnArtifact(lineage=lineage_stub),
        )

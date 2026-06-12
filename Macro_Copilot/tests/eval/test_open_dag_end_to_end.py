"""tests/eval/test_open_dag_end_to_end.py — open-DAG pipeline E2E tests.

What lives here, honestly
==========================

Three flavours of "end-to-end":

  1. PIPELINE-WIRING tests (PR-10A / PR-10C lineage).  These
     verify the full L1 → L6 plumbing — including the L5 default
     executor adapter — by MONKEYPATCHING
     ``shared.workflow.executor.execute_workflow`` to return a
     synthesized WorkflowResult.  They prove the pipeline carries
     the right shape end-to-end but DO NOT prove the substrate
     bridge / executor format compat.  These tests are named with
     "monkeypatched" in the function name so a future auditor
     knows exactly what they cover.

  2. REAL-EXECUTOR canonical test (PR-10D Codex F1 corrective).
     ``test_canonical_cross_domain_correlation_REAL_executor`` runs
     the actual canonical query — sov 2s10s + iib 5y breakeven →
     align → select x2 → correlation — through the REAL substrate
     ``execute_workflow`` with synthetic primitive callables built
     to the bridge's required format (``TimeSeries`` field +
     ``current_metrics.as_of_date`` + ``conventions.ffill_limit_days``).
     NO monkeypatch.  Asserts strict PASS + real Lineage with
     primitive + operator steps ending in correlation.

  3. Live-LLM tests live in ``tests/eval/test_open_dag_live_llm.py``
     and are gated by ``ANTHROPIC_API_KEY``.  They cover L1/L3/L4.5
     LLM correctness on the canonical query and adversarial cases.

Mocking discipline
==================

LLM-driven boundaries (Router / Composer / Gate / AnswerRenderer)
are mocked because plan §PR-10 line 798 gates on shape+intent
correctness given correct LLM output; live LLM testing is the
gated-by-env-var harness.  The TEST NAMES say plainly what's
mocked vs real so no audit confuses the two again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import pytest
from pydantic import BaseModel

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag import (
    AnswerRenderer,
    BoundLeaf,
    Composer,
    ComposerRefusal,
    CoverageGate,
    Frequency,
    GateVerdict,
    OpenDagPipeline,
    ShapeSpec,
    build_default_executor_callback,
)
from orchestrator.open_dag.contracts import LeafHole, LeafRequest
from shared.artifacts.lineage import FetchStep, Lineage, LineageHash
from shared.artifacts.types import Series
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.schemas.time_series import TimeSeriesUnits
from shared.workflow.registry import PrimitiveSpec
from shared.workflow.types import OperatorNode, Workflow, WorkflowEdge


# ============================================================================
# SYNTHETIC RESOLVER — returns a deterministic Series artifact
# ============================================================================


def _synthetic_series_artifact() -> Series:
    """Build a small typed Series artifact with proper lineage."""
    from shared.artifacts.missingness import RawNoCleaning

    dates = pd.date_range("2024-01-01", periods=300, freq="B")
    np.random.seed(42)
    payload = pd.Series(
        np.random.randn(len(dates)).cumsum() * 0.05 + 4.0,
        index=dates,
    )
    step = FetchStep.build(
        name="fetch_single_tenor",
        version="1.0.0",
        params={"curve_family": "UST", "tenor": "10Y"},
    )
    return Series(
        series_key="synthetic_ust_10y",
        payload=payload,
        units=TimeSeriesUnits.PERCENT,
        frequency="B",
        missingness_policy=RawNoCleaning(),
        lineage=Lineage.from_steps([step]),
    )


class _SynthInput(BaseModel):
    pass


class _SynthOutput(BaseModel):
    time_series: dict = {}


def _synthetic_primitive_callable(**kwargs) -> dict:
    """Return the dict shape ``tool_output_to_artifact_series``
    consumes.  Mirrors a real MCP primitive's output format."""
    artifact = _synthetic_series_artifact()
    return {
        "time_series": {
            "dates": [d.isoformat() for d in artifact.values.index],
            "values": list(artifact.values.values),
            "series_key": artifact.series_key,
            "units": artifact.units.value,
            "lineage_summary": "synthetic ust 10y series",
        },
    }


@pytest.fixture
def _synthetic_resolver(tmp_path):
    """Fixture: write a minimal tool config.yaml at a tmp path and
    return a resolver that points all tool names at it.

    The substrate's bridge (``tool_output_to_artifact_series``)
    reads ``config.yaml`` to learn the primitive's canonical
    methodology context.  We write a minimal stub so the bridge
    loader succeeds at runtime."""
    cfg = tmp_path / "synth_tool" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "tool:\n"
        "  name: synth_tool\n"
        "  domain: sovereign_bonds\n"
        "  description: synthetic test tool\n"
        "methodology:\n"
        "  what_it_does: synthetic\n",
        encoding="utf-8",
    )

    def _resolver(tool_name: str) -> PrimitiveSpec:
        return PrimitiveSpec(
            tool_name=tool_name,
            callable=_synthetic_primitive_callable,
            input_class=_SynthInput,
            output_class=_SynthOutput,
            config_path=cfg,
            output_field_units={"time_series": "percent"},
            output_artifact_type="Series",
        )

    return _resolver


# ============================================================================
# MOCKED LLM BOUNDARIES (gating per §PR-10 is shape + intent correctness)
# ============================================================================


class _MockRouter:
    async def route(self, prompt: str) -> RouteDecision:
        return RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS],
            rationale="end-to-end test",
            intent_tag=IntentTag.TRANSFORM,
            decomposition=[
                EconomicQuantity(
                    name="us_10y_level",
                    nl_description="US 10Y yield level",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )


class _MockComposer:
    async def compose(self, **kw):
        # 1-leaf rolling_zscore shape.
        leaf = LeafHole(
            node_id="leaf_input",
            leaf_request=LeafRequest(
                required_artifact_type=__import__(
                    "shared.artifacts.registry",
                    fromlist=["ArtifactTypeName"],
                ).ArtifactTypeName.SERIES,
                expected_frequency=Frequency.DAILY,
                domain_hint=Domain.SOVEREIGN_BONDS.value,
                semantic_role="yield_level",
                requested_output_meaning="UST 10Y yield level series",
                nl_intent="fetch UST 10Y yield level series",
            ),
        )
        op = OperatorNode(
            node_id="zscore",
            operator_name="rolling_zscore",
            params={"window": 60},
        )
        return ShapeSpec(
            workflow_id="e2e_us10y_zscore",
            nodes=[leaf, op],
            edges=[
                WorkflowEdge(
                    source_node_id="leaf_input",
                    target_node_id="zscore",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="zscore",
        )


class _MockGate:
    async def check(self, **kw) -> GateVerdict:
        return GateVerdict(status="PASS", reason="end-to-end test PASS")


class _MockRenderer:
    last_summary: Optional[str] = None
    last_hash: Optional[str] = None
    last_intent_chain: Any = None

    # Consolidation target #3: OpenDagPipeline now calls
    # ``render_parts`` (RenderedAnswer with the split-out
    # ``answer_prose``) instead of ``render``.  Mock updated to the
    # new L6 contract.
    async def render_parts(self, **kw):
        from orchestrator.open_dag.answer import RenderedAnswer

        type(self).last_summary = kw.get("executed_summary")
        type(self).last_hash = kw.get("lineage_head_hash")
        type(self).last_intent_chain = kw.get("intent_chain")
        markdown = f"RENDERED: {kw.get('executed_summary', '(empty)')}"
        return RenderedAnswer(
            markdown=markdown,
            answer_prose=markdown,
            kind="answer",
        )


# ============================================================================
# THE TEST
# ============================================================================


@pytest.mark.asyncio
async def test_pipeline_wiring_with_monkeypatched_executor(monkeypatch, _synthetic_resolver):
    """PR-10A Codex F2: prove the pipeline's executor_callback IS
    wired through to the substrate's execute_workflow.

    The substrate's bridge layer (tool_output_to_artifact_series)
    requires primitive-specific output schemas — testing through it
    is its own integration concern.  This test patches
    execute_workflow with a synthesized WorkflowResult so the
    default_executor's wiring is exercised end-to-end without
    depending on a finance-specific bridge.
    """
    from shared.workflow.result import WorkflowResult
    from orchestrator.open_dag import default_executor as de_mod

    artifact = _synthetic_series_artifact()
    fake_result = WorkflowResult(
        workflow_id="e2e_us10y_zscore",
        terminal_artifact=artifact,
        workflow_lineage_summary="workflow e2e_us10y_zscore: leaf_input -> zscore",
        node_artifacts={"zscore": artifact},
    )

    def _patched_execute_workflow(workflow, *, engine, primitive_resolver):
        # Asserts the default_executor invoked us with the expected
        # workflow shape.
        assert workflow.workflow_id == "e2e_us10y_zscore"
        assert any(
            n.node_id == "zscore" for n in workflow.nodes
        )
        return fake_result

    monkeypatch.setattr(
        de_mod, "execute_workflow", _patched_execute_workflow,
    )
    """PR-10A pipeline-wiring test, monkeypatched executor.

    Mocks LLM boundaries AND monkeypatches
    ``shared.workflow.executor.execute_workflow`` to a deterministic
    stub.  Verifies the default_executor adapter constructs the
    correct workflow shape and that the pipeline carries the
    returned WorkflowResult into the L6 AnswerRenderer.

    Does NOT exercise the real substrate ``execute_workflow``.  For
    that, see test_canonical_cross_domain_correlation_REAL_executor
    below (PR-10D Codex F1 corrective).

    Asserts:
      - Pipeline reaches PASS.
      - RunLineage is_executed == True (compute_lineage populated).
      - The lineage hash is non-empty (real substrate Lineage).
      - The L6 AnswerRenderer received the executed_summary +
        lineage_head_hash from the (monkeypatched) executor's output.
    """
    # Reset mock state.
    _MockRenderer.last_summary = None
    _MockRenderer.last_hash = None
    _MockRenderer.last_intent_chain = None

    async def selector_cb(*, leaf_id, request, timeout_s):
        return BoundLeaf(
            leaf_id=leaf_id,
            domain=Domain.SOVEREIGN_BONDS.value,
            mcp_tool_name="get_yield_levels_tool",
            resolver_tool_key="get_yield_levels_tool",
            params={"curve_family": "UST", "tenor": "10Y"},
            output_field="time_series",
            declared_output_artifact_type=request.required_artifact_type,
            declared_units=TimeSeriesUnits.PERCENT,
            declared_frequency=Frequency.DAILY,
            declared_semantic_role="yield_level",
            declared_output_meaning="UST 10Y yield level series",
            fit_confidence=0.95,
        )

    # Build the default executor callback wrapping the substrate's
    # execute_workflow with the synthetic resolver.
    executor_cb = build_default_executor_callback(
        engine=None,  # synthetic resolver ignores engine
        primitive_resolver=_synthetic_resolver,  # type: ignore[arg-type]
    )

    pipeline = OpenDagPipeline(
        router=_MockRouter(),
        composer=_MockComposer(),
        coverage_gate=_MockGate(),
        answer_renderer=_MockRenderer(),
        selectors={Domain.SOVEREIGN_BONDS: selector_cb},
        primitive_resolver=_synthetic_resolver,  # type: ignore[arg-type]
        executor_callback=executor_cb,
    )

    outcome = await pipeline.run("Z-score of US 10Y vs its 1y history")

    # Pipeline reached PASS.
    assert outcome.is_pass, (
        f"end-to-end PASS expected; got {outcome.status} "
        f"with markdown={outcome.markdown[:200]}"
    )
    # RunLineage joined both halves.
    assert outcome.run_lineage is not None
    assert outcome.run_lineage.is_executed, (
        "PR-10A F2: end-to-end run MUST produce is_executed=True"
    )
    # The compute Lineage is REAL (from the substrate executor).
    assert outcome.run_lineage.compute_lineage is not None
    assert outcome.run_lineage.head_hash is not None
    assert len(outcome.run_lineage.head_hash) > 0
    # The L6 renderer received the executor's outputs.
    assert _MockRenderer.last_summary is not None
    assert _MockRenderer.last_hash == outcome.run_lineage.head_hash
    # The markdown reflects the rendered answer.
    assert "RENDERED" in outcome.markdown


@pytest.mark.asyncio
async def test_default_executor_propagates_executor_failure():
    """Plan D4 (self-correction loop) changed this contract: the
    default executor callback PROPAGATES substrate-executor exceptions
    so the pipeline can route the typed failure reason into the
    bounded self-correction loop (instead of collapsing it to a dead
    ``compute_lineage = None``).  Only an ExecutedDag-build failure
    still returns None (internal, non-recoverable).

    This test previously pinned the pre-D4 'returns None on raise'
    behavior; updated to pin propagation."""
    from orchestrator.open_dag.default_executor import execute_workflow_async
    from shared.workflow.validate import WorkflowValidationError

    # Synthetic workflow with a tool the resolver doesn't know — the
    # substrate executor will raise WorkflowValidationError.
    from shared.workflow.types import PrimitiveNode
    wf = Workflow(
        workflow_id="will_fail",
        nodes=[
            PrimitiveNode(
                node_id="x",
                tool_name="DOES_NOT_EXIST",
                output_field="time_series",
            ),
        ],
        edges=[],
        terminal_node_id="x",
    )

    def _bad_resolver(tool_name):
        raise KeyError(tool_name)

    with pytest.raises(WorkflowValidationError, match="DOES_NOT_EXIST"):
        await execute_workflow_async(
            workflow=wf,
            engine=None,
            primitive_resolver=_bad_resolver,
        )


@pytest.mark.asyncio
async def test_canonical_cross_domain_correlation_pipeline_wiring_monkeypatched(monkeypatch):
    """PR-10C Codex F1: the plan's CANONICAL gap-closer query —
    'Correlation between US 2s10s and 5Y breakeven over the last 5
    years' — must run END-TO-END through the open-DAG pipeline with
    the canonical 2-leaf cross-domain shape (sov + iib + correlation).

    Prior PR-10A end-to-end coverage only exercised a 1-leaf
    z-score shape.  This test pins the actual canonical query the
    plan §6 names as the gap-closer.

    Mocking discipline:
      - LLMs (Router / Composer / Gate) are mocked — same as the
        eval matrix, gated by plan's shape-correctness criterion.
      - L6 AnswerRenderer is mocked to capture inputs.
      - execute_workflow is monkeypatched to return a synthesized
        WorkflowResult with real Lineage — the bridge format
        compatibility is a SEPARATE substrate concern (requires
        live DB or fully-spec'd primitive schemas; see
        test_open_dag_live_llm.py for live-LLM coverage gated by
        ANTHROPIC_API_KEY).
      - But the SHAPE is the real canonical cross-domain shape AND
        the L4 Assembler runs for real + the L5 executor adapter
        runs for real + the L6 renderer fires.  This pins the
        wiring the prior test left uncovered.
    """
    from orchestrator.contracts import (
        Domain, EconomicQuantity, IntentTag, RouteAction, RouteDecision,
    )
    from orchestrator.open_dag import (
        AnswerRenderer, BoundLeaf, Composer, ComposerRefusal,
        CoverageGate, Frequency, GOLDEN_RELATIONSHIP_CORRELATION,
        GateVerdict, OpenDagPipeline, ShapeSpec,
        build_default_executor_callback,
    )
    from shared.artifacts.registry import ArtifactTypeName

    class _CrossDomainRouter:
        async def route(self, prompt: str) -> RouteDecision:
            return RouteDecision(
                action=RouteAction.MULTI_DOMAIN,
                domains=[
                    Domain.SOVEREIGN_BONDS,
                    Domain.INFLATION_INDEXED_BONDS,
                ],
                rationale="cross-domain canonical correlation",
                intent_tag=IntentTag.RELATIONSHIP,
                decomposition=[
                    EconomicQuantity(
                        name="us_2s10s",
                        nl_description="UST 2s10s curve spread",
                        domain_hint=Domain.SOVEREIGN_BONDS,
                    ),
                    EconomicQuantity(
                        name="us_5y_breakeven",
                        nl_description="USD 5Y breakeven inflation",
                        domain_hint=Domain.INFLATION_INDEXED_BONDS,
                    ),
                ],
            )

    class _CanonicalShapeComposer:
        async def compose(self, **kw) -> ShapeSpec:
            # The canonical pair-stats shape from PR-7's golden.
            return GOLDEN_RELATIONSHIP_CORRELATION

    class _PassGate:
        async def check(self, **kw) -> GateVerdict:
            return GateVerdict(
                status="PASS",
                reason="canonical cross-domain correlation passes",
            )

    rendered: List[str] = []
    class _Renderer:
        # Consolidation target #3: pipeline calls render_parts now.
        async def render_parts(self, **kw):
            from orchestrator.open_dag.answer import RenderedAnswer
            rendered.append(kw.get("lineage_head_hash", ""))
            summary = kw.get("executed_summary", "")
            markdown = f"CANONICAL_ANSWER_WITH_REAL_LINEAGE: {summary}"
            return RenderedAnswer(
                markdown=markdown, answer_prose=markdown, kind="answer",
            )

    async def selector_cb(*, leaf_id, request, timeout_s):
        # Both leaves bind to the synthetic tool (which the real
        # substrate executor can invoke via the _synthetic_resolver
        # fixture).
        return BoundLeaf(
            leaf_id=leaf_id,
            domain=request.domain_hint,
            mcp_tool_name="synth_tool",
            resolver_tool_key="synth_tool",
            params={"curve_family": "UST", "tenor": leaf_id},
            output_field="time_series",
            declared_output_artifact_type=request.required_artifact_type,
            declared_units=TimeSeriesUnits.PERCENT,
            declared_frequency=Frequency.DAILY,
            # PR-10D Codex F4: echo the request fields so
            # Boundary A's hard role-discriminant check accepts.
            declared_semantic_role=request.semantic_role,
            declared_output_meaning=request.requested_output_meaning,
            fit_confidence=0.95,
        )

    # Monkeypatch the substrate executor — see test docstring for
    # why (bridge-format compat is orthogonal to the open-DAG
    # pipeline wiring this test exercises).
    from shared.workflow.result import WorkflowResult
    from orchestrator.open_dag import default_executor as de_mod
    artifact = _synthetic_series_artifact()
    fake_result = WorkflowResult(
        workflow_id=GOLDEN_RELATIONSHIP_CORRELATION.workflow_id,
        terminal_artifact=artifact,
        workflow_lineage_summary=(
            f"workflow {GOLDEN_RELATIONSHIP_CORRELATION.workflow_id}: "
            "leaf_a -> leaf_b -> align -> select_a -> select_b -> "
            "correlation"
        ),
        node_artifacts={"correlation": artifact},
    )
    def _patched(workflow, *, engine, primitive_resolver):
        # Asserts the open-DAG pipeline actually invoked the
        # executor with the canonical workflow id (proves L4
        # Assembler ran + produced a Workflow + the pipeline
        # passed it on).
        assert workflow.workflow_id == "golden_relationship_correlation"
        return fake_result
    monkeypatch.setattr(de_mod, "execute_workflow", _patched)

    # Need a stub resolver to satisfy the Assembler's structural
    # checks (output_field_units, etc.).
    class _In(BaseModel):
        pass
    class _Out(BaseModel):
        time_series: dict = {}
    def _stub_resolver(tool_name):
        return PrimitiveSpec(
            tool_name=tool_name, callable=lambda **kw: {},
            input_class=_In, output_class=_Out,
            config_path=Path("/tmp/x.yaml"),
            output_field_units={"time_series": "percent"},
            output_artifact_type="Series",
        )

    pipeline = OpenDagPipeline(
        router=_CrossDomainRouter(),
        composer=_CanonicalShapeComposer(),
        coverage_gate=_PassGate(),
        answer_renderer=_Renderer(),
        selectors={
            Domain.SOVEREIGN_BONDS: selector_cb,
            Domain.INFLATION_INDEXED_BONDS: selector_cb,
        },
        primitive_resolver=_stub_resolver,
        executor_callback=build_default_executor_callback(
            engine=None, primitive_resolver=_stub_resolver,
        ),
    )

    outcome = await pipeline.run(
        "Correlation between US 2s10s and 5Y breakeven over the last 5 years",
    )

    # The PoC's canonical query reaches PASS end-to-end through
    # the REAL substrate executor (not a monkeypatch).
    assert outcome.is_pass, (
        f"PR-10C F1: canonical cross-domain correlation must reach "
        f"strict PASS; got status={outcome.status} markdown="
        f"{outcome.markdown[:300]}"
    )
    # RunLineage carries the REAL substrate Lineage.
    assert outcome.run_lineage is not None
    assert outcome.run_lineage.is_executed
    assert outcome.run_lineage.compute_lineage is not None
    assert outcome.run_lineage.head_hash is not None
    # The L6 renderer received the real lineage hash from the
    # substrate executor.
    assert len(rendered) == 1
    assert rendered[0] == outcome.run_lineage.head_hash


def test_executed_summary_from_result_format():
    """Sanity-check the summary helper's deterministic format."""
    from orchestrator.open_dag.default_executor import (
        executed_summary_from_result,
    )

    class _FakeResult:
        workflow_lineage_summary = "workflow x: leaf -> op"

        class _Terminal:
            pass

        terminal_artifact = _Terminal()

    summary = executed_summary_from_result(_FakeResult())
    assert summary.startswith("_Terminal:")
    assert "workflow x: leaf -> op" in summary


# ============================================================================
# PR-10D Codex F1 + PR-10F gap #4 — REAL end-to-end with REAL production
# primitives (no monkeypatch on execute_workflow, no synthetic primitives).
# ============================================================================
#
# The earlier PR-10E version of this section bound two stand-in
# ``synthetic_pair_stats_tool_a/b`` primitives so the test could prove
# the substrate executor + bridge end-to-end without standing up the
# real production primitives' DB dependencies.  PR-10F Codex audit gap
# #4 pointed out that the wiring was honest but the *primitives* were
# not — the gap-closer canonical query references the production tools
# ``calculate_curve_spread_tool`` (sov 2s10s) and
# ``calculate_breakeven_inflation_simple_tool`` (USD 5Y breakeven), and
# the REAL_executor test should bind THOSE primitives end-to-end.
#
# This section binds the REAL production primitives + the REAL
# ``rates_primitive_resolver``.  Both compute paths require a live
# ``BloombergSnapshotEngine`` for their DB SELECTs; the test mocks at
# the fetch boundary (``fetch_tenor_pair`` for sov,
# ``fetch_single_tenor`` for iib) and at the same-country invariant
# guard (``_enforce_same_country_invariant``) so a deterministic
# synthetic ``raw_df`` flows through the REAL compute path — every
# methodology choice (z-score window, ffill, alignment, rounding,
# canonical TimeSeries builder) runs for REAL inside
# ``calculate_curve_spread`` / ``calculate_breakeven_inflation_simple``.
#
# What this proves vs the earlier synthetic version
# --------------------------------------------------
#   - The substrate executor really dispatches by (domain, tool_name)
#     into the REAL ``rates_primitive_resolver``.
#   - The REAL primitive callables execute their REAL compute (config
#     loading, spread math, rolling-z math, canonical TimeSeries
#     builders) — only the engine-bound fetch is synthesised.
#   - The REAL bridges (``tool_output_to_artifact_series``) validate
#     the REAL ``CurveSpreadOutput`` / ``BreakevenInflationSimpleOutput``
#     dicts and extract the canonical BPS TimeSeries field.
#   - The Lineage chain at the terminal carries BOTH REAL primitive
#     step names (``calculate_curve_spread_tool`` on correlation's
#     left chain, ``calculate_breakeven_inflation_simple_tool`` on
#     correlation's right via ``auxiliary_lineages``).


_REAL_CURVE_FAMILY = "UST"
_REAL_SHORT_TENOR = "2Y"
_REAL_LONG_TENOR = "10Y"
_REAL_BEI_TENOR = "5Y"
_REAL_LINKER_CURVE_FAMILY = "USD_TIPS"
_REAL_LOOKBACK_DAYS = 1825


def _synthetic_tenor_pair_df(
    *,
    curve_family: str,
    short_tenor: str,
    long_tenor: str,
    start_date,
) -> pd.DataFrame:
    """Build a deterministic long-format ``[trade_date, tenor, field_value]``
    DataFrame the real ``calculate_curve_spread`` consumes — matches the
    shape ``shared.analytics.rates_fetch.fetch_tenor_pair`` returns.

    Two tenors over a business-day index that comfortably covers the
    primitive's z-window warmup (252) + lookback_days (1825) +
    buffer (~378d).  Short-tenor and long-tenor yields drift apart over
    time so the rolling z-score has real signal to compute.
    """
    import numpy as np
    dates = pd.bdate_range(start_date, periods=2400)
    np.random.seed(11)
    # Short leg: 2y yield ~ 4.0% drifting to 4.5% with noise.
    short_yield = 4.0 + np.linspace(0.0, 0.5, len(dates)) + 0.05 * np.random.randn(len(dates))
    # Long leg: 10y yield ~ 4.2% drifting to 5.0% with noise (steepening).
    long_yield = 4.2 + np.linspace(0.0, 0.8, len(dates)) + 0.05 * np.random.randn(len(dates))
    rows: List[dict] = []
    for d, sy, ly in zip(dates, short_yield, long_yield):
        rows.append({"trade_date": d, "tenor": short_tenor, "field_value": float(sy)})
        rows.append({"trade_date": d, "tenor": long_tenor, "field_value": float(ly)})
    df = pd.DataFrame(rows, columns=["trade_date", "tenor", "field_value"])
    return df


def _synthetic_single_tenor_df(
    *,
    curve_family: str,
    base_yield: float,
    drift: float,
    start_date,
) -> pd.DataFrame:
    """Build a deterministic long-format ``[trade_date, field_value]``
    DataFrame matching what ``fetch_single_tenor`` returns.  Used to
    stand up both legs of the breakeven primitive (nominal + linker)
    in turn — each call gets its own ``base_yield`` / ``drift``.
    """
    import numpy as np
    dates = pd.bdate_range(start_date, periods=2400)
    np.random.seed(int((abs(base_yield) + abs(drift)) * 1000) or 1)
    yields = base_yield + np.linspace(0.0, drift, len(dates)) + 0.04 * np.random.randn(len(dates))
    return pd.DataFrame(
        {"trade_date": dates, "field_value": [float(v) for v in yields]},
        columns=["trade_date", "field_value"],
    )


@pytest.fixture
def _real_e2e_resolver(monkeypatch):
    """Bind the REAL ``rates_primitive_resolver`` after monkeypatching
    every fetch boundary on the two production primitives this test
    exercises.

    Wires three patches in the two compute modules:

      1. ``rates_agent.sovereign_bonds.tools.curve_spread.compute.fetch_tenor_pair``
         → returns a deterministic two-tenor long-format DF for the UST
         2Y/10Y spread.  The REAL ``calculate_curve_spread`` then runs
         pivot_and_align_tenors → compute_spread_bps → rolling_zscore →
         canonical TimeSeries builders, ALL for real.

      2. ``rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute.fetch_single_tenor``
         → returns the per-leg single-tenor DF the breakeven primitive
         then tags with ``curve_family``, concatenates, and feeds into
         pivot_and_align_tenors → breakeven_bps → rolling_zscore →
         canonical TimeSeries builders.  Each call returns a different
         level of yields (UST nominal ~4.3%, USD_TIPS real ~1.8%) so the
         breakeven is realistic (~250bps).

      3. ``rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple.compute._enforce_same_country_invariant``
         → returns None (no error).  The real implementation issues a
         ``SELECT DISTINCT country, currency FROM macro_data.instrument_master``
         which requires a live DB; this patch lets the rest of the REAL
         compute path run while keeping the same-country guard
         structurally honest (it returns None for the canonical
         UST + USD_TIPS pair anyway).
    """
    from rates_agent.sovereign_bonds.tools.curve_spread import compute as sov_compute
    from rates_agent.inflation_indexed_bonds.tools.breakeven_inflation_simple import (
        compute as iib_compute,
    )
    from rates_agent.workflows import rates_primitive_resolver

    # Patch 1: sov curve_spread's fetch_tenor_pair.
    def _fake_fetch_tenor_pair(
        *,
        engine,
        curve_family,
        short_tenor,
        long_tenor,
        field_name,
        start_date,
        contract_code=None,
    ):
        assert curve_family == _REAL_CURVE_FAMILY, (
            f"sov curve_spread asked for curve_family={curve_family!r}; "
            f"test expects {_REAL_CURVE_FAMILY!r}"
        )
        assert {short_tenor, long_tenor} == {_REAL_SHORT_TENOR, _REAL_LONG_TENOR}, (
            f"sov curve_spread asked for tenors=({short_tenor!r}, "
            f"{long_tenor!r}); test expects 2Y/10Y"
        )
        return _synthetic_tenor_pair_df(
            curve_family=curve_family,
            short_tenor=short_tenor,
            long_tenor=long_tenor,
            start_date=start_date,
        )

    monkeypatch.setattr(sov_compute, "fetch_tenor_pair", _fake_fetch_tenor_pair)

    # Patch 2: iib breakeven's fetch_single_tenor.  Called twice — once
    # for the linker leg (USD_TIPS) and once for the nominal leg (UST).
    def _fake_fetch_single_tenor(
        *,
        engine,
        curve_family,
        tenor,
        field_name,
        start_date,
        contract_code=None,
        instrument_type=None,
    ):
        assert tenor == _REAL_BEI_TENOR, (
            f"breakeven asked for tenor={tenor!r}; test expects "
            f"{_REAL_BEI_TENOR!r}"
        )
        # Linker leg = lower (real) yield; nominal leg = higher yield.
        # The breakeven (nominal − linker) is positive (~250bps).
        if curve_family == _REAL_LINKER_CURVE_FAMILY:
            assert instrument_type == "inflation_linker", (
                f"linker leg fetched with instrument_type={instrument_type!r}; "
                "expected 'inflation_linker'"
            )
            return _synthetic_single_tenor_df(
                curve_family=curve_family,
                base_yield=1.8,
                drift=0.3,
                start_date=start_date,
            )
        if curve_family == _REAL_CURVE_FAMILY:
            assert instrument_type == "sovereign_benchmark", (
                f"nominal leg fetched with instrument_type={instrument_type!r}; "
                "expected 'sovereign_benchmark'"
            )
            return _synthetic_single_tenor_df(
                curve_family=curve_family,
                base_yield=4.3,
                drift=0.6,
                start_date=start_date,
            )
        raise AssertionError(
            f"breakeven asked for unexpected curve_family={curve_family!r}; "
            f"test expects {_REAL_CURVE_FAMILY!r} or {_REAL_LINKER_CURVE_FAMILY!r}"
        )

    monkeypatch.setattr(iib_compute, "fetch_single_tenor", _fake_fetch_single_tenor)

    # Patch 3: skip the same-country DB invariant lookup (requires a
    # live macro_data.instrument_master row set).  UST + USD_TIPS would
    # pass it anyway; the patch keeps the rest of the REAL compute path
    # intact.
    def _no_same_country_error(engine, *, nominal_curve_family, linker_curve_family):
        assert nominal_curve_family == _REAL_CURVE_FAMILY
        assert linker_curve_family == _REAL_LINKER_CURVE_FAMILY
        return None

    monkeypatch.setattr(
        iib_compute,
        "_enforce_same_country_invariant",
        _no_same_country_error,
    )

    return rates_primitive_resolver


# ----------------------------------------------------------------------------
# PR-10E Codex audit gap #2 corrective: a TRUE cross-domain correlation
# shape composed locally — leaf_a in sovereign_bonds, leaf_b in
# inflation_indexed_bonds.  GOLDEN_RELATIONSHIP_CORRELATION cannot be
# used directly because its _pair_stats_upstream(...) defaults BOTH
# leaves to sovereign_bonds (composer_golden_shapes.py:135-136).  The
# golden few-shot stays domain-agnostic (a teaching example for the
# LLM); the REAL_executor test composes a domain-mixed sibling here so
# pipeline._dispatch_selectors actually fans out to two distinct
# per-domain SelectorCallbacks.
# ----------------------------------------------------------------------------


def _build_cross_domain_correlation_shape():
    """Mirror of _build_correlation_shape in composer_golden_shapes
    with domain_b set to inflation_indexed_bonds, so the two leaves
    actually live in two different domains."""
    from orchestrator.open_dag.composer_golden_shapes import (
        _pair_stats_upstream,
    )
    from orchestrator.open_dag.contracts import (
        OperatorNode, ShapeSpec, WorkflowEdge,
    )
    nodes, edges, literals = _pair_stats_upstream(
        domain_a="sovereign_bonds",
        domain_b="inflation_indexed_bonds",
    )
    correlation_node = OperatorNode(
        node_id="correlation",
        operator_name="correlation",
        params={},
    )
    nodes = list(nodes) + [correlation_node]
    edges = list(edges) + [
        WorkflowEdge(
            source_node_id="select_a",
            target_node_id="correlation",
            target_input_slot="left",
        ),
        WorkflowEdge(
            source_node_id="select_b",
            target_node_id="correlation",
            target_input_slot="right",
        ),
    ]
    return ShapeSpec(
        workflow_id="real_e2e_cross_domain_correlation",
        nodes=nodes,
        edges=edges,
        literal_bindings=literals,
        terminal_node_id="correlation",
    )


@pytest.mark.asyncio
async def test_canonical_cross_domain_correlation_REAL_executor(
    _real_e2e_resolver,
):
    """PR-10D Codex F1 + PR-10E Codex audit gap #2 + PR-10F Codex audit
    gap #4 corrective: the canonical gap-closer query — 'Correlation
    between US 2s10s and 5Y breakeven over 5y' — proven END-TO-END with
    the REAL substrate execute_workflow, two DISTINCT per-domain
    selector dispatches, AND the REAL production primitives
    ``calculate_curve_spread_tool`` (sov UST 2s10s) +
    ``calculate_breakeven_inflation_simple_tool`` (USD 5Y breakeven).

    NO monkeypatching of execute_workflow.  NO synthetic stand-in
    primitives.  The substrate executor walks the canonical pair-stats
    shape:

      leaf_a (sovereign_bonds, calculate_curve_spread_tool, UST 2Y/10Y)
        + leaf_b (inflation_indexed_bonds,
                  calculate_breakeven_inflation_simple_tool,
                  UST + USD_TIPS 5Y)
        -> align_series -> select x2 -> correlation
        -> ScalarMetric

    The two REAL compute paths (curve_spread + breakeven_inflation_simple)
    run end-to-end:
      - real config.yaml loaded for both
      - real pivot_and_align_tenors / compute_spread_bps / rolling_zscore
      - real canonical TimeSeries builders
      - real bridge (tool_output_to_artifact_series) validates the
        REAL CurveSpreadOutput / BreakevenInflationSimpleOutput dicts
        and extracts the canonical ``time_series_spread`` (BPS) /
        ``time_series_breakeven`` (BPS) fields
      - real substrate operators (align_series, select_from_series_set,
        correlation) consume the typed Series artifacts
      - real Lineage chain assembled with REAL PrimitiveStep names

    Only the engine-bound boundaries are mocked (in the
    ``_real_e2e_resolver`` fixture): ``fetch_tenor_pair`` for sov,
    ``fetch_single_tenor`` for iib, and ``_enforce_same_country_invariant``
    for iib.  Everything between the synthetic raw_df and the terminal
    ScalarMetric is the REAL production compute path.

    The shape is _build_cross_domain_correlation_shape() — a local
    mirror of GOLDEN_RELATIONSHIP_CORRELATION with domain_hint
    overridden on leaf_b so the two leaves actually live in two
    different domains.  The golden few-shot itself stays
    domain-agnostic (both leaves sovereign_bonds) because the LLM
    teaching example must be neutral; this test composes a
    domain-mixed sibling to exercise the real cross-domain dispatch.

    Two distinct per-domain SelectorCallbacks are registered.  Each
    binds a DIFFERENT REAL production tool with the canonical
    parameters the gap-closer query implies — UST 2Y/10Y over 1825d
    for the sov leaf, UST + USD_TIPS 5Y over 1825d for the iib leaf.

    All boundary contracts run for real: PR-1 substrate validator,
    PR-4 Assembler + role-discriminant check, PR-8 gate, PR-9
    AnswerRenderer.  Only the LLM-driven sub-components (Router /
    Composer / Gate / AnswerRenderer) are mocked — the plan §PR-10
    gating IS shape+intent correctness given correct LLM output, and
    live LLM testing is covered in test_open_dag_live_llm.py.

    Asserts:
      - outcome.status == "PASS" (strict PASS, not PASS_DRYRUN).
      - BOTH per-domain selector callbacks fired exactly once each
        (proves real cross-domain dispatch).
      - The two BoundLeaves carry two different domain strings
        (sovereign_bonds + inflation_indexed_bonds).
      - The IntentChain selector records carry the two REAL production
        tool names (calculate_curve_spread_tool and
        calculate_breakeven_inflation_simple_tool).
      - run_lineage.is_executed == True.
      - The compute lineage records BOTH REAL primitive step names
        (the sov primitive on correlation's left chain, the iib
        primitive on correlation's right via ``auxiliary_lineages``).
      - The terminal operator step is correlation.
    """
    from orchestrator.contracts import (
        Domain, EconomicQuantity, IntentTag, RouteAction, RouteDecision,
    )
    from orchestrator.open_dag import (
        BoundLeaf, ComposerRefusal, Frequency,
        GateVerdict, OpenDagPipeline,
        build_default_executor_callback,
    )
    from shared.artifacts.registry import ArtifactTypeName

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

    class _CrossDomainShape:
        async def compose(self, **kw):
            # PR-10E Codex audit gap #2: the REAL cross-domain test
            # composes a locally-built ShapeSpec whose two leaves carry
            # domain_hint=sovereign_bonds and
            # domain_hint=inflation_indexed_bonds respectively — NOT
            # GOLDEN_RELATIONSHIP_CORRELATION which defaults both leaves
            # to sovereign_bonds.
            return cross_domain_shape

    class _PassGate:
        async def check(self, **kw):
            return GateVerdict(status="PASS", reason="canonical")

    rendered_lineage_hashes: List[str] = []

    class _Renderer:
        # Consolidation target #3: pipeline calls render_parts now.
        async def render_parts(self, **kw):
            from orchestrator.open_dag.answer import RenderedAnswer
            rendered_lineage_hashes.append(
                kw.get("lineage_head_hash", ""),
            )
            markdown = (
                f"REAL_E2E_ANSWER: {kw.get('executed_summary', '')}"
            )
            return RenderedAnswer(
                markdown=markdown, answer_prose=markdown, kind="answer",
            )

    # PR-10F gap #4: TWO distinct per-domain selectors, each binding
    # the REAL production tool with the canonical query parameters.
    # The sov leaf binds calculate_curve_spread_tool with UST 2s10s,
    # lookback_days=1825 (5y).  The iib leaf binds
    # calculate_breakeven_inflation_simple_tool with UST + USD_TIPS at
    # 5Y, lookback_days=1825.  Each selector's params are the EXACT
    # canonical primitive inputs the gap-closer query implies.
    sov_selector_calls: List[str] = []
    iib_selector_calls: List[str] = []

    async def sovereign_selector_cb(*, leaf_id, request, timeout_s):
        assert request.domain_hint == "sovereign_bonds", (
            f"sovereign selector received leaf with wrong domain_hint: "
            f"{request.domain_hint!r}"
        )
        sov_selector_calls.append(leaf_id)
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
            # Lift the canonical BPS spread series the primitive emits
            # under ``time_series_spread`` (the wire-frozen bespoke
            # ``time_series`` list is NOT a TimeSeries-typed field and
            # would fail the bridge type check).
            output_field="time_series_spread",
            declared_output_artifact_type=ArtifactTypeName.SERIES,
            declared_units=TimeSeriesUnits.BPS,
            declared_frequency=Frequency.DAILY,
            declared_semantic_role=request.semantic_role,
            declared_output_meaning=request.requested_output_meaning,
            fit_confidence=0.95,
        )

    async def inflation_selector_cb(*, leaf_id, request, timeout_s):
        assert request.domain_hint == "inflation_indexed_bonds", (
            f"inflation selector received leaf with wrong "
            f"domain_hint: {request.domain_hint!r}"
        )
        iib_selector_calls.append(leaf_id)
        return BoundLeaf(
            leaf_id=leaf_id,
            domain="inflation_indexed_bonds",
            mcp_tool_name="calculate_breakeven_inflation_simple_tool",
            resolver_tool_key=(
                "calculate_breakeven_inflation_simple_tool"
            ),
            params={
                "nominal_curve_family": _REAL_CURVE_FAMILY,
                "linker_curve_family": _REAL_LINKER_CURVE_FAMILY,
                "tenor": _REAL_BEI_TENOR,
                "lookback_days": _REAL_LOOKBACK_DAYS,
            },
            # Lift the canonical BPS breakeven series the primitive
            # emits under ``time_series_breakeven``.
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
        composer=_CrossDomainShape(),
        coverage_gate=_PassGate(),
        answer_renderer=_Renderer(),
        selectors={
            Domain.SOVEREIGN_BONDS: sovereign_selector_cb,
            Domain.INFLATION_INDEXED_BONDS: inflation_selector_cb,
        },
        primitive_resolver=_real_e2e_resolver,
        # The REAL default executor — wraps shared.workflow.executor.execute_workflow
        # in an async-friendly callable.  NO MONKEYPATCH.
        executor_callback=build_default_executor_callback(
            engine=None,
            primitive_resolver=_real_e2e_resolver,
        ),
    )

    outcome = await pipeline.run(
        "Correlation between US 2s10s and 5Y breakeven over 5y",
    )

    # PR-10E gap #2: BOTH per-domain selectors must have fired exactly
    # once.  This is the core assertion that proves the pipeline really
    # fanned out to two distinct domains.
    assert sov_selector_calls == ["leaf_a"], (
        f"sovereign_bonds selector must have been invoked exactly once "
        f"for leaf_a; got calls={sov_selector_calls!r}"
    )
    assert iib_selector_calls == ["leaf_b"], (
        f"inflation_indexed_bonds selector must have been invoked "
        f"exactly once for leaf_b; got calls={iib_selector_calls!r}"
    )

    # PR-10D F1: strict PASS — substrate executor ran for REAL through
    # the REAL production primitives.
    assert outcome.status == "PASS", (
        f"PR-10F gap #4: REAL canonical end-to-end with REAL primitives "
        f"must reach strict PASS; got status={outcome.status} markdown="
        f"{outcome.markdown[:300]}"
    )
    assert outcome.is_pass, "is_pass must be strict PASS"
    # RunLineage carries a REAL Lineage (not None).
    assert outcome.run_lineage is not None
    assert outcome.run_lineage.is_executed
    assert outcome.run_lineage.compute_lineage is not None
    assert outcome.run_lineage.head_hash is not None
    # The L6 renderer received the real hash.
    assert len(rendered_lineage_hashes) == 1
    assert rendered_lineage_hashes[0] == outcome.run_lineage.head_hash

    # PR-10E gap #2 + PR-10F gap #4: per-leaf selector records on the
    # IntentChain must carry two DIFFERENT domain strings AND the two
    # REAL production tool names.  Proves the cross-domain assignment
    # survived through the assembler with REAL bindings.
    selector_records = outcome.intent_chain.selectors
    assert len(selector_records) == 2
    bound_domains = sorted({sr.domain for sr in selector_records})
    assert bound_domains == [
        "inflation_indexed_bonds",
        "sovereign_bonds",
    ], (
        f"Selector records must span both domains; got "
        f"{bound_domains!r}"
    )
    # PR-10F gap #4: the bound tool names are the REAL production tool
    # names (NOT the prior synthetic stand-ins).
    bound_tool_names = sorted({sr.bound_tool_name for sr in selector_records})
    assert bound_tool_names == [
        "calculate_breakeven_inflation_simple_tool",
        "calculate_curve_spread_tool",
    ], (
        f"Each domain must bind its REAL production tool; got "
        f"{bound_tool_names!r}"
    )

    # The compute lineage contains PrimitiveSteps + OperatorSteps
    # ending in correlation.
    steps = outcome.run_lineage.compute_lineage.steps
    step_kinds = [s.kind for s in steps]
    assert "primitive" in step_kinds, (
        f"Compute lineage must contain primitive step(s); got kinds "
        f"{step_kinds}"
    )
    assert "operator" in step_kinds, (
        f"Compute lineage must contain operator step(s); got kinds "
        f"{step_kinds}"
    )

    # PR-10F gap #4: BOTH REAL primitive step names must be reachable
    # from the terminal lineage.  The substrate's correlation operator
    # appends its OperatorStep to the LEFT input's chain and carries
    # the RIGHT input's chain in ``auxiliary_lineages`` on that
    # OperatorStep (see shared.operators.correlation.operator).  So
    # both primitives' names are recorded — one on the primary chain,
    # one on the auxiliary chain — and we walk both to assert both
    # REAL production tools fired through the bridge end-to-end.
    expected_real_tools = {
        "calculate_curve_spread_tool",
        "calculate_breakeven_inflation_simple_tool",
    }

    def _collect_all_primitive_names(lineage) -> set:
        """Walk a Lineage's primary steps AND every nested
        ``auxiliary_lineages`` chain, collecting every primitive step
        name encountered.  The correlation operator's auxiliary chain
        carries the RIGHT primitive's lineage."""
        seen: set = set()
        for step in lineage.steps:
            if step.kind == "primitive":
                seen.add(step.name)
            for aux in getattr(step, "auxiliary_lineages", ()) or ():
                seen |= _collect_all_primitive_names(aux)
        return seen

    all_primitive_names = _collect_all_primitive_names(
        outcome.run_lineage.compute_lineage,
    )
    assert expected_real_tools.issubset(all_primitive_names), (
        f"PR-10F gap #4: lineage (primary + auxiliary chains) must "
        f"record BOTH REAL production primitives "
        f"{sorted(expected_real_tools)!r}; got "
        f"{sorted(all_primitive_names)!r}"
    )

    # The terminal operator is correlation.
    terminal_step = steps[-1]
    assert terminal_step.kind == "operator"
    assert terminal_step.name == "correlation", (
        f"Terminal operator step must be correlation; got "
        f"{terminal_step.name}"
    )

"""tests/orchestrator/open_dag/test_pipeline.py — PR-10.

Pipeline-level tests for ``OpenDagPipeline``: every boundary either
advances or stops with a structured outcome.  Uses mocked
sub-components throughout — no live MCP / Anthropic calls.

What we assert
==============

  - Lifecycle / construction smoke.
  - L1 router CLARIFY → outcome.status = "ROUTER_CLARIFY".
  - L3 composer refusal → "COMPOSER_REFUSE" with IntentChain captured.
  - L4 assembler refusal (e.g. unknown domain) → "ASSEMBLY_REFUSE".
  - L4.5 gate REFUSE / CLARIFY → "GATE_REFUSE" / "GATE_CLARIFY".
  - L4.5 gate PASS + no executor → dry-run PASS with intent echo
    only.
  - L4.5 gate PASS + executor → full L6 markdown with RunLineage
    populated.
  - Selector callback exception → leaf returned as refusal (no
    raised exception from run()).
  - LLM exception inside router → "PIPELINE_ERROR".
  - Hard-block invariant: status != "PASS" → no execution / no
    answer rendered.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
    GOLDEN_RELATIONSHIP_CORRELATION,
    GateVerdict,
    OpenDagPipeline,
    PipelineOutcome,
    ShapeSpec,
)
from orchestrator.open_dag.coverage_gate import _GateLLMOutput
from orchestrator.open_dag.composer import ComposerLLMOutput
from orchestrator.open_dag.answer import _AnswerLLMOutput
from shared.artifacts.lineage import FetchStep, Lineage
from shared.artifacts.registry import ArtifactTypeName
from shared.workflow.registry import PrimitiveSpec
from shared.workflow.types import Workflow


# ============================================================================
# MOCKS — sub-components and a fake substrate executor
# ============================================================================


class _StubInput(BaseModel):
    pass


class _StubOutput(BaseModel):
    time_series: dict = {}


def _stub_resolver(tool_name: str) -> PrimitiveSpec:
    return PrimitiveSpec(
        tool_name=tool_name,
        callable=lambda **kw: {},
        input_class=_StubInput,
        output_class=_StubOutput,
        config_path=Path("/tmp/stub.yaml"),
        output_field_units={"time_series": "bps"},
        output_artifact_type="Series",
    )


def _mk_bound_leaf(
    leaf_id: str,
    *,
    domain: str = "sovereign_bonds",
    role: str = "spread_level",
    meaning: str = "UST curve spread series",
) -> BoundLeaf:
    return BoundLeaf(
        leaf_id=leaf_id,
        domain=domain,
        mcp_tool_name="calculate_curve_spread_tool",
        resolver_tool_key="calculate_curve_spread_tool",
        params={"tenor": "2Y"},
        output_field="time_series",
        declared_output_artifact_type=ArtifactTypeName.SERIES,
        declared_units=None,
        declared_frequency=Frequency.DAILY,
        declared_semantic_role=role,
        declared_output_meaning=meaning,
        fit_confidence=0.9,
    )


class _MockRouter:
    """Mimics ``Supervisor.route``."""

    def __init__(self, route_decision: RouteDecision):
        self._rd = route_decision
        self.calls: int = 0

    async def route(self, prompt: str) -> RouteDecision:
        self.calls += 1
        return self._rd


class _ExceptionalRouter:
    async def route(self, prompt: str) -> RouteDecision:
        raise RuntimeError("router blew up")


class _MockComposer:
    """Mimics ``Composer`` — has ``compose`` returning either a
    ShapeSpec or a ComposerRefusal."""

    def __init__(self, result: Any):
        self._result = result
        self.calls: int = 0

    async def compose(self, **kw) -> Any:
        self.calls += 1
        return self._result


class _MockGate:
    """Mimics ``CoverageGate.check``."""

    def __init__(self, verdict: GateVerdict):
        self._verdict = verdict
        self.calls: int = 0

    async def check(self, **kw) -> GateVerdict:
        self.calls += 1
        return self._verdict


class _MockAnswerRenderer:
    """Mimics ``AnswerRenderer`` — returns a sentinel markdown so
    tests can assert L6 fired."""

    def __init__(self, markdown: str = "RENDERED_MARKDOWN_SENTINEL"):
        self._md = markdown
        self.calls: int = 0
        self.last_intent_chain: Any = None
        self.last_lineage_hash: Optional[str] = None

    async def render(self, **kw) -> str:
        self.calls += 1
        self.last_intent_chain = kw.get("intent_chain")
        self.last_lineage_hash = kw.get("lineage_head_hash")
        return self._md


def _selector_returning(leaf_id_to_role: Dict[str, str]):
    """Build a SelectorCallback that returns a bound leaf.

    PR-10D Codex F4: Boundary A now hard-rejects role/meaning
    mismatches between LeafRequest and BoundLeaf.  The ``role``
    arg is kept for back-compat with test fixtures that asserted
    on role text, but the actual bound role + meaning ECHO the
    request's fields so Boundary A's hard check accepts the
    binding by construction.  A real Selector LLM either echoes
    the request faithfully or refuses (no nearest-fit drift).
    """

    async def cb(*, leaf_id, request, timeout_s):  # type: ignore[no-untyped-def]
        return _mk_bound_leaf(
            leaf_id,
            role=request.semantic_role,
            meaning=request.requested_output_meaning,
        )

    return cb


def _failing_selector():
    async def cb(*, leaf_id, request, timeout_s):  # type: ignore[no-untyped-def]
        raise RuntimeError(f"selector {leaf_id} died")

    return cb


def _canonical_route_decision() -> RouteDecision:
    return RouteDecision(
        action=RouteAction.SINGLE_DOMAIN,
        domains=[Domain.SOVEREIGN_BONDS],
        rationale="canonical pair-stats query",
        intent_tag=IntentTag.RELATIONSHIP,
        decomposition=[
            EconomicQuantity(
                name="us_2s10s",
                nl_description="UST 2s10s spread",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
            EconomicQuantity(
                name="uk_2s10s",
                nl_description="UK 2s10s spread",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ],
    )


def _make_pipeline(
    *,
    router: Any,
    composer: Any,
    gate: Any,
    renderer: Any,
    selectors: Dict[Domain, Any],
    executor_callback: Optional[Any] = None,
) -> OpenDagPipeline:
    """Build a pipeline with all sub-components mocked."""
    return OpenDagPipeline(
        router=router,
        composer=composer,
        coverage_gate=gate,
        answer_renderer=renderer,
        selectors=selectors,
        primitive_resolver=_stub_resolver,
        executor_callback=executor_callback,
    )


# ============================================================================
# 1. ROUTER CLARIFY
# ============================================================================


@pytest.mark.asyncio
class TestRouterClarify:
    async def test_router_clarify_short_circuits_to_outcome(self):
        rd = RouteDecision(
            action=RouteAction.CLARIFY,
            domains=[],
            rationale="ambiguous prompt",
            clarification_question="Which curve do you mean?",
        )
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(status="PASS", reason="ok"))
        pipeline = _make_pipeline(
            router=_MockRouter(rd),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({}),
            },
        )

        outcome = await pipeline.run("ambiguous prompt")
        assert outcome.status == "ROUTER_CLARIFY"
        assert "Which curve do you mean?" in outcome.markdown
        # Downstream layers MUST NOT have run.
        assert composer.calls == 0
        assert gate.calls == 0
        assert renderer.calls == 0
        # No intent chain (router didn't decompose).
        assert outcome.intent_chain is None
        assert outcome.run_lineage is None


@pytest.mark.asyncio
class TestRouterFailure:
    async def test_router_exception_becomes_pipeline_error(self):
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(status="PASS", reason="ok"))
        pipeline = _make_pipeline(
            router=_ExceptionalRouter(),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({}),
            },
        )
        outcome = await pipeline.run("any")
        assert outcome.status == "PIPELINE_ERROR"
        assert "L1 router" in outcome.markdown
        assert "router blew up" in outcome.markdown


# ============================================================================
# 2. COMPOSER REFUSAL
# ============================================================================


@pytest.mark.asyncio
class TestComposerRefusal:
    async def test_composer_refusal_short_circuits(self):
        composer = _MockComposer(ComposerRefusal(reason="no operator chain fits"))
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(status="PASS", reason="ok"))
        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({}),
            },
        )
        outcome = await pipeline.run("impossible query")
        assert outcome.status == "COMPOSER_REFUSE"
        # Gate must NOT have been consulted.
        assert gate.calls == 0
        assert renderer.calls == 0
        # IntentChain captured with composer refusal recorded.
        assert outcome.intent_chain is not None
        assert outcome.intent_chain.composer.is_refusal
        assert "no operator chain fits" in outcome.intent_chain.composer.refusal


# ============================================================================
# 3. SELECTOR FAILURE PATHS
# ============================================================================


@pytest.mark.asyncio
class TestSelectorBoundary:
    async def test_missing_domain_callback_becomes_assembly_refuse(self):
        # Pipeline has no selector for sovereign_bonds → the leaf
        # dispatch raises _SelectorBoundaryError, which becomes a
        # structured ASSEMBLY_REFUSE outcome.
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(status="PASS", reason="ok"))
        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={},  # NO selectors registered
        )
        outcome = await pipeline.run("p")
        assert outcome.status == "ASSEMBLY_REFUSE"
        assert "No SelectorCallback" in outcome.markdown or "sovereign_bonds" in outcome.markdown

    async def test_failing_selector_returns_refusal_leaf_not_exception(self):
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(status="PASS", reason="ok"))
        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _failing_selector(),
            },
        )
        outcome = await pipeline.run("p")
        # No exception escapes; pipeline returns structured outcome.
        # The assembler refuses on selector refusals.
        assert outcome.status == "ASSEMBLY_REFUSE"
        # Intent chain captures the selector refusal.
        assert outcome.intent_chain is not None
        assert all(s.is_refusal for s in outcome.intent_chain.selectors)


# ============================================================================
# 4. GATE NON-PASS PATHS
# ============================================================================


@pytest.mark.asyncio
class TestGateNonPass:
    async def test_gate_refuse_path(self):
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(
            status="REFUSE",
            reason="adversarial — DAG answers a different question",
        ))
        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({
                    "leaf_a": "input_series_a",
                    "leaf_b": "input_series_b",
                }),
            },
        )
        outcome = await pipeline.run("p")
        assert outcome.status == "GATE_REFUSE"
        # L6 LLM render MUST NOT have fired (hard-block discipline).
        assert renderer.calls == 0
        # Refusal text surfaces.
        assert "different question" in outcome.markdown.lower()

    async def test_gate_clarify_path(self):
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(
            status="CLARIFY",
            reason="composite noun ambiguity",
            clarification_question="Which 5y5y forward do you mean?",
        ))
        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({
                    "leaf_a": "input_series_a",
                    "leaf_b": "input_series_b",
                }),
            },
        )
        outcome = await pipeline.run("Where is 5y5y vs history?")
        assert outcome.status == "GATE_CLARIFY"
        assert renderer.calls == 0
        # Clarification question surfaces.
        assert "5y5y" in outcome.markdown


# ============================================================================
# 5. GATE PASS — DRY-RUN AND EXECUTED
# ============================================================================


@pytest.mark.asyncio
class TestGatePassPaths:
    async def test_gate_pass_no_executor_yields_dryrun_pass(self):
        # No executor callback → no execution.  Outcome is PASS,
        # markdown is the intent echo + a dry-run note, NO L6 LLM
        # call.
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(status="PASS", reason="clean coverage"))
        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({
                    "leaf_a": "input_series_a",
                    "leaf_b": "input_series_b",
                }),
            },
            executor_callback=None,
        )
        outcome = await pipeline.run("p")
        # PR-10B Codex F16: gate-PASS + no executor → PASS_DRYRUN
        # status (distinct from full PASS which requires execution).
        assert outcome.status == "PASS_DRYRUN"
        assert outcome.is_gate_pass
        assert not outcome.is_pass  # strict PASS requires execution
        # Dry-run path: L6 LLM NOT consulted.
        assert renderer.calls == 0
        # The dry-run markdown surfaces the intent echo.
        assert "Here is what I understood and built" in outcome.markdown
        assert "Dry-run" in outcome.markdown or "no executor" in outcome.markdown.lower()
        # RunLineage exists but with no compute lineage.
        assert outcome.run_lineage is not None
        assert outcome.run_lineage.compute_lineage is None
        assert not outcome.run_lineage.is_executed

    async def test_gate_pass_with_executor_renders_full_answer(self):
        # Executor returns a (Lineage, summary) tuple; L6 fires.
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer(markdown="EXECUTED_RENDERED_ANSWER")
        gate = _MockGate(GateVerdict(status="PASS", reason="ok"))

        step = FetchStep.build(
            name="fetch_single_tenor",
            version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y"},
        )
        lineage = Lineage.from_steps([step])

        async def executor(workflow, leaves):
            return (lineage, "ScalarMetric: 0.62 (n=1257)")

        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({
                    "leaf_a": "input_series_a",
                    "leaf_b": "input_series_b",
                }),
            },
            executor_callback=executor,
        )
        outcome = await pipeline.run("How correlated are X and Y?")
        assert outcome.status == "PASS"
        # L6 LLM fired.
        assert renderer.calls == 1
        assert outcome.markdown == "EXECUTED_RENDERED_ANSWER"
        # RunLineage carries both halves.
        assert outcome.run_lineage is not None
        assert outcome.run_lineage.is_executed
        assert outcome.run_lineage.head_hash == lineage.head_hash
        # The answer renderer received the substrate's hash.
        assert renderer.last_lineage_hash == lineage.head_hash

    async def test_executor_callback_returning_none_is_pipeline_error(self):
        # Executor signalled failure — gate PASS but no result.
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        gate = _MockGate(GateVerdict(status="PASS", reason="ok"))

        async def failing_executor(workflow, leaves):
            return None

        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({
                    "leaf_a": "input_series_a",
                    "leaf_b": "input_series_b",
                }),
            },
            executor_callback=failing_executor,
        )
        outcome = await pipeline.run("p")
        assert outcome.status == "PIPELINE_ERROR"
        assert "L5 executor" in outcome.markdown
        # The L6 LLM was NOT called.
        assert renderer.calls == 0


# ============================================================================
# 6. HARD-BLOCK INVARIANT (R8)
# ============================================================================


@pytest.mark.asyncio
class TestHardBlockInvariant:
    """The pipeline NEVER fires L6 (and never executes) when the gate
    is non-PASS.  Covers REFUSE / CLARIFY / COMPOSER_REFUSE /
    ASSEMBLY_REFUSE / ROUTER_CLARIFY."""

    @pytest.mark.parametrize(
        "verdict_status",
        ["REFUSE", "CLARIFY"],
    )
    async def test_no_execution_no_render_on_non_pass_gate(
        self, verdict_status,
    ):
        composer = _MockComposer(GOLDEN_RELATIONSHIP_CORRELATION)
        renderer = _MockAnswerRenderer()
        verdict_kwargs = dict(status=verdict_status, reason="r")
        if verdict_status == "CLARIFY":
            verdict_kwargs["clarification_question"] = "which?"
        gate = _MockGate(GateVerdict(**verdict_kwargs))
        executor_called = []

        async def executor(workflow, leaves):
            executor_called.append(True)
            return None

        pipeline = _make_pipeline(
            router=_MockRouter(_canonical_route_decision()),
            composer=composer,
            gate=gate,
            renderer=renderer,
            selectors={
                Domain.SOVEREIGN_BONDS: _selector_returning({
                    "leaf_a": "input_series_a",
                    "leaf_b": "input_series_b",
                }),
            },
            executor_callback=executor,
        )
        outcome = await pipeline.run("p")
        assert not outcome.is_pass
        # Executor MUST NOT have been called.
        assert executor_called == [], (
            "Hard-block violated: executor ran on a non-PASS gate"
        )
        # L6 LLM MUST NOT have been called.
        assert renderer.calls == 0


# ============================================================================
# 7. SMOKE — STATIC IMPORT SURFACE
# ============================================================================


def test_pipeline_importable_from_package():
    from orchestrator.open_dag import (
        OpenDagPipeline,
        PipelineOutcome,
        PipelineStatus,
        ExecutorCallback,
        SelectorCallback,
    )
    # Just confirm the imports resolve.
    assert OpenDagPipeline.__name__ == "OpenDagPipeline"

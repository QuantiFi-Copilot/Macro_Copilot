"""tests/orchestrator/open_dag/test_pipeline_self_correction.py

Phase B of the orchestration-upgrade plan (Decision D1/D3, §5 state
machine): the BOUNDED SELF-CORRECTION loop.

Invariant under test (the one the owner insisted on): a wrong /
low-confidence DAG is NEVER executed.  On a recomposable failure
(deterministic terminal-shape mismatch, or a coverage-gate REFUSE) the
composer re-composes ONCE with the SPECIFIC reason; if the re-composed
DAG still fails, the pipeline HARD-BLOCKS.  Self-correction is a chance
to fix BEFORE the gate, never a way to bypass it.

All stubbed — no live LLM / MCP / Anthropic.  Drives the REAL assembler +
the REAL deterministic shape check (plan D2) with valid golden ShapeSpecs:
the rolling-zscore golden terminates in a Series; the summary golden in a
ScalarMetric.  So "expected_answer_shape=['scalar'] + zscore shape" is the
exact "average → built a Series" failure the loop must self-correct.
"""

from __future__ import annotations

from typing import Any, List, Optional

import pytest

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag import GateVerdict, OpenDagPipeline
from orchestrator.open_dag.composer_golden_shapes import (
    GOLDEN_SUMMARY_SINGLE_STAT,   # terminal = summarize_series → ScalarMetric
    GOLDEN_TRANSFORM_ROLLING_ZSCORE,  # terminal = rolling_zscore → Series
)

# Reuse the proven stub harness from the sibling pipeline test module.
from tests.orchestrator.open_dag.test_pipeline import (
    _MockAnswerRenderer,
    _MockRouter,
    _selector_returning,
    _stub_resolver,
)

pytestmark = pytest.mark.asyncio


# ============================================================================
# Sequence stubs — return a DIFFERENT result on each call (wrong → right),
# and record what they were called with (corrections / call counts).
# ============================================================================


class _SequenceComposer:
    """compose() returns results[i] for the i-th call (clamped to last),
    recording the ``correction`` kwarg per call."""

    def __init__(self, results: List[Any]):
        self._results = results
        self.calls = 0
        self.corrections: List[Optional[str]] = []

    async def compose(self, **kw) -> Any:
        self.corrections.append(kw.get("correction"))
        idx = min(self.calls, len(self._results) - 1)
        self.calls += 1
        return self._results[idx]


class _SequenceGate:
    """check() returns verdicts[i] for the i-th call (clamped to last)."""

    def __init__(self, verdicts: List[GateVerdict]):
        self._verdicts = verdicts
        self.calls = 0

    async def check(self, **kw) -> GateVerdict:
        idx = min(self.calls, len(self._verdicts) - 1)
        self.calls += 1
        return self._verdicts[idx]


class _RecordingExecutor:
    """Records whether L5 was ever reached.  Returns None (we only use it
    to assert it is NOT called on refuse paths)."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, workflow, bound_leaves):
        self.calls += 1
        return None


class _RaisingExecutor:
    """Mimics the production default_executor on a typed execute-time
    failure (plan D4): RAISES (carrying the reason) instead of returning
    None.  The pipeline routes this into the self-correction loop."""

    def __init__(self, msg: str = "RollingZscoreError: produced an all-NaN output (252 rows, window=252)"):
        self.calls = 0
        self._msg = msg

    async def __call__(self, workflow, bound_leaves):
        self.calls += 1
        raise RuntimeError(self._msg)


def _route(expected_shape: List[str]) -> RouteDecision:
    return RouteDecision(
        action=RouteAction.SINGLE_DOMAIN,
        domains=[Domain.SOVEREIGN_BONDS],
        rationale="single-series summary",
        intent_tag=IntentTag.LOOKUP,
        decomposition=[
            EconomicQuantity(
                name="us_2s10s",
                nl_description="UST 2s10s spread",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ],
        expected_answer_shape=expected_shape,
    )


def _pipeline(
    *,
    composer: Any,
    gate: Any,
    executor: Any = None,
    recompose_budget: int = 1,
) -> OpenDagPipeline:
    return OpenDagPipeline(
        router=_MockRouter(_route(["scalar"])),  # overridden per test below
        composer=composer,
        coverage_gate=gate,
        answer_renderer=_MockAnswerRenderer(),
        selectors={d: _selector_returning({}) for d in Domain},
        primitive_resolver=_stub_resolver,
        executor_callback=executor,
        recompose_budget=recompose_budget,
    )


def _pipeline_for(route: RouteDecision, **kw) -> OpenDagPipeline:
    p = _pipeline(**kw)
    p._router = _MockRouter(route)  # inject the route we want
    return p


_PASS = GateVerdict(status="PASS", reason="coverage ok")
_REFUSE = GateVerdict(status="REFUSE", reason="answers a different question")


# ============================================================================
# 1. Deterministic shape mismatch → self-correct → reaches execution
# ============================================================================


class TestSelfCorrectsShapeMismatch:
    async def test_wrong_shape_then_right_shape_self_corrects(self):
        # Attempt 1: zscore shape (Series terminal) vs expected ['scalar'] →
        # deterministic shape refusal → re-compose.  Attempt 2: summary shape
        # (ScalarMetric terminal) → satisfies → gate PASS → reaches L5
        # (PASS_DRYRUN, no executor wired).
        composer = _SequenceComposer(
            [GOLDEN_TRANSFORM_ROLLING_ZSCORE, GOLDEN_SUMMARY_SINGLE_STAT]
        )
        pipe = _pipeline_for(
            _route(["scalar"]), composer=composer, gate=_SequenceGate([_PASS])
        )
        outcome = await pipe.run("Average US 2s10s over 5y")
        assert outcome.status == "PASS_DRYRUN", outcome.markdown[:300]
        assert composer.calls == 2  # one self-correction
        # The 2nd compose carried a correction; the 1st did not.
        assert composer.corrections[0] is None
        assert composer.corrections[1] and "shape" in composer.corrections[1].lower()
        # Plan D9: the audit trace records the one self-correction.
        assert len(outcome.recompose_trace) == 1
        step = outcome.recompose_trace[0]
        assert step.attempt == 0
        assert step.failed_status == "ASSEMBLY_REFUSE"
        assert step.reason

    async def test_persistent_wrong_shape_hard_blocks_and_never_executes(self):
        # Both attempts produce a Series terminal vs expected ['scalar'].
        # After the one bounded re-compose the loop HARD-BLOCKS — and the
        # executor is NEVER reached.
        composer = _SequenceComposer(
            [GOLDEN_TRANSFORM_ROLLING_ZSCORE, GOLDEN_TRANSFORM_ROLLING_ZSCORE]
        )
        executor = _RecordingExecutor()
        pipe = _pipeline_for(
            _route(["scalar"]),
            composer=composer,
            gate=_SequenceGate([_PASS]),
            executor=executor,
        )
        outcome = await pipe.run("Average US 2s10s over 5y")
        assert outcome.status == "ASSEMBLY_REFUSE"
        assert composer.calls == 2  # 1 initial + 1 re-compose (budget)
        assert executor.calls == 0  # NEVER executed a wrong-shaped DAG


# ============================================================================
# 2. Gate REFUSE → self-correct (gate is the trigger, like today's bug)
# ============================================================================


class TestSelfCorrectsGateRefuse:
    async def test_gate_refuse_then_pass_self_corrects(self):
        # expected_answer_shape unconstrained so the deterministic check is a
        # no-op; the GATE is the trigger.  REFUSE then PASS → PASS_DRYRUN.
        composer = _SequenceComposer(
            [GOLDEN_SUMMARY_SINGLE_STAT, GOLDEN_SUMMARY_SINGLE_STAT]
        )
        gate = _SequenceGate([_REFUSE, _PASS])
        pipe = _pipeline_for(_route([]), composer=composer, gate=gate)
        outcome = await pipe.run("...")
        assert outcome.status == "PASS_DRYRUN"
        assert composer.calls == 2
        assert gate.calls == 2
        assert composer.corrections[1]  # reason threaded into re-compose
        # Plan D9: trace records the gate-refuse → re-compose event.
        assert len(outcome.recompose_trace) == 1
        assert outcome.recompose_trace[0].failed_status == "GATE_REFUSE"

    async def test_gate_refuse_twice_hard_blocks_and_never_executes(self):
        composer = _SequenceComposer([GOLDEN_SUMMARY_SINGLE_STAT])
        executor = _RecordingExecutor()
        pipe = _pipeline_for(
            _route([]),
            composer=composer,
            gate=_SequenceGate([_REFUSE]),  # always refuse
            executor=executor,
        )
        outcome = await pipe.run("...")
        assert outcome.status == "GATE_REFUSE"
        assert composer.calls == 2  # 1 + budget
        assert executor.calls == 0  # hard-block floor — never executes


# ============================================================================
# 3. Budget + unconstrained behavior
# ============================================================================


class TestSelfCorrectsExecutionError:
    """Plan D4: a typed execute-time failure (e.g. z-score all-NaN) is
    routed into the self-correction loop, then hard-blocks — it does NOT
    dead-end as PIPELINE_ERROR, and it never ships a wrong answer."""

    async def test_execution_error_routes_to_recompose_then_hard_blocks(self):
        composer = _SequenceComposer([GOLDEN_SUMMARY_SINGLE_STAT])  # valid shape; gate PASSes
        executor = _RaisingExecutor()
        pipe = _pipeline_for(
            _route(["scalar"]),
            composer=composer,
            gate=_SequenceGate([_PASS]),
            executor=executor,
        )
        outcome = await pipe.run("Z-score of US 2s10s vs its 1-year history")
        assert outcome.status == "EXECUTION_REFUSE"   # hard-block floor (not PIPELINE_ERROR)
        assert executor.calls == 2   # executed attempt 1 + the one re-composed attempt
        assert composer.calls == 2   # one bounded re-compose
        # The audit trace records the execution failure + its reason.
        assert len(outcome.recompose_trace) == 1
        assert outcome.recompose_trace[0].failed_status == "EXECUTION_REFUSE"
        assert "all-nan" in outcome.recompose_trace[0].reason.lower() \
            or "rolling" in outcome.recompose_trace[0].reason.lower()
        # The 2nd compose was given the execution reason.
        assert composer.corrections[1]

    async def test_returns_none_executor_stays_pipeline_error(self):
        # Opaque failure (executor returns None, no reason) is NOT
        # recomposable — stays PIPELINE_ERROR (back-compat).
        composer = _SequenceComposer([GOLDEN_SUMMARY_SINGLE_STAT])

        class _NoneExec:
            def __init__(self): self.calls = 0
            async def __call__(self, w, b):
                self.calls += 1
                return None

        ex = _NoneExec()
        pipe = _pipeline_for(
            _route(["scalar"]), composer=composer,
            gate=_SequenceGate([_PASS]), executor=ex,
        )
        outcome = await pipe.run("Average US 2s10s over 5y")
        assert outcome.status == "PIPELINE_ERROR"
        assert ex.calls == 1   # no re-compose on opaque None
        assert composer.calls == 1


class TestBudgetAndUnconstrained:
    async def test_budget_zero_disables_recompose(self):
        composer = _SequenceComposer([GOLDEN_SUMMARY_SINGLE_STAT])
        pipe = _pipeline_for(
            _route([]),
            composer=composer,
            gate=_SequenceGate([_REFUSE]),
            recompose_budget=0,
        )
        outcome = await pipe.run("...")
        assert outcome.status == "GATE_REFUSE"
        assert composer.calls == 1  # no re-compose when budget is 0

    async def test_unconstrained_shape_never_shape_refuses(self):
        # A Series terminal with NO shape contract (['any']/[]) passes the
        # deterministic check; only the gate governs.  Gate PASS → execute.
        composer = _SequenceComposer([GOLDEN_TRANSFORM_ROLLING_ZSCORE])
        pipe = _pipeline_for(
            _route(["any"]), composer=composer, gate=_SequenceGate([_PASS])
        )
        outcome = await pipe.run("Show me the z-score over time")
        assert outcome.status == "PASS_DRYRUN"
        assert composer.calls == 1  # no shape refusal, no re-compose

    async def test_satisfied_shape_first_try_no_recompose(self):
        # Right shape on the first try → no re-compose, straight through.
        composer = _SequenceComposer([GOLDEN_SUMMARY_SINGLE_STAT])
        pipe = _pipeline_for(
            _route(["scalar"]), composer=composer, gate=_SequenceGate([_PASS])
        )
        outcome = await pipe.run("Average US 2s10s over 5y")
        assert outcome.status == "PASS_DRYRUN"
        assert composer.calls == 1
        assert composer.corrections == [None]
        # Plan D9: no self-correction → empty trace.
        assert outcome.recompose_trace == ()

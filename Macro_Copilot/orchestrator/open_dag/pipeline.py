"""orchestrator.open_dag.pipeline — PR-10 of the open-DAG PoC.

The end-to-end **open-DAG pipeline lane**: L1 → L3 → L2 → L4 → L4.5
→ L5 → L6, stitched together as a single ``OpenDagPipeline`` class
that the orchestrator (``CopilotSession``) routes user prompts into
when the template lane doesn't match.

Layer stack (per the plan's eight-layer diagram)
================================================

  L0 Intake         — the caller's user_prompt string (passed in).
  L1 Router         — Supervisor.route(prompt) → RouteDecision
                      (action + intent_tag + decomposition + adjustments).
  L3 Composer       — Composer.compose(prompt, intent_tag, decomposition)
                      → ShapeSpec OR ComposerRefusal.
  L2 Selectors      — DomainAgentSession.fill_leaf per LeafHole;
                      concurrent fan-out via asyncio.gather, dispatched
                      by domain_hint.  Each returns BoundLeaf.
  L4 Assembler      — Assembler.assemble(shape, leaves) → AssemblyResult
                      (CLEAN or REFUSED).
  L4.5 Coverage     — CoverageGate.check(prompt, assembly, route,
      Gate          leaves) → GateVerdict (PASS / REFUSE / CLARIFY).
                      HARD-BLOCK: any non-PASS halts execution.
  L5 Executor       — substrate execute_workflow (PR-1).  Deferred to
                      the caller in V1: PR-10's pipeline produces a
                      RunLineage when the gate PASSes and the caller
                      supplied an executor; otherwise the pipeline
                      stops at the gate verdict and the caller
                      decides whether / how to execute.
  L6 Answer         — AnswerRenderer.render(intent_chain, executed_
                      summary, lineage_head_hash) → markdown.

Fail-safe at every boundary
===========================

R8 / R9 / R11 discipline carried through: every boundary in the
pipeline either returns a structured outcome OR converts an
exception into a structured outcome.  No exception escapes
``run()``.  The ``PipelineOutcome`` carries:

  - status: a closed Literal — PASS / GATE_REFUSE / GATE_CLARIFY /
    COMPOSER_REFUSE / ASSEMBLY_REFUSE / ROUTER_CLARIFY / PIPELINE_ERROR.
  - intent_chain: always present (the partial intent chain captured
    up to the failure point — None only on a router-side hard
    failure).
  - run_lineage: present when execution happened; None otherwise.
  - markdown: the user-facing rendered answer or refusal /
    clarification message.

The pipeline IS the binding contract that satisfies §PR-10
acceptance criterion 4 ("Pre-router cleanly separates lanes;
existing template lane unaffected"): the open-DAG lane is wholly
self-contained; the existing template lane in
``orchestrator.session`` does not import this module.

V1 scope clarification
======================

This module ships the WIRING + the contract.  The actual L5
executor invocation is gated behind an optional ``executor_callback``
parameter on ``run()``.  In PR-10's eval tests the callback is None
(eval gating is shape + intent correctness per §PR-10 acceptance
#1); in production wiring (caller's choice) the callback is a
function that takes the assembled Workflow + the L1 bindings and
returns a ``(Lineage, executed_summary: str)`` tuple.

Why this split: the eval matrix per §PR-10 gates on SHAPE + INTENT
correctness across 15 entries; live execution is orthogonal to that
gating and lives behind the substrate's ``execute_workflow`` /
``shared.workflow.executor`` surface.  Decoupling them via a
callback keeps the pipeline's contract narrow + testable without
needing a live DB connection.

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/`` (agent layer).  It
imports from ``orchestrator.*`` (supervisor, contracts), from
``orchestrator.open_dag.*`` (composer, coverage_gate, assembler,
intent_chain, answer, run_record), and from ``shared.workflow.*``
(Workflow, validation_result).  It does NOT import from
``rates_agent/`` — the domain Selectors are injected via a typed
``Mapping[Domain, DomainAgentSession]`` so the pipeline never sees
domain-specific code paths.
"""

from __future__ import annotations

import asyncio
import logging
from typing import (
    Any,
    Awaitable,
    Callable,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.contracts import Domain, RouteAction, RouteDecision
from orchestrator.open_dag.answer import AnswerRenderer
from orchestrator.open_dag.assembler import (
    Assembler,
    AssemblyResult,
    AssemblyStatus,
)
from orchestrator.open_dag.composer import (
    Composer,
    ComposerRefusal,
)
from orchestrator.open_dag.contracts import BoundLeaf, ShapeSpec
from orchestrator.open_dag.coverage_gate import (
    CoverageGate,
    GateVerdict,
)
from orchestrator.open_dag.intent_chain import IntentChain
from orchestrator.open_dag.run_record import RunLineage
from shared.artifacts.lineage import Lineage
from shared.workflow.registry import PrimitiveResolver
from shared.workflow.types import Workflow


logger = logging.getLogger(__name__)


# ============================================================================
# OUTCOME — the typed pipeline result
# ============================================================================


PipelineStatus = Literal[
    "PASS",                # gate PASS + executor ran + L6 rendered (FULL pipeline)
    "PASS_DRYRUN",         # gate PASS + NO executor wired (intent echo only)
    "GATE_REFUSE",         # gate returned REFUSE
    "GATE_CLARIFY",        # gate returned CLARIFY (with question)
    "COMPOSER_REFUSE",     # composer declined
    "ASSEMBLY_REFUSE",     # assembler couldn't substitute / repair
    "ROUTER_CLARIFY",      # L1 router routed CLARIFY (no decomposition)
    "PIPELINE_ERROR",      # an exception escaped a sub-call (rare)
]


class PipelineOutcome(BaseModel):
    """The typed outcome of one OpenDagPipeline.run() call.

    Frozen.  Always carries a markdown (the user-facing string).
    The intent_chain is present except on router-side hard failures
    (where the chain can't be built because the router didn't
    return a RouteDecision).  The run_lineage is present only when
    execution happened.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    status: PipelineStatus = Field(
        ...,
        description=(
            "Closed-family pipeline status.  PASS = gate green, "
            "answer rendered.  *_REFUSE / *_CLARIFY = halted at the "
            "named boundary with a structured user-facing message.  "
            "PIPELINE_ERROR = exception escaped (rare; always "
            "accompanies a fail-safe markdown explaining what failed)."
        ),
    )
    markdown: str = Field(
        ...,
        min_length=1,
        description=(
            "The user-facing markdown.  Always populated — either "
            "the L6 rendered answer (on PASS), the gate's refusal / "
            "clarification message, the composer's refusal echo, the "
            "assembler's refusal echo, the router's clarification "
            "question, or a structured pipeline-error explanation."
        ),
    )
    intent_chain: Optional[IntentChain] = Field(
        default=None,
        description=(
            "The IntentChain captured up to the failure point.  None "
            "only on router-side hard failures (the chain can't be "
            "built without a RouteDecision)."
        ),
    )
    run_lineage: Optional[RunLineage] = Field(
        default=None,
        description=(
            "The RunLineage joining intent_chain + compute_lineage.  "
            "Populated when execution happened (gate PASS + executor "
            "callback supplied + execution succeeded).  None "
            "otherwise."
        ),
    )
    route_decision: Optional[RouteDecision] = Field(
        default=None,
        description=(
            "The L1 router's RouteDecision.  Captured for caller "
            "introspection (lineage indexing, observability).  None "
            "only when the router itself failed."
        ),
    )

    @property
    def is_pass(self) -> bool:
        """True iff status is the strict PASS — pipeline fully
        executed + L6 answer rendered.  PR-10B Codex F16:
        intentionally NOT True for PASS_DRYRUN — that's a gate-pass
        without execution, and the PoC's PASS bar requires
        execution.  Use ``is_gate_pass`` when you want the
        gate-passed-or-better predicate (PASS OR PASS_DRYRUN)."""
        return self.status == "PASS"

    @property
    def is_gate_pass(self) -> bool:
        """True iff the gate verdict was PASS, regardless of whether
        execution actually ran.  Use this when the caller cares about
        gate-clearance + intent capture (e.g. lineage indexing,
        observability), not strict end-to-end execution."""
        return self.status in ("PASS", "PASS_DRYRUN")


# ============================================================================
# TYPE ALIASES
# ============================================================================


# Executor callback contract: takes (workflow, bound_leaves) and
# returns (Lineage, executed_summary).  Async so the substrate's
# execute_workflow can do I/O.  Returns None on executor failure;
# the pipeline converts that to a structured "execution failed"
# markdown (still PASS at the gate level — the result is just
# missing).
ExecutorCallback = Callable[
    [Workflow, Sequence[BoundLeaf]],
    Awaitable[Optional[Tuple[Lineage, str]]],
]


# Per-domain selector callable contract: takes (leaf_id, leaf_request,
# timeout_s) and returns a BoundLeaf.  This wraps
# ``DomainAgentSession.fill_leaf`` so the pipeline doesn't import the
# session type directly (keeps the agent-layer dependency narrow and
# lets tests inject mocks).
SelectorCallback = Callable[..., Awaitable[BoundLeaf]]


# ============================================================================
# PIPELINE
# ============================================================================


class OpenDagPipeline:
    """The PR-10 end-to-end open-DAG pipeline lane.

    Constructed with:
      - A ``router`` (Supervisor instance — exposes ``.route(prompt)``).
      - A ``composer`` (Composer instance — exposes ``.compose()`` /
        ``.repair()``).
      - A ``coverage_gate`` (CoverageGate instance — exposes ``.check()``).
      - An ``answer_renderer`` (AnswerRenderer instance — exposes
        ``.render()``).
      - A ``selectors`` mapping ``Domain -> SelectorCallback`` for
        per-domain fill_leaf dispatch.
      - A ``primitive_resolver`` for the Assembler.
      - An optional ``executor_callback`` for L5 execution.
      - Optional ``shape_patch_provider`` / ``leaf_rebinder``
        (default: synthesise from the Composer's repair_sync wrapper
        and the SelectorCallback respectively).

    Lifecycle: open() / close() on each sub-component cascades.

    The pipeline does NOT own its sub-components; the caller passes
    fully-open instances and is responsible for closing them.  This
    keeps the test surface simple (mock the sub-components directly)
    and lets the production CopilotSession share component instances
    across pipeline calls.
    """

    def __init__(
        self,
        *,
        router: Any,                            # Supervisor (duck-typed)
        composer: Composer,
        coverage_gate: CoverageGate,
        answer_renderer: AnswerRenderer,
        selectors: Mapping[Domain, SelectorCallback],
        primitive_resolver: PrimitiveResolver,
        executor_callback: Optional[ExecutorCallback] = None,
        leaf_timeout_s: float = 10.0,
        compose_timeout_s: float = 15.0,
        gate_timeout_s: float = 10.0,
        answer_timeout_s: float = 15.0,
    ) -> None:
        self._router = router
        self._composer = composer
        self._coverage_gate = coverage_gate
        self._answer_renderer = answer_renderer
        self._selectors = dict(selectors)
        self._primitive_resolver = primitive_resolver
        self._executor_callback = executor_callback

        self._leaf_timeout_s = leaf_timeout_s
        self._compose_timeout_s = compose_timeout_s
        self._gate_timeout_s = gate_timeout_s
        self._answer_timeout_s = answer_timeout_s

        # PR-10B Codex F4: wire the L4 bounded repair loop into the
        # Assembler.  Without these callbacks the Assembler refuses
        # on any L3_WIRING / L2_BINDING hard error — defeating the
        # purpose of the repair round.
        #
        # ``composer.repair_sync`` satisfies the sync
        # ``ShapePatchProvider`` Protocol (it runs the async
        # ``Composer.repair`` to completion on a worker thread when
        # called from inside an existing async context).  Pulled via
        # ``getattr`` so test mocks that don't implement repair_sync
        # still construct cleanly — the Assembler treats a None
        # callback as "no repair possible" (the pre-PR-10B behaviour).
        #
        # The leaf rebinder is a sync closure over
        # ``self._selectors``: it dispatches by
        # ``leaf_request.domain_hint`` to the matching
        # ``SelectorCallback`` and runs it to completion via the
        # same worker-thread pattern.
        shape_patch_provider = getattr(composer, "repair_sync", None)
        self._assembler = Assembler(
            primitive_resolver=primitive_resolver,
            shape_patch_provider=shape_patch_provider,
            leaf_rebinder=self._build_sync_leaf_rebinder(),
        )

    def _build_sync_leaf_rebinder(self):
        """Build the sync ``LeafRebinder`` callback the PR-4
        Assembler's repair round expects.

        Dispatches by ``leaf_request.domain_hint`` to the matching
        per-domain ``SelectorCallback``.  Runs the async fill_leaf to
        completion on a worker thread (the Assembler is called
        synchronously from inside ``OpenDagPipeline.run``'s async
        context — direct asyncio.run would error).

        Returns a refusal ``BoundLeaf`` on every failure mode so the
        Assembler's repair loop sees a structured outcome and
        proceeds to its own refusal path.
        """
        import asyncio
        import concurrent.futures

        def _rebind(*, leaf_request, current_bound, errors):
            domain_str = leaf_request.domain_hint
            try:
                domain = Domain(domain_str)
            except ValueError:
                return BoundLeaf(
                    leaf_id=current_bound.leaf_id,
                    domain=domain_str,
                    fit_confidence=0.0,
                    refusal=(
                        f"LeafRebinder: domain_hint {domain_str!r} "
                        "is not in the closed Domain enum."
                    ),
                )
            cb = self._selectors.get(domain)
            if cb is None:
                return BoundLeaf(
                    leaf_id=current_bound.leaf_id,
                    domain=domain_str,
                    fit_confidence=0.0,
                    refusal=(
                        f"LeafRebinder: no SelectorCallback registered "
                        f"for domain {domain_str!r}."
                    ),
                )

            async def _invoke():
                return await cb(
                    leaf_id=current_bound.leaf_id,
                    request=leaf_request,
                    timeout_s=self._leaf_timeout_s,
                )

            try:
                # Always offload to a fresh-loop worker thread —
                # the rebinder runs inside the Assembler, which the
                # pipeline calls SYNCHRONOUSLY from within its
                # async run().  asyncio.run() from inside an active
                # loop raises.
                def _runner():
                    new_loop = asyncio.new_event_loop()
                    try:
                        return new_loop.run_until_complete(_invoke())
                    finally:
                        new_loop.close()

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(_runner).result()
            except Exception as exc:
                logger.exception(
                    "LeafRebinder: re-bind invocation failed for "
                    "leaf %s in domain %s",
                    current_bound.leaf_id, domain_str,
                )
                return BoundLeaf(
                    leaf_id=current_bound.leaf_id,
                    domain=domain_str,
                    fit_confidence=0.0,
                    refusal=(
                        f"LeafRebinder: re-bind raised "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )

        return _rebind

    # ------------------------------------------------------------------
    # PUBLIC ENTRY POINT
    # ------------------------------------------------------------------

    async def run(
        self,
        user_prompt: str,
    ) -> PipelineOutcome:
        """Run the full open-DAG lane for one user prompt.

        Always returns a PipelineOutcome; never raises.  Each boundary
        either advances or stops the pipeline with a structured
        outcome explaining what happened.
        """
        # ---- L1 ROUTER ----
        try:
            route_decision: RouteDecision = await self._router.route(user_prompt)
        except Exception as exc:
            logger.exception("OpenDagPipeline: router failed")
            return PipelineOutcome(
                status="PIPELINE_ERROR",
                markdown=(
                    "**Pipeline error at L1 router.**\n\n"
                    f"```\n{type(exc).__name__}: {exc}\n```"
                ),
            )

        # Router CLARIFY short-circuit: render the clarification
        # question directly without spawning the downstream lanes.
        if route_decision.action == RouteAction.CLARIFY:
            return self._router_clarify_outcome(user_prompt, route_decision)

        # ---- L3 COMPOSER ----
        # PR-10A Codex F8: Composer.compose's own fail-safe handles
        # LLM exceptions internally (returns ComposerRefusal), but a
        # construction-time exception or a contract violation in the
        # sub-component could still raise.  Wrap defensively to honour
        # the "no exception escapes run()" contract documented in this
        # module's docstring.
        try:
            compose_result = await self._composer.compose(
                prompt=user_prompt,
                intent_tag=route_decision.intent_tag,  # type: ignore[arg-type]
                decomposition=route_decision.decomposition,
                timeout_s=self._compose_timeout_s,
            )
        except Exception as exc:
            logger.exception("OpenDagPipeline: composer raised")
            return PipelineOutcome(
                status="PIPELINE_ERROR",
                markdown=(
                    "**Pipeline error at L3 composer.**\n\n"
                    f"```\n{type(exc).__name__}: {exc}\n```"
                ),
                route_decision=route_decision,
            )
        if isinstance(compose_result, ComposerRefusal):
            return self._composer_refuse_outcome(
                user_prompt=user_prompt,
                route_decision=route_decision,
                refusal=compose_result,
            )
        shape: ShapeSpec = compose_result  # type: ignore[assignment]

        # ---- L2 SELECTORS (concurrent per LeafHole) ----
        try:
            bound_leaves = await self._dispatch_selectors(shape)
        except _SelectorBoundaryError as exc:
            return self._selector_failure_outcome(
                user_prompt=user_prompt,
                route_decision=route_decision,
                shape=shape,
                reason=str(exc),
            )

        # ---- L4 ASSEMBLER ----
        assembly_result = self._assembler.assemble(shape, bound_leaves)
        if assembly_result.status != AssemblyStatus.CLEAN:
            return self._assembly_refuse_outcome(
                user_prompt=user_prompt,
                route_decision=route_decision,
                shape=shape,
                bound_leaves=bound_leaves,
                assembly_result=assembly_result,
            )

        # ---- L4.5 COVERAGE GATE (hard-block) ----
        # PR-10A Codex F8: defensive wrap.  Gate.check has its own
        # fail-safe (REFUSE on timeout/LLM-exception) but the wrap
        # protects against construction-time / contract-violation
        # exceptions.
        try:
            verdict = await self._coverage_gate.check(
                user_prompt=user_prompt,
                assembly_result=assembly_result,
                route_decision=route_decision,
                leaves=bound_leaves,
                timeout_s=self._gate_timeout_s,
            )
        except Exception as exc:
            logger.exception("OpenDagPipeline: coverage gate raised")
            return PipelineOutcome(
                status="PIPELINE_ERROR",
                markdown=(
                    "**Pipeline error at L4.5 coverage gate.**\n\n"
                    f"```\n{type(exc).__name__}: {exc}\n```"
                ),
                route_decision=route_decision,
            )
        if not verdict.is_pass:
            return self._gate_non_pass_outcome(
                user_prompt=user_prompt,
                route_decision=route_decision,
                shape=shape,
                bound_leaves=bound_leaves,
                verdict=verdict,
            )

        # ---- L5 EXECUTOR (optional via callback) ----
        # PR-10C Codex F7: thread the LLM-authored wiring rationale
        # from Composer.last_compose_rationale into the IntentChain so
        # the L6 echo + lineage record the ACTUAL LLM rationale, not
        # just the derived fallback.  Empty string when the Composer
        # LLM didn't supply one — IntentChain falls back to the V1
        # derivation in that case.
        composer_llm_rationale = getattr(
            self._composer, "last_compose_rationale", "",
        )
        intent_chain = IntentChain.from_inputs(
            user_prompt=user_prompt,
            route_decision=route_decision,
            bound_leaves=tuple(bound_leaves),
            shape_or_workflow=assembly_result.workflow,
            gate_verdict=verdict,
            composer_llm_rationale=composer_llm_rationale,
        )

        executed_summary: str = ""
        compute_lineage: Optional[Lineage] = None
        if self._executor_callback is not None:
            try:
                executed = await self._executor_callback(
                    assembly_result.workflow,  # type: ignore[arg-type]
                    bound_leaves,
                )
            except Exception as exc:
                logger.exception("OpenDagPipeline: executor callback failed")
                executed = None
            if executed is None:
                # Executor failure — still surface the intent chain +
                # gate PASS, but mark execution incomplete.
                run_lineage = RunLineage(
                    intent_chain=intent_chain,
                    compute_lineage=None,
                )
                return PipelineOutcome(
                    status="PIPELINE_ERROR",
                    markdown=(
                        "**Pipeline error at L5 executor.**\n\n"
                        "Gate PASSED but the executor callback failed "
                        "to produce a result.  Intent chain captured; "
                        "no compute lineage."
                    ),
                    intent_chain=intent_chain,
                    run_lineage=run_lineage,
                    route_decision=route_decision,
                )
            compute_lineage, executed_summary = executed

        run_lineage = RunLineage(
            intent_chain=intent_chain,
            compute_lineage=compute_lineage,
        )

        # ---- L6 ANSWER RENDERER ----
        if not run_lineage.is_executed:
            # Gate-PASS dry-run path: no L6 prose; surface the intent
            # echo + a "no execution" note.
            from orchestrator.open_dag.answer import (
                _failsafe_answer,
                render_intent_echo,
            )
            markdown = render_intent_echo(intent_chain) + (
                "\n\n(Dry-run: gate PASSED but no executor was wired; "
                "no answer prose was generated.  Provide an "
                "executor_callback to OpenDagPipeline to render the "
                "full answer.)"
            )
            # PR-10B Codex F16: distinct PASS_DRYRUN status so the
            # PoC's PASS flag means "validated + gated + executed +
            # answered" and dry-run is its own observable state.
            return PipelineOutcome(
                status="PASS_DRYRUN",
                markdown=markdown,
                intent_chain=intent_chain,
                run_lineage=run_lineage,
                route_decision=route_decision,
            )

        # PR-10A Codex F8: defensive wrap around L6 render.  The
        # renderer has its own fail-safe on LLM-side failures, but a
        # construction-time exception or an unexpected sub-component
        # contract violation could still raise.
        try:
            markdown = await self._answer_renderer.render(
                intent_chain=intent_chain,
                executed_summary=executed_summary,
                lineage_head_hash=run_lineage.head_hash or "",
                timeout_s=self._answer_timeout_s,
            )
        except Exception as exc:
            logger.exception("OpenDagPipeline: answer renderer raised")
            from orchestrator.open_dag.answer import _failsafe_answer
            markdown = _failsafe_answer(
                intent_chain=intent_chain,
                lineage_head_hash=run_lineage.head_hash or "",
                reason=(
                    f"L6 answer renderer raised: "
                    f"{type(exc).__name__}: {exc}."
                ),
            )
            return PipelineOutcome(
                status="PIPELINE_ERROR",
                markdown=markdown,
                intent_chain=intent_chain,
                run_lineage=run_lineage,
                route_decision=route_decision,
            )
        return PipelineOutcome(
            status="PASS",
            markdown=markdown,
            intent_chain=intent_chain,
            run_lineage=run_lineage,
            route_decision=route_decision,
        )

    # ------------------------------------------------------------------
    # INTERNAL — SELECTOR DISPATCH
    # ------------------------------------------------------------------

    async def _dispatch_selectors(
        self,
        shape: ShapeSpec,
    ) -> List[BoundLeaf]:
        """Fan out per-leaf fill_leaf calls concurrently.

        Each LeafHole's ``domain_hint`` selects which SelectorCallback
        handles it.  If a hint targets a domain we don't have a
        selector for, raise ``_SelectorBoundaryError`` — the pipeline
        converts that to a structured outcome.

        Order of returned leaves matches the order of leaf_holes() —
        a stable assignment Assembler later uses to substitute.
        """
        holes = shape.leaf_holes()
        if not holes:
            return []

        async def _fill_one(hole_idx: int, hole) -> BoundLeaf:
            domain_str = hole.leaf_request.domain_hint
            try:
                domain = Domain(domain_str)
            except ValueError:
                raise _SelectorBoundaryError(
                    f"Composer assigned LeafHole {hole.node_id!r} a "
                    f"domain_hint {domain_str!r} that's not in the "
                    f"Domain enum."
                )
            cb = self._selectors.get(domain)
            if cb is None:
                raise _SelectorBoundaryError(
                    f"No SelectorCallback registered for domain "
                    f"{domain_str!r}.  Pipeline received "
                    f"{sorted(d.value for d in self._selectors.keys())}."
                )
            try:
                return await cb(
                    leaf_id=hole.node_id,
                    request=hole.leaf_request,
                    timeout_s=self._leaf_timeout_s,
                )
            except Exception as exc:
                logger.exception(
                    "OpenDagPipeline: selector callback failed for "
                    "leaf %s in domain %s", hole.node_id, domain_str,
                )
                # Return a refusal BoundLeaf so the downstream
                # assembler sees a structured outcome instead of a
                # raised exception.
                return BoundLeaf(
                    leaf_id=hole.node_id,
                    domain=domain_str,
                    fit_confidence=0.0,
                    refusal=(
                        f"Selector callback failed: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )

        coros = [_fill_one(i, h) for i, h in enumerate(holes)]
        # asyncio.gather preserves order — important for the
        # downstream assembler's leaf-id-to-hole match.
        return list(await asyncio.gather(*coros))

    # ------------------------------------------------------------------
    # INTERNAL — OUTCOME BUILDERS
    # ------------------------------------------------------------------

    def _router_clarify_outcome(
        self,
        user_prompt: str,
        route_decision: RouteDecision,
    ) -> PipelineOutcome:
        question = (
            route_decision.clarification_question
            or "Could you provide more detail about what you'd like to know?"
        )
        markdown = (
            "**Routing clarification needed:**\n\n"
            f"> {question}\n"
        )
        return PipelineOutcome(
            status="ROUTER_CLARIFY",
            markdown=markdown,
            intent_chain=None,
            run_lineage=None,
            route_decision=route_decision,
        )

    def _composer_refuse_outcome(
        self,
        *,
        user_prompt: str,
        route_decision: RouteDecision,
        refusal: ComposerRefusal,
    ) -> PipelineOutcome:
        # Build an IntentChain with a composer-refusal record and
        # gate set to REFUSE (so the L6 render path emits the gate's
        # refusal message — same UX shape).
        intent_chain = IntentChain.from_inputs(
            user_prompt=user_prompt,
            route_decision=route_decision,
            bound_leaves=(),
            shape_or_workflow=None,
            gate_verdict=GateVerdict(
                status="REFUSE",
                reason=(
                    f"Composer declined to compose a shape: "
                    f"{refusal.reason}"
                ),
            ),
            composer_refusal=refusal.reason,
        )
        from orchestrator.open_dag.answer import render_refusal
        markdown = render_refusal(intent_chain)
        return PipelineOutcome(
            status="COMPOSER_REFUSE",
            markdown=markdown,
            intent_chain=intent_chain,
            run_lineage=RunLineage(
                intent_chain=intent_chain, compute_lineage=None,
            ),
            route_decision=route_decision,
        )

    def _selector_failure_outcome(
        self,
        *,
        user_prompt: str,
        route_decision: RouteDecision,
        shape: ShapeSpec,
        reason: str,
    ) -> PipelineOutcome:
        intent_chain = IntentChain.from_inputs(
            user_prompt=user_prompt,
            route_decision=route_decision,
            bound_leaves=(),
            shape_or_workflow=shape,
            gate_verdict=GateVerdict(
                status="REFUSE",
                reason=(
                    f"Selector boundary error before any leaf was "
                    f"bound: {reason}"
                ),
            ),
        )
        from orchestrator.open_dag.answer import render_refusal
        markdown = render_refusal(intent_chain)
        return PipelineOutcome(
            status="ASSEMBLY_REFUSE",
            markdown=markdown,
            intent_chain=intent_chain,
            run_lineage=RunLineage(
                intent_chain=intent_chain, compute_lineage=None,
            ),
            route_decision=route_decision,
        )

    def _assembly_refuse_outcome(
        self,
        *,
        user_prompt: str,
        route_decision: RouteDecision,
        shape: ShapeSpec,
        bound_leaves: Sequence[BoundLeaf],
        assembly_result: AssemblyResult,
    ) -> PipelineOutcome:
        reason = (
            "; ".join(assembly_result.refusal_reasons)
            if assembly_result.refusal_reasons
            else "Assembler refused without a recorded reason"
        )
        intent_chain = IntentChain.from_inputs(
            user_prompt=user_prompt,
            route_decision=route_decision,
            bound_leaves=tuple(bound_leaves),
            shape_or_workflow=shape,
            gate_verdict=GateVerdict(
                status="REFUSE",
                reason=f"Assembler refused: {reason}",
            ),
        )
        from orchestrator.open_dag.answer import render_refusal
        markdown = render_refusal(intent_chain)
        return PipelineOutcome(
            status="ASSEMBLY_REFUSE",
            markdown=markdown,
            intent_chain=intent_chain,
            run_lineage=RunLineage(
                intent_chain=intent_chain, compute_lineage=None,
            ),
            route_decision=route_decision,
        )

    def _gate_non_pass_outcome(
        self,
        *,
        user_prompt: str,
        route_decision: RouteDecision,
        shape: ShapeSpec,
        bound_leaves: Sequence[BoundLeaf],
        verdict: GateVerdict,
    ) -> PipelineOutcome:
        intent_chain = IntentChain.from_inputs(
            user_prompt=user_prompt,
            route_decision=route_decision,
            bound_leaves=tuple(bound_leaves),
            shape_or_workflow=shape,
            gate_verdict=verdict,
        )
        from orchestrator.open_dag.answer import (
            render_clarification,
            render_refusal,
        )
        if verdict.status == "CLARIFY":
            markdown = render_clarification(intent_chain)
            status: PipelineStatus = "GATE_CLARIFY"
        else:
            markdown = render_refusal(intent_chain)
            status = "GATE_REFUSE"
        return PipelineOutcome(
            status=status,
            markdown=markdown,
            intent_chain=intent_chain,
            run_lineage=RunLineage(
                intent_chain=intent_chain, compute_lineage=None,
            ),
            route_decision=route_decision,
        )


# ============================================================================
# INTERNAL — raised at the selector boundary, caught by run()
# ============================================================================


class _SelectorBoundaryError(RuntimeError):
    """Raised inside ``_dispatch_selectors`` when the LeafHole's
    domain_hint is unroutable (unknown domain OR no callback
    registered).  The pipeline converts to a structured
    ``ASSEMBLY_REFUSE`` outcome — not an exception."""


__all__ = [
    "OpenDagPipeline",
    "PipelineOutcome",
    "PipelineStatus",
    "ExecutorCallback",
    "SelectorCallback",
]

"""tests/orchestrator/open_dag/test_coverage_gate.py — PR-8.

Three layers:

  1. **Pure logic**: DagEcho construction + determinism + redaction;
     GateVerdict invariants (CLARIFY → question; PASS/REFUSE → no
     question); render_gate_user_message surfaces the prompt
     verbatim + decomposition as supplementary + warnings.
  2. **Acceptance**: §PR-8 acceptance criteria (prompt verbatim;
     hard-block surface; reasons populated; soft warnings surfaced).
  3. **Session-level (mocked LLM)**: PASS / REFUSE / CLARIFY flows
     + timeout / LLM-exception / parsing-error → REFUSE fail-safe;
     under-scoped routing → REFUSE; composite-noun → CLARIFY with
     one question; adversarial DAG → REFUSE.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional, Sequence

import pytest
from pydantic import BaseModel, ValidationError as PydanticValidationError

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag import (
    AssemblyResult,
    AssemblyStatus,
    BoundLeaf,
    CoverageGate,
    DagEcho,
    Frequency,
    GOLDEN_RELATIONSHIP_CORRELATION,
    GateVerdict,
    build_and_render_dag_echo,
    build_dag_echo,
    render_dag_echo,
    render_gate_user_message,
    warnings_to_string_list,
)
from orchestrator.open_dag.assembler import Assembler
from orchestrator.open_dag.coverage_gate import _GateLLMOutput
from shared.artifacts.registry import ArtifactTypeName
from shared.workflow.registry import PrimitiveSpec
from shared.workflow.types import (
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
)
from shared.workflow.validation_result import (
    ErrorCode,
    OwnerLayer,
    Severity,
    ValidationError,
    ValidationResult,
)


# ============================================================================
# FIXTURES
# ============================================================================


def _stub_resolver(tool_name: str) -> PrimitiveSpec:
    class _In(BaseModel):
        pass

    class _Out(BaseModel):
        time_series: dict = {}

    return PrimitiveSpec(
        tool_name=tool_name,
        callable=lambda **kw: {},
        input_class=_In,
        output_class=_Out,
        config_path=Path("/tmp/stub.yaml"),
        output_field_units={"time_series": "bps"},
        output_artifact_type="Series",
    )


def _mk_bound_leaf(
    leaf_id: str,
    *,
    domain: str = "sovereign_bonds",
    role: str = "input_series",
    meaning: str = "a series",
) -> BoundLeaf:
    return BoundLeaf(
        leaf_id=leaf_id,
        domain=domain,
        mcp_tool_name=f"sentinel_{leaf_id}_tool",
        resolver_tool_key=f"sentinel_{leaf_id}_tool",
        params={},
        output_field="time_series",
        declared_output_artifact_type=ArtifactTypeName.SERIES,
        declared_units=None,
        declared_frequency=Frequency.DAILY,
        declared_semantic_role=role,
        declared_output_meaning=meaning,
        fit_confidence=0.9,
    )


def _assemble_correlation_clean() -> tuple[AssemblyResult, list[BoundLeaf]]:
    """Build a CLEAN AssemblyResult for the canonical correlation
    shape.  Returned alongside the leaves so the gate's tests can
    reuse them."""
    leaves = [
        _mk_bound_leaf(
            "leaf_a",
            role="input_series_a",
            meaning=(
                "first input series (one leg of the pair); the Selector "
                "binds the concrete primitive"
            ),
        ),
        _mk_bound_leaf(
            "leaf_b",
            role="input_series_b",
            meaning=(
                "second input series (other leg of the pair); the Selector "
                "binds the concrete primitive"
            ),
        ),
    ]
    asm = Assembler(primitive_resolver=_stub_resolver)
    result = asm.assemble(GOLDEN_RELATIONSHIP_CORRELATION, leaves)
    assert result.status == AssemblyStatus.CLEAN, (
        f"Test fixture broken: expected CLEAN, got {result.status}"
    )
    return result, leaves


# ============================================================================
# 1. DAG ECHO — determinism + redaction + structure
# ============================================================================


class TestDagEchoStructure:
    def test_build_dag_echo_returns_typed_payload(self):
        result, leaves = _assemble_correlation_clean()
        echo = build_dag_echo(result.workflow, leaves)
        assert isinstance(echo, DagEcho)
        assert echo.workflow_id == result.workflow.workflow_id
        # Two leaves substituted; four operators; six edges.
        assert len(echo.leaves) == 2
        assert len(echo.operators) == 4
        assert len(echo.edges) == 6

    def test_terminal_resolves_to_correlation_scalar(self):
        result, leaves = _assemble_correlation_clean()
        echo = build_dag_echo(result.workflow, leaves)
        assert echo.terminal_node_id == "correlation"
        assert echo.terminal_kind == "operator"
        assert echo.terminal_label == "correlation"
        assert echo.terminal_artifact_type == "ScalarMetric"

    def test_leaves_surfaced_with_role_and_meaning(self):
        result, leaves = _assemble_correlation_clean()
        echo = build_dag_echo(result.workflow, leaves)
        leaf_ids = {l.leaf_id for l in echo.leaves}
        assert leaf_ids == {"leaf_a", "leaf_b"}
        # Each leaf carries domain + role + meaning + artifact type.
        for l in echo.leaves:
            assert l.domain == "sovereign_bonds"
            assert l.artifact_type == "Series"
            assert l.semantic_role
            assert l.output_meaning

    def test_operators_iterated_in_node_id_sort_order(self):
        result, leaves = _assemble_correlation_clean()
        echo = build_dag_echo(result.workflow, leaves)
        names = [o.node_id for o in echo.operators]
        assert names == sorted(names), (
            "Operators must be sorted by node_id for byte-stable output"
        )


class TestDagEchoDeterminism:
    def test_render_dag_echo_is_byte_stable(self):
        result, leaves = _assemble_correlation_clean()
        first = render_dag_echo(build_dag_echo(result.workflow, leaves))
        second = render_dag_echo(build_dag_echo(result.workflow, leaves))
        assert first == second

    def test_leaf_order_reshuffle_does_not_change_echo(self):
        result, leaves = _assemble_correlation_clean()
        first = render_dag_echo(build_dag_echo(result.workflow, leaves))
        reshuffled = list(reversed(leaves))
        second = render_dag_echo(build_dag_echo(result.workflow, reshuffled))
        assert first == second, (
            "Echo must NOT depend on leaf insertion order — caller may "
            "have re-substituted during repair"
        )

    def test_build_and_render_dag_echo_convenience(self):
        result, leaves = _assemble_correlation_clean()
        direct = render_dag_echo(build_dag_echo(result.workflow, leaves))
        convenience = build_and_render_dag_echo(result.workflow, leaves)
        assert direct == convenience


class TestDagEchoPrimitiveBlindness:
    """The echo MUST surface semantic_role + output_meaning + units
    instead of the primitive's tool_name / output_field.  Same R8 / P11
    discipline as the PR-7A repair-prompt redaction."""

    def test_primitive_tool_name_not_in_rendered_echo(self):
        result, leaves = _assemble_correlation_clean()
        text = render_dag_echo(build_dag_echo(result.workflow, leaves))
        # The fixture's tool names contain "sentinel_<leaf_id>_tool".
        for leaf in leaves:
            assert leaf.mcp_tool_name not in text, (
                f"Echo leaks primitive tool_name {leaf.mcp_tool_name!r}"
            )
            assert leaf.resolver_tool_key not in text, (
                f"Echo leaks resolver_tool_key {leaf.resolver_tool_key!r}"
            )

    def test_primitive_output_field_not_in_rendered_echo(self):
        # Substitute a primitive whose output_field is a sentinel string.
        nodes = [
            PrimitiveNode(
                node_id="p",
                tool_name="sentinel_tool",
                output_field="SECRET_OUTPUT_FIELD",
                params={"SECRET_PRIMITIVE_PARAM": "SECRET_VALUE"},
            ),
            OperatorNode(
                node_id="op",
                operator_name="rolling_zscore",
                params={"window": 252},
            ),
        ]
        edges = [
            WorkflowEdge(
                source_node_id="p",
                target_node_id="op",
                target_input_slot="series",
            ),
        ]
        wf = Workflow(
            workflow_id="degenerate",
            nodes=nodes,
            edges=edges,
            terminal_node_id="op",
        )
        leaf = _mk_bound_leaf("p", role="input_series", meaning="the input")
        text = render_dag_echo(build_dag_echo(wf, [leaf]))
        assert "SECRET_OUTPUT_FIELD" not in text
        assert "SECRET_PRIMITIVE_PARAM" not in text
        assert "SECRET_VALUE" not in text

    def test_primitive_terminal_does_not_leak_tool_name(self):
        # Degenerate 1-leaf lookup shape: leaf IS the terminal.
        leaf = _mk_bound_leaf(
            "the_leaf",
            role="lookup_value",
            meaning="the raw value the user asked for",
        )
        wf = Workflow(
            workflow_id="lookup",
            nodes=[
                PrimitiveNode(
                    node_id="the_leaf",
                    tool_name="VERY_SECRET_LOOKUP_TOOL",
                    output_field="time_series",
                ),
            ],
            edges=[],
            terminal_node_id="the_leaf",
        )
        echo = build_dag_echo(wf, [leaf])
        text = render_dag_echo(echo)
        assert "VERY_SECRET_LOOKUP_TOOL" not in text
        # The label uses the semantic_role + "(leaf)" sentinel instead.
        assert echo.terminal_kind == "primitive"
        assert "lookup_value" in echo.terminal_label


# ============================================================================
# 2. GATE VERDICT — invariants (acceptance #3 + R8 hard-block)
# ============================================================================


class TestGateVerdictInvariants:
    def test_pass_requires_no_question(self):
        v = GateVerdict(status="PASS", reason="ok")
        assert v.is_pass
        assert not v.is_hard_block
        assert v.clarification_question is None

    def test_refuse_requires_no_question(self):
        v = GateVerdict(status="REFUSE", reason="L1 dropped a domain")
        assert not v.is_pass
        assert v.is_hard_block

    def test_clarify_requires_question(self):
        with pytest.raises(PydanticValidationError, match="clarification_question"):
            GateVerdict(status="CLARIFY", reason="ambiguous")

    def test_clarify_with_question_ok(self):
        v = GateVerdict(
            status="CLARIFY",
            reason="composite noun 5y5y",
            clarification_question="Which 5y5y forward do you mean?",
        )
        assert v.is_hard_block
        assert v.clarification_question

    def test_pass_must_not_carry_question(self):
        with pytest.raises(PydanticValidationError):
            GateVerdict(
                status="PASS",
                reason="ok",
                clarification_question="why am I here?",
            )

    def test_refuse_must_not_carry_question(self):
        with pytest.raises(PydanticValidationError):
            GateVerdict(
                status="REFUSE",
                reason="no",
                clarification_question="hi",
            )

    def test_reason_must_be_non_empty(self):
        with pytest.raises(PydanticValidationError):
            GateVerdict(status="PASS", reason="")

    def test_status_must_be_closed_family(self):
        with pytest.raises(PydanticValidationError):
            GateVerdict(status="MAYBE", reason="x")  # type: ignore[arg-type]


# ============================================================================
# 3. WARNINGS SURFACE
# ============================================================================


class TestWarningsToStringList:
    def test_empty_warnings_yields_empty_list(self):
        assert warnings_to_string_list(()) == []

    def test_one_warning_includes_code_and_node(self):
        w = ValidationError(
            code=ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH,
            owner_layer=OwnerLayer.L2_BINDING,
            severity=Severity.WARNING,
            message="role mismatch on semantic_role",
            node_id="leaf_a",
        )
        out = warnings_to_string_list([w])
        assert len(out) == 1
        assert "E_ROLE_DISCRIMINANT_MISMATCH" in out[0]
        assert "leaf_a" in out[0]


# ============================================================================
# 4. USER MESSAGE RENDERING (acceptance #1 + #4)
# ============================================================================


class TestRenderGateUserMessage:
    def test_user_prompt_appears_verbatim(self):
        prompt = "What's the rolling beta of US 10Y to Bund over 1y?"
        msg = render_gate_user_message(
            user_prompt=prompt,
            dag_echo_text="(echo)",
            decomposition=[],
            adjustments=[],
            warnings=[],
        )
        assert prompt in msg
        assert "SOURCE OF TRUTH" in msg

    def test_dag_echo_text_included(self):
        msg = render_gate_user_message(
            user_prompt="p",
            dag_echo_text="(THE_ECHO_TEXT)",
            decomposition=[],
            adjustments=[],
            warnings=[],
        )
        assert "(THE_ECHO_TEXT)" in msg

    def test_decomposition_block_labelled_supplementary(self):
        decomp = [
            EconomicQuantity(
                name="us_2s10s",
                nl_description="UST 2s10s",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ]
        msg = render_gate_user_message(
            user_prompt="p",
            dag_echo_text="(e)",
            decomposition=decomp,
            adjustments=[],
            warnings=[],
        )
        assert "us_2s10s" in msg
        assert "SUPPLEMENTARY" in msg, (
            "Decomposition block must label itself as supplementary "
            "per R8 / Codex correction point 4"
        )

    def test_adjustments_surfaced(self):
        msg = render_gate_user_message(
            user_prompt="p",
            dag_echo_text="(e)",
            decomposition=[],
            adjustments=[
                "decomposition implies domain ois (entry sofr_ois_2s10s)"
                " but routing domains are ['sovereign_bonds'].",
            ],
            warnings=[],
        )
        assert "sofr_ois_2s10s" in msg
        assert "NORMALISER ADJUSTMENT" in msg.upper()

    def test_warnings_surfaced(self):
        w = ValidationError(
            code=ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH,
            owner_layer=OwnerLayer.L2_BINDING,
            severity=Severity.WARNING,
            message="role mismatch",
            node_id="leaf_a",
        )
        msg = render_gate_user_message(
            user_prompt="p",
            dag_echo_text="(e)",
            decomposition=[],
            adjustments=[],
            warnings=[w],
        )
        assert "BOUNDARY A SOFT WARNINGS" in msg
        assert "E_ROLE_DISCRIMINANT_MISMATCH" in msg

    def test_no_primitive_tool_names_via_warning_channel(self):
        # Per the F2 discipline carried into PR-8: ValidationError.tool_name
        # must NOT appear in the rendered prompt.
        w = ValidationError(
            code=ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH,
            owner_layer=OwnerLayer.L2_BINDING,
            severity=Severity.WARNING,
            message="role mismatch",
            node_id="leaf_a",
            tool_name="SECRET_PRIMITIVE_TOOL_NAME",
        )
        msg = render_gate_user_message(
            user_prompt="p",
            dag_echo_text="(e)",
            decomposition=[],
            adjustments=[],
            warnings=[w],
        )
        assert "SECRET_PRIMITIVE_TOOL_NAME" not in msg

    def test_message_is_deterministic(self):
        first = render_gate_user_message(
            user_prompt="p",
            dag_echo_text="(e)",
            decomposition=[],
            adjustments=[],
            warnings=[],
        )
        second = render_gate_user_message(
            user_prompt="p",
            dag_echo_text="(e)",
            decomposition=[],
            adjustments=[],
            warnings=[],
        )
        assert first == second


# ============================================================================
# 5. SYSTEM PROMPT CONTENT (acceptance #1)
# ============================================================================


class TestCoverageGateSystemPrompt:
    def test_prompt_loads(self):
        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT
        assert COVERAGE_GATE_SYSTEM_PROMPT
        assert "SOURCE OF TRUTH" in COVERAGE_GATE_SYSTEM_PROMPT

    def test_prompt_describes_three_status_values(self):
        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT
        for status in ("PASS", "REFUSE", "CLARIFY"):
            assert status in COVERAGE_GATE_SYSTEM_PROMPT

    def test_prompt_calls_out_supplementary_evidence_rule(self):
        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT
        text = COVERAGE_GATE_SYSTEM_PROMPT.lower()
        assert "supplementary" in text or "supplementary evidence" in text
        # Decomposition explicitly NOT truth.
        assert "decomposition is not truth" in text or "is not truth" in text

    def test_prompt_demands_one_precise_question(self):
        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT
        assert "ONE precise" in COVERAGE_GATE_SYSTEM_PROMPT or "one precise" in COVERAGE_GATE_SYSTEM_PROMPT.lower()

    def test_prompt_contains_worked_examples(self):
        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT
        # 4 examples covering each verdict shape + the adversarial case.
        assert COVERAGE_GATE_SYSTEM_PROMPT.count("EXAMPLE") >= 4

    def test_prompt_token_budget_under_8k(self):
        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT
        from shared.workflow.operator_catalogue import approx_tokens
        tok = approx_tokens(COVERAGE_GATE_SYSTEM_PROMPT)
        # Gate prompt is much smaller than the Composer's — sanity
        # cap at 8K so unexpected bloat shows up here.
        assert tok < 8000, (
            f"COVERAGE_GATE_SYSTEM_PROMPT is {tok} approx tokens; "
            "cap is 8K"
        )

    def test_prompt_contains_no_primitive_names(self):
        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT
        # Same P11 discipline as the Composer prompt — the gate's
        # vocabulary is finance-blind English + closed substrate.
        assert "_tool" not in COVERAGE_GATE_SYSTEM_PROMPT


# ============================================================================
# 6. SESSION-LEVEL (mocked LLM) — PASS / REFUSE / CLARIFY flows
# ============================================================================


class _MockGateModel:
    """Mimics LangChain's ``with_structured_output(include_raw=True)``
    contract."""

    def __init__(
        self,
        *,
        parsed: Optional[BaseModel] = None,
        raise_exc: Optional[Exception] = None,
        parsing_error: Optional[Exception] = None,
    ):
        self._parsed = parsed
        self._raise_exc = raise_exc
        self._parsing_error = parsing_error
        self.calls: List[Any] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        if self._raise_exc is not None:
            raise self._raise_exc
        return {
            "raw": SimpleNamespace(usage_metadata=None),
            "parsed": self._parsed,
            "parsing_error": self._parsing_error,
        }


def _make_gate_with_state(*, gate_model: Optional[_MockGateModel] = None) -> CoverageGate:
    """Construct a CoverageGate without invoking real .open() (which
    needs LangChain Anthropic).  Populates state with mocks."""
    from langchain_core.messages import SystemMessage

    gate = CoverageGate()
    gate._is_open = True
    gate._cached_system_message = SystemMessage(content="(mock system)")
    gate._gate_model = gate_model
    return gate


def _basic_route_decision(
    *,
    decomposition: Sequence[EconomicQuantity] = (),
    adjustments: Sequence[str] = (),
    domains: Sequence[Domain] = (Domain.SOVEREIGN_BONDS,),
    intent: IntentTag = IntentTag.RELATIONSHIP,
) -> RouteDecision:
    return RouteDecision(
        action=RouteAction.SINGLE_DOMAIN if len(domains) <= 1 else RouteAction.MULTI_DOMAIN,
        domains=list(domains),
        rationale="test fixture",
        intent_tag=intent,
        decomposition=list(decomposition),
        adjustments=list(adjustments),
    )


@pytest.mark.asyncio
class TestCoverageGateCheck:
    async def test_pass_path_returns_pass_verdict(self):
        parsed = _GateLLMOutput(
            status="PASS",
            reason="DAG faithfully matches the prompt",
        )
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        result, leaves = _assemble_correlation_clean()
        verdict = await gate.check(
            user_prompt="How correlated are X and Y?",
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=leaves,
        )
        assert verdict.status == "PASS"
        assert verdict.is_pass
        assert verdict.reason

    async def test_refuse_on_under_scoped_routing(self):
        # Simulate L1 dropping the OIS domain — the supervisor flagged
        # the decomposition entry as out-of-routing with an adjustment.
        decomp = [
            EconomicQuantity(
                name="us_2s10s",
                nl_description="UST 2s10s",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
            EconomicQuantity(
                name="sofr_ois_2s10s",
                nl_description="SOFR OIS 2s10s",
                domain_hint=Domain.OIS,
            ),
        ]
        adjustments = [
            "decomposition implies domain ois (entry sofr_ois_2s10s) "
            "but routing domains are ['sovereign_bonds']. Possible "
            "under-scoped routing — Boundary B should treat as "
            "supplementary evidence.",
        ]
        parsed = _GateLLMOutput(
            status="REFUSE",
            reason=(
                "Prompt mentions both sovereign_bonds and ois; DAG covers "
                "only sovereign_bonds. L1 dropped the ois domain. "
                "Refusing rather than executing a half-answer."
            ),
        )
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        result, leaves = _assemble_correlation_clean()
        verdict = await gate.check(
            user_prompt=(
                "Compare US 2s10s curve spread against SOFR OIS 2s10s "
                "spread over 5y."
            ),
            assembly_result=result,
            route_decision=_basic_route_decision(
                decomposition=decomp,
                adjustments=adjustments,
            ),
            leaves=leaves,
        )
        assert verdict.status == "REFUSE"
        assert verdict.is_hard_block
        assert "ois" in verdict.reason.lower()

    async def test_clarify_on_composite_noun(self):
        parsed = _GateLLMOutput(
            status="CLARIFY",
            reason="composite noun 5y5y has multiple market interpretations",
            clarification_question=(
                "Which 5y5y forward do you mean — USD Treasury nominal, "
                "USD breakeven, USD real, or another curve?"
            ),
        )
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        result, leaves = _assemble_correlation_clean()
        verdict = await gate.check(
            user_prompt="Where is 5y5y currently vs history?",
            assembly_result=result,
            route_decision=_basic_route_decision(intent=IntentTag.LOOKUP),
            leaves=leaves,
        )
        assert verdict.status == "CLARIFY"
        assert verdict.clarification_question
        # Acceptance: ONE precise question (no multi-part).  Test
        # heuristic — no " and " joining sub-questions.
        q = verdict.clarification_question.lower()
        assert q.count("?") == 1

    async def test_refuse_on_adversarial_dag(self):
        # The DAG terminates in a ScalarMetric (correlation) but the
        # prompt asks for rolling beta over time — the gate's job is
        # to refuse this divergence.
        parsed = _GateLLMOutput(
            status="REFUSE",
            reason=(
                "Prompt asks for rolling beta time series; DAG terminates "
                "in a full-sample ScalarMetric correlation. The DAG "
                "plausibly answers a different question."
            ),
        )
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        result, leaves = _assemble_correlation_clean()
        verdict = await gate.check(
            user_prompt="What's the rolling beta of US 10Y to Bund over 1y?",
            assembly_result=result,
            route_decision=_basic_route_decision(intent=IntentTag.REGRESSION),
            leaves=leaves,
        )
        assert verdict.status == "REFUSE"
        assert "different question" in verdict.reason.lower()

    async def test_refuse_on_llm_timeout(self):
        import asyncio

        class _SlowModel:
            calls: list[Any] = []

            async def ainvoke(self, messages):
                self.calls.append(messages)
                await asyncio.sleep(10)
                return {"raw": SimpleNamespace(usage_metadata=None), "parsed": None}

        gate = _make_gate_with_state(gate_model=_SlowModel())
        result, leaves = _assemble_correlation_clean()
        verdict = await gate.check(
            user_prompt="anything",
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=leaves,
            timeout_s=0.1,
        )
        assert verdict.status == "REFUSE"
        assert "timed out" in verdict.reason.lower()

    async def test_refuse_on_llm_exception(self):
        gate = _make_gate_with_state(
            gate_model=_MockGateModel(raise_exc=RuntimeError("HTTP 500")),
        )
        result, leaves = _assemble_correlation_clean()
        verdict = await gate.check(
            user_prompt="anything",
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=leaves,
        )
        assert verdict.status == "REFUSE"
        assert "HTTP 500" in verdict.reason

    async def test_refuse_on_parsing_error(self):
        gate = _make_gate_with_state(
            gate_model=_MockGateModel(
                parsed=None,
                parsing_error=ValueError("malformed JSON"),
            ),
        )
        result, leaves = _assemble_correlation_clean()
        verdict = await gate.check(
            user_prompt="anything",
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=leaves,
        )
        assert verdict.status == "REFUSE"
        assert "did not produce" in verdict.reason

    async def test_soft_warnings_surface_in_verdict(self):
        # Manually construct a CLEAN AssemblyResult that carries a
        # WARNING (role mismatch).  The verdict's soft_warnings must
        # include it.
        result_clean, leaves = _assemble_correlation_clean()
        # Inject a WARNING into the validation_result.
        warning = ValidationError(
            code=ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH,
            owner_layer=OwnerLayer.L2_BINDING,
            severity=Severity.WARNING,
            message="semantic_role mismatch on leaf_a",
            node_id="leaf_a",
        )
        result_with_warning = AssemblyResult(
            status=AssemblyStatus.CLEAN,
            workflow=result_clean.workflow,
            validation_result=ValidationResult(
                workflow_id=result_clean.workflow.workflow_id,
                errors=(warning,),
            ),
            repair_trace=result_clean.repair_trace,
        )
        parsed = _GateLLMOutput(status="PASS", reason="ok")
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        verdict = await gate.check(
            user_prompt="p",
            assembly_result=result_with_warning,
            route_decision=_basic_route_decision(),
            leaves=leaves,
        )
        assert verdict.status == "PASS"
        assert len(verdict.soft_warnings) == 1
        assert "E_ROLE_DISCRIMINANT_MISMATCH" in verdict.soft_warnings[0]

    async def test_check_rejects_refused_assembly(self):
        # Caller bug: the gate is given a REFUSED AssemblyResult.  The
        # gate MUST raise — it cannot evaluate a refusal.
        from orchestrator.open_dag.assembler import (
            AssemblyResult,
            AssemblyStatus,
        )

        refused = AssemblyResult(
            status=AssemblyStatus.REFUSED,
            workflow=None,
            validation_result=ValidationResult(
                workflow_id="x",
                errors=(),
            ),
            refusal_reasons=("test refusal",),
        )
        parsed = _GateLLMOutput(status="PASS", reason="ok")
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        result_clean, leaves = _assemble_correlation_clean()
        with pytest.raises(ValueError, match="CLEAN"):
            await gate.check(
                user_prompt="p",
                assembly_result=refused,
                route_decision=_basic_route_decision(),
                leaves=leaves,
            )

    async def test_check_requires_open(self):
        gate = CoverageGate()
        result, leaves = _assemble_correlation_clean()
        with pytest.raises(RuntimeError, match="not open"):
            await gate.check(
                user_prompt="p",
                assembly_result=result,
                route_decision=_basic_route_decision(),
                leaves=leaves,
            )

    async def test_clarify_without_question_falls_back_to_refuse(self):
        # The LLM emits status=CLARIFY but the post-LLM GateVerdict
        # construction trips the model_validator.  Gate catches and
        # surfaces a REFUSE.
        # NOTE: _GateLLMOutput allows CLARIFY without a question
        # (parser-level laxity); the post-LLM transform enforces the
        # invariant.
        parsed = _GateLLMOutput(
            status="CLARIFY",
            reason="ambiguous",
            clarification_question=None,
        )
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        result, leaves = _assemble_correlation_clean()
        verdict = await gate.check(
            user_prompt="p",
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=leaves,
        )
        assert verdict.status == "REFUSE", (
            "A malformed CLARIFY (missing question) MUST convert to "
            "REFUSE so the hard-block discipline never lets a "
            "broken verdict slip through"
        )
        assert "invalid verdict" in verdict.reason.lower()


# ============================================================================
# 7. LIFECYCLE
# ============================================================================


@pytest.mark.asyncio
class TestCoverageGateLifecycle:
    async def test_close_clears_state(self):
        parsed = _GateLLMOutput(status="PASS", reason="ok")
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        assert gate._is_open
        gate.close()
        assert not gate._is_open
        assert gate._gate_model is None
        assert gate._cached_system_message is None

    async def test_close_idempotent(self):
        gate = CoverageGate()
        gate.close()
        gate.close()


# ============================================================================
# 8. HARD-BLOCK INVARIANT (acceptance #2 — pipeline never executes on != PASS)
# ============================================================================


class TestHardBlockInvariant:
    """R8: the pipeline must NEVER execute when status != PASS.  The
    contract is checked via the ``is_pass`` / ``is_hard_block`` flags;
    these tests are the discipline anchor for the orchestrator
    (PR-10) to grep against."""

    def test_pass_is_the_only_pass(self):
        passes = GateVerdict(status="PASS", reason="ok")
        assert passes.is_pass
        assert not passes.is_hard_block

    def test_refuse_is_hard_block(self):
        refuse = GateVerdict(status="REFUSE", reason="x")
        assert not refuse.is_pass
        assert refuse.is_hard_block

    def test_clarify_is_hard_block(self):
        clarify = GateVerdict(
            status="CLARIFY",
            reason="x",
            clarification_question="why?",
        )
        assert not clarify.is_pass
        assert clarify.is_hard_block


# ============================================================================
# PR-8A — CODEX AUDIT CORRECTIVE TESTS
# ============================================================================


class TestPR8A_F1_CoverageMismatchFailsClosed:
    """PR-8A Codex F1: ``build_dag_echo`` must NOT silently skip a
    PrimitiveNode whose leaf is missing.  An incomplete echo would
    make the DAG look cleaner to the gate than it actually is —
    breaking the hard-block discipline.  Coverage mismatches raise
    ``DagEchoError``; the gate catches and converts to a structured
    REFUSE so the public API still never raises."""

    def test_build_dag_echo_raises_on_missing_leaf(self):
        from orchestrator.open_dag.dag_echo import DagEchoError
        result, leaves = _assemble_correlation_clean()
        # Drop one BoundLeaf — coverage mismatch.
        with pytest.raises(DagEchoError, match="Missing"):
            build_dag_echo(result.workflow, [leaves[0]])

    def test_build_dag_echo_raises_on_extra_leaf(self):
        from orchestrator.open_dag.dag_echo import DagEchoError
        result, leaves = _assemble_correlation_clean()
        extra = _mk_bound_leaf(
            "leaf_extra",
            role="extra_role",
            meaning="extra meaning",
        )
        with pytest.raises(DagEchoError, match="Extra"):
            build_dag_echo(result.workflow, list(leaves) + [extra])

    def test_build_dag_echo_raises_on_duplicate_leaf_id(self):
        from orchestrator.open_dag.dag_echo import DagEchoError
        result, leaves = _assemble_correlation_clean()
        # Duplicate leaf_a — caller bug.
        dup = _mk_bound_leaf("leaf_a", role="dup_role", meaning="dup")
        with pytest.raises(DagEchoError, match="duplicate"):
            build_dag_echo(result.workflow, list(leaves) + [dup])

    def test_build_dag_echo_raises_on_refusal_in_clean_input(self):
        from orchestrator.open_dag.dag_echo import DagEchoError
        result, leaves = _assemble_correlation_clean()
        # Construct a refusal BoundLeaf and append.
        refusal_leaf = BoundLeaf(
            leaf_id="leaf_a",
            domain="sovereign_bonds",
            fit_confidence=0.0,
            refusal="selector declined",
        )
        # Replace leaf_a with the refusal — CLEAN AssemblyResult
        # invariant says refusals never reach here.
        bad_leaves = [refusal_leaf, leaves[1]]
        with pytest.raises(DagEchoError, match="refusal"):
            build_dag_echo(result.workflow, bad_leaves)


@pytest.mark.asyncio
class TestPR8A_F1_GateRefusesOnCoverageMismatch:
    """The public API never raises — ``CoverageGate.check`` catches
    DagEchoError and converts to a REFUSE verdict.  Same fail-closed
    discipline as LLM timeouts / exceptions."""

    async def test_gate_refuses_on_missing_leaf(self):
        result, leaves = _assemble_correlation_clean()
        parsed = _GateLLMOutput(status="PASS", reason="ok")
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        verdict = await gate.check(
            user_prompt="p",
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=[leaves[0]],  # missing leaf_b
        )
        assert verdict.status == "REFUSE", (
            "Coverage mismatch must fail closed — gate must NOT "
            "consult the LLM with an incomplete echo"
        )
        assert "coverage mismatch" in verdict.reason.lower()

    async def test_gate_refuses_on_extra_leaf(self):
        result, leaves = _assemble_correlation_clean()
        extra = _mk_bound_leaf("phantom", role="x", meaning="x")
        parsed = _GateLLMOutput(status="PASS", reason="ok")
        gate = _make_gate_with_state(gate_model=_MockGateModel(parsed=parsed))
        verdict = await gate.check(
            user_prompt="p",
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=list(leaves) + [extra],
        )
        assert verdict.status == "REFUSE"

    async def test_gate_does_not_call_llm_on_coverage_mismatch(self):
        # The LLM model's `calls` list must remain empty — the fail-
        # closed branch returns BEFORE consulting the model.
        result, leaves = _assemble_correlation_clean()
        mock = _MockGateModel(parsed=_GateLLMOutput(status="PASS", reason="ok"))
        gate = _make_gate_with_state(gate_model=mock)
        await gate.check(
            user_prompt="p",
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=[leaves[0]],
        )
        assert mock.calls == [], (
            "Gate must not consult the LLM when the input pair is "
            "incoherent — incomplete echo never reaches the model"
        )


class TestPR8A_F2_WarningChannelDoesNotLeakPrimitives:
    """PR-8A Codex F2: the PR-8 redaction only stripped the named
    ``ValidationError.tool_name`` field.  But the Assembler's PR-4
    contract_check interpolates ``bound.mcp_tool_name!r`` INSIDE the
    ``message`` f-string — and the prior renderer surfaced
    ``message`` verbatim.  Same risk on the ``detail`` dict for any
    future key the substrate adds.

    Fix: allowlist-based renderer.  These tests use sentinel strings
    in both ``message`` and unsafe ``detail`` keys, then assert the
    rendered prompt + soft_warnings list never contain them."""

    @staticmethod
    def _warning_with_secrets() -> ValidationError:
        return ValidationError(
            code=ErrorCode.E_ROLE_DISCRIMINANT_MISMATCH,
            owner_layer=OwnerLayer.L2_BINDING,
            severity=Severity.WARNING,
            message=(
                "leaked SECRET_TOOL_NAME_IN_MESSAGE in this free-form "
                "string — exactly the assembler's PR-4 pattern"
            ),
            leaf_id="leaf_a",
            node_id="leaf_a",
            tool_name="SECRET_TOOL_NAME_IN_FIELD",
            detail={
                # The PR-4-safe keys (passed through):
                "field": "semantic_role",
                "requested": "spread_level",
                "declared": "level_series",
                # An unsafe sentinel key that future substrate code
                # might add — MUST be dropped by the allowlist.
                "primitive_provenance": "SECRET_TOOL_NAME_IN_DETAIL",
            },
        )

    def test_message_text_not_in_rendered_warning_block(self):
        from orchestrator.open_dag.coverage_gate import _format_warnings_block

        w = self._warning_with_secrets()
        text = _format_warnings_block([w])
        # All three sentinels (message, field, detail-unsafe-key) must
        # be absent from the rendered block.
        assert "SECRET_TOOL_NAME_IN_MESSAGE" not in text
        assert "SECRET_TOOL_NAME_IN_FIELD" not in text
        assert "SECRET_TOOL_NAME_IN_DETAIL" not in text

    def test_safe_detail_keys_still_surfaced(self):
        from orchestrator.open_dag.coverage_gate import _format_warnings_block

        w = self._warning_with_secrets()
        text = _format_warnings_block([w])
        # The structural diagnostic the gate needs IS present —
        # allowlisted keys pass through.
        assert "semantic_role" in text
        assert "spread_level" in text
        assert "level_series" in text
        # The code + node_id are present so the gate can reason about
        # which leaf has the warning.
        assert "E_ROLE_DISCRIMINANT_MISMATCH" in text
        assert "leaf_a" in text

    def test_warnings_to_string_list_does_not_leak(self):
        w = self._warning_with_secrets()
        out = warnings_to_string_list([w])
        assert len(out) == 1
        assert "SECRET_TOOL_NAME_IN_MESSAGE" not in out[0]
        assert "SECRET_TOOL_NAME_IN_FIELD" not in out[0]
        assert "SECRET_TOOL_NAME_IN_DETAIL" not in out[0]
        # Structural identity preserved (downstream lineage can act
        # on the warning).
        assert "E_ROLE_DISCRIMINANT_MISMATCH" in out[0]
        assert "leaf_a" in out[0]

    @pytest.mark.asyncio
    async def test_gate_user_message_does_not_leak_secrets(self):
        # End-to-end via CoverageGate.check — surface the secrets-
        # bearing warning through the AssemblyResult and confirm the
        # mocked LLM call received a message without leakage.
        result_clean, leaves = _assemble_correlation_clean()
        w = self._warning_with_secrets()
        result_with_warning = AssemblyResult(
            status=AssemblyStatus.CLEAN,
            workflow=result_clean.workflow,
            validation_result=ValidationResult(
                workflow_id=result_clean.workflow.workflow_id,
                errors=(w,),
            ),
        )

        captured_messages: list = []

        class _CapturingModel:
            async def ainvoke(self, messages):
                captured_messages.append(messages)
                return {
                    "raw": SimpleNamespace(usage_metadata=None),
                    "parsed": _GateLLMOutput(status="PASS", reason="ok"),
                    "parsing_error": None,
                }

        gate = _make_gate_with_state(gate_model=_CapturingModel())
        verdict = await gate.check(
            user_prompt="p",
            assembly_result=result_with_warning,
            route_decision=_basic_route_decision(),
            leaves=leaves,
        )
        # The verdict's soft_warnings list also goes through the
        # redacted view.
        for line in verdict.soft_warnings:
            assert "SECRET_TOOL_NAME_IN_MESSAGE" not in line
            assert "SECRET_TOOL_NAME_IN_FIELD" not in line
            assert "SECRET_TOOL_NAME_IN_DETAIL" not in line
        # The user message handed to the LLM also doesn't leak.
        assert len(captured_messages) == 1
        human_content = captured_messages[0][1].content
        assert "SECRET_TOOL_NAME_IN_MESSAGE" not in human_content
        assert "SECRET_TOOL_NAME_IN_FIELD" not in human_content
        assert "SECRET_TOOL_NAME_IN_DETAIL" not in human_content


class TestPR8A_F3_HardBlockEnforcementDeferredToPR10:
    """PR-8A Codex F3 (documentation clarification): GateVerdict
    docstring + module docstring now explicitly call out that the
    typed CONTRACT is exposed here (is_pass / is_hard_block) but live
    ENFORCEMENT (the executor refusing to run when is_pass is False)
    is PR-10's wiring layer.  These tests anchor the contract surface
    so PR-10's tests can grep against the same flags."""

    def test_is_pass_only_true_for_pass(self):
        assert GateVerdict(status="PASS", reason="x").is_pass is True
        assert GateVerdict(status="REFUSE", reason="x").is_pass is False
        assert GateVerdict(
            status="CLARIFY", reason="x",
            clarification_question="?",
        ).is_pass is False

    def test_is_hard_block_inverse_of_is_pass(self):
        for status, extra in (
            ("PASS", {}),
            ("REFUSE", {}),
            ("CLARIFY", {"clarification_question": "?"}),
        ):
            v = GateVerdict(status=status, reason="x", **extra)  # type: ignore[arg-type]
            assert v.is_hard_block == (not v.is_pass)

    def test_gateverdict_docstring_documents_deferral(self):
        # PR-8A explicitly documents the PR-10 deferral so a future
        # reader doesn't believe the hard-block is live yet.
        assert "PR-10" in GateVerdict.__doc__
        # And PR-9 deferral for lineage observability.
        assert "PR-9" in GateVerdict.__doc__
        assert "lineage" in GateVerdict.__doc__.lower()


# ============================================================================
# PR-10E — CODEX AUDIT GAP #6: WRONG-BINDING DETECTION (params + rationale)
# ============================================================================


_CANONICAL_PAIR_ROLES_BY_LEAF_ID = {
    "leaf_a": (
        "input_series_a",
        "first input series (one leg of the pair); the Selector binds the "
        "concrete primitive",
    ),
    "leaf_b": (
        "input_series_b",
        "second input series (other leg of the pair); the Selector binds the "
        "concrete primitive",
    ),
}


def _mk_bound_leaf_with_params(
    leaf_id: str,
    *,
    domain: str = "sovereign_bonds",
    role: Optional[str] = None,
    meaning: Optional[str] = None,
    params: Optional[dict] = None,
    rationale: str = "",
    mcp_tool_name: Optional[str] = None,
) -> BoundLeaf:
    """Wrong-binding test helper.  Like _mk_bound_leaf but lets the test
    author the params dict + rationale string the selector would have
    emitted.  Used by TestPR10E_F6_WrongBindingDetection to simulate a
    selector that bound the wrong-but-type-legal curve family while
    authoring plausible English."""
    tool = mcp_tool_name or f"calculate_curve_spread_{leaf_id}_tool"
    canonical = _CANONICAL_PAIR_ROLES_BY_LEAF_ID.get(
        leaf_id, ("input_series", "a series"),
    )
    effective_role = role if role is not None else canonical[0]
    effective_meaning = meaning if meaning is not None else canonical[1]
    return BoundLeaf(
        leaf_id=leaf_id,
        domain=domain,
        mcp_tool_name=tool,
        resolver_tool_key=tool,
        params=params if params is not None else {},
        output_field="time_series",
        declared_output_artifact_type=ArtifactTypeName.SERIES,
        declared_units=None,
        declared_frequency=Frequency.DAILY,
        declared_semantic_role=effective_role,
        declared_output_meaning=effective_meaning,
        fit_confidence=0.9,
        rationale=rationale,
    )


class TestPR10E_F6_WrongBindingDetection:
    """PR-10E Codex audit gap #6 — Boundary B must be able to catch a
    wrong-primitive bind whose declared English is plausible but whose
    params dict shows the selector bound a wrong-but-type-legal
    curve family / tenor / window.

    The fix exposes BoundLeaf.params (redacted) + BoundLeaf.rationale
    in the echo so the gate's LLM can cross-check slot-fills against
    the user's intent.  Primitive-blindness is preserved by
    _redact_params_for_echo.
    """

    def test_leaf_echo_carries_params_and_rationale(self):
        leaves = [
            _mk_bound_leaf_with_params(
                "leaf_a",
                role="input_series_a",
                meaning=(
                    "first input series (one leg of the pair); the Selector "
                    "binds the concrete primitive"
                ),
                params={
                    "curve_family": "BRL_GOV",
                    "short_tenor": "2Y",
                    "long_tenor": "10Y",
                    "lookback_days": 1825,
                },
                rationale="Bound the Brazilian sovereign curve spread at 2Y-10Y.",
            ),
            _mk_bound_leaf_with_params(
                "leaf_b",
                role="input_series_b",
                meaning=(
                    "second input series (other leg of the pair); the Selector "
                    "binds the concrete primitive"
                ),
                params={
                    "curve_family": "UK_GILT",
                    "short_tenor": "2Y",
                    "long_tenor": "10Y",
                    "lookback_days": 1825,
                },
            ),
        ]
        asm = Assembler(primitive_resolver=_stub_resolver)
        result = asm.assemble(GOLDEN_RELATIONSHIP_CORRELATION, leaves)
        assert result.status == AssemblyStatus.CLEAN

        echo = build_dag_echo(result.workflow, leaves)
        leaf_a_echo = next(l for l in echo.leaves if l.leaf_id == "leaf_a")
        assert leaf_a_echo.params == {
            "curve_family": "BRL_GOV",
            "short_tenor": "2Y",
            "long_tenor": "10Y",
            "lookback_days": 1825,
        }
        assert "Brazilian" in leaf_a_echo.rationale

    def test_rendered_echo_includes_param_slot_fills(self):
        leaves = [
            _mk_bound_leaf_with_params(
                "leaf_a",
                params={"curve_family": "BRL_GOV", "short_tenor": "2Y"},
                rationale="Brazilian sovereign 2s10s",
            ),
            _mk_bound_leaf_with_params(
                "leaf_b",
                params={"curve_family": "UK_GILT", "short_tenor": "2Y"},
            ),
        ]
        asm = Assembler(primitive_resolver=_stub_resolver)
        result = asm.assemble(GOLDEN_RELATIONSHIP_CORRELATION, leaves)
        text = render_dag_echo(build_dag_echo(result.workflow, leaves))
        assert "BRL_GOV" in text
        assert "UK_GILT" in text
        assert "curve_family" in text
        assert "Brazilian" in text

    def test_redaction_strips_actual_mcp_tool_name_when_leaked(self):
        leaves = [
            _mk_bound_leaf_with_params(
                "leaf_a",
                params={
                    "curve_family": "UST",
                    "reference_path": "sentinel_leaf_a_tool/time_series",
                },
                mcp_tool_name="sentinel_leaf_a_tool",
            ),
            _mk_bound_leaf_with_params(
                "leaf_b",
                params={"curve_family": "DE_BUND"},
                mcp_tool_name="sentinel_leaf_b_tool",
            ),
        ]
        asm = Assembler(primitive_resolver=_stub_resolver)
        result = asm.assemble(GOLDEN_RELATIONSHIP_CORRELATION, leaves)
        text = render_dag_echo(build_dag_echo(result.workflow, leaves))
        assert "sentinel_leaf_a_tool" not in text
        assert "sentinel_leaf_b_tool" not in text
        assert "'UST'" in text
        assert "'DE_BUND'" in text

    @pytest.mark.asyncio
    async def test_gate_user_message_contains_wrong_bind_params(self):
        leaves = [
            _mk_bound_leaf_with_params(
                "leaf_a",
                role="input_series_a",
                meaning=(
                    "first input series (one leg of the pair); the Selector "
                    "binds the concrete primitive"
                ),
                params={
                    "curve_family": "BRL_GOV",
                    "short_tenor": "2Y",
                    "long_tenor": "10Y",
                },
                rationale="Bound Brazilian sovereign 2s10s",
            ),
            _mk_bound_leaf_with_params(
                "leaf_b",
                role="input_series_b",
                meaning=(
                    "second input series (other leg of the pair); the Selector "
                    "binds the concrete primitive"
                ),
                params={
                    "curve_family": "UK_GILT",
                    "short_tenor": "2Y",
                    "long_tenor": "10Y",
                },
            ),
        ]
        asm = Assembler(primitive_resolver=_stub_resolver)
        result = asm.assemble(GOLDEN_RELATIONSHIP_CORRELATION, leaves)

        captured: list = []

        class _Capture:
            async def ainvoke(self, messages):
                captured.append(messages)
                return {
                    "raw": SimpleNamespace(usage_metadata=None),
                    "parsed": _GateLLMOutput(
                        status="REFUSE",
                        reason=(
                            "leaf_a's params bind curve_family='BRL_GOV' "
                            "but the user asked for US 2s10s."
                        ),
                    ),
                    "parsing_error": None,
                }

        gate = _make_gate_with_state(gate_model=_Capture())
        verdict = await gate.check(
            user_prompt=(
                "How correlated has the US 2s10s curve spread been with "
                "the UK 2s10s over the last five years?"
            ),
            assembly_result=result,
            route_decision=_basic_route_decision(),
            leaves=leaves,
        )
        assert len(captured) == 1
        human = captured[0][1].content
        assert "BRL_GOV" in human
        assert "curve_family" in human
        assert "Brazilian" in human
        assert verdict.status == "REFUSE"
        assert verdict.is_hard_block
        assert "BRL_GOV" in verdict.reason

    def test_existing_correlation_fixture_still_renders_with_empty_params(self):
        result, leaves = _assemble_correlation_clean()
        text = render_dag_echo(build_dag_echo(result.workflow, leaves))
        assert "params=(none declared)" in text
        assert "rationale=(selector did not author one)" in text

    def test_system_prompt_teaches_wrong_binding_check(self):
        from orchestrator.prompts import COVERAGE_GATE_SYSTEM_PROMPT
        text = COVERAGE_GATE_SYSTEM_PROMPT.lower()
        assert "wrong-binding" in text or "wrong binding" in text
        assert "params" in text
        assert COVERAGE_GATE_SYSTEM_PROMPT.count("EXAMPLE") >= 5
        assert "BRL_GOV" in COVERAGE_GATE_SYSTEM_PROMPT

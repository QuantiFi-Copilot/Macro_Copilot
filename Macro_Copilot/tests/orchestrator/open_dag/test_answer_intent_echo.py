"""tests/orchestrator/open_dag/test_answer_intent_echo.py — PR-9.

Three layers:

  1. **Pure logic**: IntentChain construction + invariants;
     render_intent_echo / render_provenance_footer / render_clarification
     / render_refusal determinism + content; assemble_final_answer
     R9-discipline checks (hash only in footer; intent echo first).
  2. **Acceptance** (per §PR-9):
     (1) Every answer in the new lane echoes intent before number.
     (2) Lineage record includes IntentChain (covered by
         IntentChain.from_inputs + the four sub-records).
     (3) PM-facing copy never refers to the lineage hash as proof of
         correctness.
  3. **Session-level (mocked LLM)**: AnswerRenderer.render
     PASS / CLARIFY / REFUSE flows + timeout / exception / parsing
     error → fail-safe answer (intent echo + provenance, no LLM
     prose).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

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
    AnswerRenderer,
    ComposerIntentRecord,
    GateVerdict,
    GOLDEN_RELATIONSHIP_CORRELATION,
    GOLDEN_TRANSFORM_ROLLING_ZSCORE,
    IntentChain,
    GateIntentRecord,
    RouterIntentRecord,
    SelectorIntentRecord,
    assemble_final_answer,
    render_clarification,
    render_intent_echo,
    render_provenance_footer,
    render_refusal,
)
from orchestrator.open_dag.answer import (
    _AnswerLLMOutput,
    _failsafe_answer,
    render_answer_user_message,
)
from orchestrator.open_dag.contracts import BoundLeaf, Frequency
from shared.artifacts.registry import ArtifactTypeName


# ============================================================================
# FIXTURES
# ============================================================================


def _mk_route_decision(
    *,
    decomposition: Optional[List[EconomicQuantity]] = None,
    adjustments: Optional[List[str]] = None,
    intent: IntentTag = IntentTag.RELATIONSHIP,
    domains: Optional[List[Domain]] = None,
) -> RouteDecision:
    return RouteDecision(
        action=RouteAction.SINGLE_DOMAIN if (not domains or len(domains) <= 1) else RouteAction.MULTI_DOMAIN,
        domains=domains or [Domain.SOVEREIGN_BONDS],
        rationale="test fixture",
        intent_tag=intent,
        decomposition=decomposition or [
            EconomicQuantity(
                name="us_2s10s",
                nl_description="UST 2s10s curve spread",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ],
        adjustments=adjustments or [],
    )


def _mk_bound_leaf(
    leaf_id: str,
    *,
    domain: str = "sovereign_bonds",
    role: str = "spread_level",
    meaning: str = "UST curve spread series",
    tool: str = "calculate_curve_spread_tool",
    params: Optional[dict] = None,
    confidence: float = 0.9,
) -> BoundLeaf:
    return BoundLeaf(
        leaf_id=leaf_id,
        domain=domain,
        mcp_tool_name=tool,
        resolver_tool_key=tool,
        params=params or {"tenor": "2Y"},
        output_field="time_series",
        declared_output_artifact_type=ArtifactTypeName.SERIES,
        declared_units=None,
        declared_frequency=Frequency.DAILY,
        declared_semantic_role=role,
        declared_output_meaning=meaning,
        fit_confidence=confidence,
    )


def _build_canonical_pass_chain(
    *,
    user_prompt: str = "How correlated are US 2s10s and UK 2s10s over 5y?",
) -> IntentChain:
    return IntentChain.from_inputs(
        user_prompt=user_prompt,
        route_decision=_mk_route_decision(),
        bound_leaves=[
            _mk_bound_leaf("leaf_a", params={"tenor": "2Y"}),
            _mk_bound_leaf("leaf_b", params={"tenor": "10Y"}),
        ],
        shape_or_workflow=GOLDEN_RELATIONSHIP_CORRELATION,
        gate_verdict=GateVerdict(status="PASS", reason="clean coverage"),
    )


def _build_canonical_clarify_chain() -> IntentChain:
    return IntentChain.from_inputs(
        user_prompt="Where is 5y5y vs history?",
        route_decision=_mk_route_decision(intent=IntentTag.LOOKUP),
        bound_leaves=[
            _mk_bound_leaf("leaf_a", role="forward_rate", meaning="5y5y forward"),
            _mk_bound_leaf("leaf_b", role="forward_rate", meaning="5y5y forward"),
        ],
        shape_or_workflow=GOLDEN_RELATIONSHIP_CORRELATION,
        gate_verdict=GateVerdict(
            status="CLARIFY",
            reason="composite-noun 5y5y is ambiguous across markets",
            clarification_question=(
                "Which 5y5y forward do you mean — USD Treasury "
                "nominal, USD breakeven, USD real, or another curve?"
            ),
        ),
    )


def _build_canonical_refuse_chain() -> IntentChain:
    return IntentChain.from_inputs(
        user_prompt="Compare US 2s10s vs SOFR OIS 2s10s over 5y.",
        route_decision=_mk_route_decision(
            decomposition=[
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
            ],
            adjustments=[
                "decomposition implies domain ois (entry sofr_ois_2s10s) "
                "but routing domains are ['sovereign_bonds']."
            ],
        ),
        bound_leaves=[
            _mk_bound_leaf("leaf_a", role="spread_level"),
            _mk_bound_leaf("leaf_b", role="spread_level"),
        ],
        shape_or_workflow=GOLDEN_RELATIONSHIP_CORRELATION,
        gate_verdict=GateVerdict(
            status="REFUSE",
            reason=(
                "Prompt mentions both sovereign_bonds and ois; DAG "
                "covers only sovereign_bonds.  L1 dropped the ois "
                "domain.  Refusing rather than executing a half-answer."
            ),
        ),
    )


# ============================================================================
# 1. INTENT CHAIN — construction + invariants
# ============================================================================


class TestIntentChainConstruction:
    def test_canonical_correlation_chain_is_answerable(self):
        chain = _build_canonical_pass_chain()
        assert chain.is_answerable
        assert chain.router.intent_tag == IntentTag.RELATIONSHIP
        assert len(chain.selectors) == 2
        assert chain.composer.terminal_operator_name == "correlation"
        assert chain.composer.terminal_artifact_type == "ScalarMetric"
        assert chain.gate.status == "PASS"

    def test_clarify_chain_is_not_answerable(self):
        chain = _build_canonical_clarify_chain()
        assert not chain.is_answerable
        assert chain.gate.clarification_question

    def test_refuse_chain_is_not_answerable(self):
        chain = _build_canonical_refuse_chain()
        assert not chain.is_answerable

    def test_composer_refusal_propagates_to_chain(self):
        chain = IntentChain.from_inputs(
            user_prompt="impossible",
            route_decision=_mk_route_decision(),
            bound_leaves=[_mk_bound_leaf("leaf_a")],
            shape_or_workflow=None,
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
            composer_refusal="no operator chain fits",
        )
        assert chain.composer.is_refusal
        assert not chain.is_answerable

    def test_selector_refusal_propagates(self):
        # An L2 selector refusal that somehow reaches PR-9 without
        # being caught upstream — is_answerable still flips to False
        # so the L6 routes to the gate's clarification path (defensive).
        refusal_leaf = BoundLeaf(
            leaf_id="leaf_a",
            domain="sovereign_bonds",
            fit_confidence=0.0,
            refusal="no tool fits",
        )
        chain = IntentChain.from_inputs(
            user_prompt="p",
            route_decision=_mk_route_decision(),
            bound_leaves=[refusal_leaf],
            shape_or_workflow=GOLDEN_TRANSFORM_ROLLING_ZSCORE,
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
        )
        assert not chain.is_answerable
        assert chain.selectors[0].is_refusal
        assert chain.selectors[0].bound_tool_name == ""

    def test_degenerate_1leaf_lookup_terminal_handling(self):
        chain = IntentChain.from_inputs(
            user_prompt="What's US 10Y?",
            route_decision=_mk_route_decision(intent=IntentTag.LOOKUP),
            bound_leaves=[_mk_bound_leaf("leaf_input")],
            shape_or_workflow=GOLDEN_TRANSFORM_ROLLING_ZSCORE,
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
        )
        # Transform shape has a single operator terminal — not the
        # degenerate-lookup case, but exercises the non-pair-stats
        # path through _composer_record_from.
        assert chain.composer.terminal_operator_name == "rolling_zscore"
        assert chain.composer.terminal_artifact_type == "Series"
        assert "rolling_zscore" in chain.composer.operator_names

    def test_chain_roundtrips_through_json(self):
        chain = _build_canonical_pass_chain()
        as_json = chain.model_dump_json()
        rebuilt = IntentChain.model_validate_json(as_json)
        assert rebuilt.is_answerable == chain.is_answerable
        assert rebuilt.user_prompt == chain.user_prompt
        assert rebuilt.gate.status == chain.gate.status


class TestGateIntentRecordInvariants:
    def test_clarify_requires_question(self):
        with pytest.raises(PydanticValidationError, match="clarification_question"):
            GateIntentRecord(
                status="CLARIFY",
                reason="ambiguous",
            )

    def test_pass_must_not_have_question(self):
        with pytest.raises(PydanticValidationError):
            GateIntentRecord(
                status="PASS",
                reason="ok",
                clarification_question="why?",
            )


# ============================================================================
# 2. INTENT ECHO — determinism + content (acceptance #1)
# ============================================================================


class TestRenderIntentEcho:
    def test_echo_starts_with_understood_and_built(self):
        chain = _build_canonical_pass_chain()
        echo = render_intent_echo(chain)
        assert echo.startswith("Here is what I understood and built:")

    def test_echo_has_three_bullets(self):
        chain = _build_canonical_pass_chain()
        echo = render_intent_echo(chain)
        # Three bullets — Decomposed / Pulled / Wired.
        assert "Decomposed:" in echo
        assert "Pulled:" in echo
        assert "Wired:" in echo

    def test_echo_decomposed_names_quantities(self):
        chain = _build_canonical_pass_chain()
        echo = render_intent_echo(chain)
        assert "us_2s10s" in echo
        assert "sovereign_bonds" in echo

    def test_echo_pulled_names_selectors_and_tools(self):
        chain = _build_canonical_pass_chain()
        echo = render_intent_echo(chain)
        # Selector roles and tool names appear in 'Pulled'.
        assert "spread_level" in echo
        assert "calculate_curve_spread_tool" in echo

    def test_echo_wired_describes_pipeline(self):
        chain = _build_canonical_pass_chain()
        echo = render_intent_echo(chain)
        # The pair-stats shape has align_series / select / correlation.
        assert "correlation" in echo
        assert "ScalarMetric" in echo

    def test_echo_is_deterministic(self):
        chain = _build_canonical_pass_chain()
        a = render_intent_echo(chain)
        b = render_intent_echo(chain)
        assert a == b

    def test_echo_handles_composer_refusal(self):
        chain = IntentChain.from_inputs(
            user_prompt="p",
            route_decision=_mk_route_decision(),
            bound_leaves=[_mk_bound_leaf("leaf_a")],
            shape_or_workflow=None,
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
            composer_refusal="no chain fits",
        )
        echo = render_intent_echo(chain)
        assert "Composer refused" in echo


# ============================================================================
# 3. PROVENANCE FOOTER + R9 DISCIPLINE (acceptance #3)
# ============================================================================


class TestProvenanceFooter:
    def test_footer_includes_lineage_hash(self):
        chain = _build_canonical_pass_chain()
        footer = render_provenance_footer(chain, "deadbeef00")
        assert "deadbeef00" in footer

    def test_footer_includes_reproducibility_disclaimer(self):
        # R9: the suffix MUST be adjacent to the hash so the user
        # reads the affordance unambiguously.
        chain = _build_canonical_pass_chain()
        footer = render_provenance_footer(chain, "deadbeef00")
        assert "reproducibility, not correctness" in footer

    def test_footer_lists_each_leaf(self):
        chain = _build_canonical_pass_chain()
        footer = render_provenance_footer(chain, "h")
        assert "leaf_a" in footer
        assert "leaf_b" in footer
        # Tool name + domain + params surface.
        assert "calculate_curve_spread_tool" in footer
        assert "sovereign_bonds" in footer

    def test_footer_handles_refusal_leaf(self):
        refusal_leaf = BoundLeaf(
            leaf_id="leaf_a",
            domain="sovereign_bonds",
            fit_confidence=0.0,
            refusal="no fit",
        )
        chain = IntentChain.from_inputs(
            user_prompt="p",
            route_decision=_mk_route_decision(),
            bound_leaves=[refusal_leaf],
            shape_or_workflow=GOLDEN_TRANSFORM_ROLLING_ZSCORE,
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
        )
        footer = render_provenance_footer(chain, "h")
        assert "REFUSED" in footer


class TestR9LineageHashDiscipline:
    """R9: the lineage hash is reproducibility, NOT correctness.
    It belongs in the provenance footer only — never the intent
    echo, never the answer body.  These tests pin that discipline."""

    def test_lineage_hash_not_in_intent_echo(self):
        chain = _build_canonical_pass_chain()
        echo = render_intent_echo(chain)
        # A sentinel hash value that's unlikely to appear naturally.
        sentinel_hash = "SENTINEL_HASH_VALUE_NOT_IN_ECHO_42"
        # The intent echo does NOT consume the hash, so it cannot
        # appear in the rendered string by construction — but the
        # test makes the discipline explicit.
        assert sentinel_hash not in echo

    def test_assemble_final_answer_places_hash_in_footer_only(self):
        chain = _build_canonical_pass_chain()
        sentinel_hash = "0xCAFEBABE_HASH"
        final = assemble_final_answer(
            chain=chain,
            answer_prose="Here is the result: ~62% correlation.",
            lineage_head_hash=sentinel_hash,
        )
        # Find the hash and split: it must be after the
        # "Provenance:" banner.
        prov_idx = final.find("Provenance:")
        hash_idx = final.find(sentinel_hash)
        assert prov_idx != -1
        assert hash_idx != -1
        assert hash_idx > prov_idx, (
            "R9 discipline: lineage hash must appear ONLY inside the "
            "provenance footer (after the 'Provenance:' banner) — "
            "never in the intent echo or answer body"
        )

    def test_answer_prompt_explicitly_calls_out_r9(self):
        from orchestrator.prompts import ANSWER_SYSTEM_PROMPT
        assert "reproducibility, not correctness" in ANSWER_SYSTEM_PROMPT
        # The prompt MUST forbid the LLM from presenting the hash as
        # a correctness seal.
        assert "MUST NOT present the hash as a correctness seal" in ANSWER_SYSTEM_PROMPT
        assert "MUST NOT mention the lineage hash" in ANSWER_SYSTEM_PROMPT

    def test_intent_echo_precedes_answer_body_in_final_assembly(self):
        chain = _build_canonical_pass_chain()
        final = assemble_final_answer(
            chain=chain,
            answer_prose="The number is X.",
            lineage_head_hash="h",
        )
        echo_idx = final.find("Here is what I understood and built:")
        prose_idx = final.find("The number is X.")
        assert echo_idx == 0, "Intent echo MUST come first"
        assert prose_idx > echo_idx


# ============================================================================
# 4. REFUSAL / CLARIFICATION SHORT-CIRCUITS
# ============================================================================


class TestShortCircuits:
    def test_render_clarification_surfaces_question(self):
        chain = _build_canonical_clarify_chain()
        out = render_clarification(chain)
        # The clarification question MUST appear.
        assert chain.gate.clarification_question in out
        # And the intent echo precedes it.
        assert out.startswith("Here is what I understood and built:")
        # No lineage hash in clarification output.
        assert "Lineage hash" not in out

    def test_render_refusal_surfaces_reason(self):
        chain = _build_canonical_refuse_chain()
        out = render_refusal(chain)
        assert "I can't answer" in out
        assert "L1 dropped the ois domain" in out
        # Intent echo first.
        assert out.startswith("Here is what I understood and built:")

    def test_render_clarification_rejects_non_clarify_chain(self):
        chain = _build_canonical_pass_chain()
        with pytest.raises(ValueError, match="non-CLARIFY"):
            render_clarification(chain)

    def test_render_refusal_rejects_non_refuse_chain(self):
        chain = _build_canonical_pass_chain()
        with pytest.raises(ValueError, match="non-REFUSE"):
            render_refusal(chain)


# ============================================================================
# 5. ANSWER USER MESSAGE
# ============================================================================


class TestAnswerUserMessage:
    def test_user_message_includes_prompt(self):
        chain = _build_canonical_pass_chain()
        msg = render_answer_user_message(
            intent_chain=chain,
            executed_summary="ScalarMetric: 0.62",
        )
        assert chain.user_prompt in msg
        assert "ScalarMetric: 0.62" in msg
        # Includes the intent echo so the LLM has full context.
        assert "Here is what I understood and built:" in msg

    def test_user_message_instructs_no_lineage_hash(self):
        chain = _build_canonical_pass_chain()
        msg = render_answer_user_message(
            intent_chain=chain,
            executed_summary="x",
        )
        # The per-call instructions reinforce R9.
        assert "Do NOT mention the lineage hash" in msg


# ============================================================================
# 6. ANSWER RENDERER — session-level (mocked LLM)
# ============================================================================


class _MockAnswerModel:
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


def _make_renderer_with_state(*, model: Optional[_MockAnswerModel] = None) -> AnswerRenderer:
    from langchain_core.messages import SystemMessage

    r = AnswerRenderer()
    r._is_open = True
    r._cached_system_message = SystemMessage(content="(mock)")
    r._model = model
    return r


@pytest.mark.asyncio
class TestAnswerRendererRender:
    async def test_pass_path_produces_full_answer(self):
        chain = _build_canonical_pass_chain()
        parsed = _AnswerLLMOutput(
            answer_prose=(
                "The US 2s10s and UK 2s10s have run ~62% correlated "
                "over the five-year window."
            ),
        )
        renderer = _make_renderer_with_state(model=_MockAnswerModel(parsed=parsed))
        result = await renderer.render(
            intent_chain=chain,
            executed_summary="ScalarMetric: 0.62",
            lineage_head_hash="deadbeef00",
        )
        # All three sections present.
        assert "Here is what I understood and built:" in result
        assert "62% correlated" in result
        assert "Provenance:" in result
        assert "deadbeef00" in result
        # R9 — hash adjacent to disclaimer.
        assert "reproducibility, not correctness" in result

    async def test_clarify_path_short_circuits_no_llm(self):
        chain = _build_canonical_clarify_chain()
        mock = _MockAnswerModel(parsed=_AnswerLLMOutput(answer_prose="x"))
        renderer = _make_renderer_with_state(model=mock)
        result = await renderer.render(
            intent_chain=chain,
            executed_summary="(no result — gate refused)",
            lineage_head_hash="h",
        )
        # LLM was NOT consulted.
        assert mock.calls == [], (
            "CLARIFY verdict must short-circuit before the LLM call"
        )
        # Result is the clarification message.
        assert chain.gate.clarification_question in result

    async def test_refuse_path_short_circuits_no_llm(self):
        chain = _build_canonical_refuse_chain()
        mock = _MockAnswerModel(parsed=_AnswerLLMOutput(answer_prose="x"))
        renderer = _make_renderer_with_state(model=mock)
        result = await renderer.render(
            intent_chain=chain,
            executed_summary="x",
            lineage_head_hash="h",
        )
        assert mock.calls == []
        assert "I can't answer" in result
        assert "L1 dropped the ois domain" in result

    async def test_llm_timeout_returns_failsafe(self):
        import asyncio

        class _Slow:
            calls: list = []

            async def ainvoke(self, m):
                self.calls.append(m)
                await asyncio.sleep(10)
                return {"raw": SimpleNamespace(usage_metadata=None), "parsed": None}

        renderer = _make_renderer_with_state(model=_Slow())
        chain = _build_canonical_pass_chain()
        result = await renderer.render(
            intent_chain=chain,
            executed_summary="x",
            lineage_head_hash="h",
            timeout_s=0.1,
        )
        # Failsafe surfaces intent echo + footer + the failure reason.
        assert "Here is what I understood and built:" in result
        assert "answer prose could not be generated" in result
        assert "Provenance:" in result
        assert "timed out" in result

    async def test_llm_exception_returns_failsafe(self):
        renderer = _make_renderer_with_state(
            model=_MockAnswerModel(raise_exc=RuntimeError("HTTP 500")),
        )
        chain = _build_canonical_pass_chain()
        result = await renderer.render(
            intent_chain=chain,
            executed_summary="x",
            lineage_head_hash="h",
        )
        assert "HTTP 500" in result
        # Still surfaces the intent echo.
        assert "Here is what I understood and built:" in result

    async def test_llm_parsing_error_returns_failsafe(self):
        renderer = _make_renderer_with_state(
            model=_MockAnswerModel(
                parsed=None,
                parsing_error=ValueError("malformed JSON"),
            ),
        )
        chain = _build_canonical_pass_chain()
        result = await renderer.render(
            intent_chain=chain,
            executed_summary="x",
            lineage_head_hash="h",
        )
        assert "did not produce a valid output" in result
        # Failsafe preserves the lineage hash in the footer.
        assert "Lineage hash: h" in result

    async def test_render_without_open_raises(self):
        renderer = AnswerRenderer()
        chain = _build_canonical_pass_chain()
        with pytest.raises(RuntimeError, match="not open"):
            await renderer.render(
                intent_chain=chain,
                executed_summary="x",
                lineage_head_hash="h",
            )


@pytest.mark.asyncio
class TestAnswerRendererLifecycle:
    async def test_close_clears_state(self):
        renderer = _make_renderer_with_state(
            model=_MockAnswerModel(parsed=_AnswerLLMOutput(answer_prose="x")),
        )
        assert renderer._is_open
        renderer.close()
        assert not renderer._is_open
        assert renderer._model is None

    async def test_close_idempotent(self):
        renderer = AnswerRenderer()
        renderer.close()
        renderer.close()


# ============================================================================
# 7. FAIL-SAFE HELPER (also reachable from defensive paths)
# ============================================================================


class TestFailsafeAnswer:
    def test_failsafe_surfaces_intent_echo_and_footer(self):
        chain = _build_canonical_pass_chain()
        out = _failsafe_answer(
            intent_chain=chain,
            lineage_head_hash="hh",
            reason="test failure",
        )
        # Intent echo first.
        assert out.startswith("Here is what I understood and built:")
        # Reason and footer present.
        assert "test failure" in out
        assert "Provenance:" in out
        assert "Lineage hash: hh" in out

    def test_failsafe_disclaims_correctness_in_footer(self):
        chain = _build_canonical_pass_chain()
        out = _failsafe_answer(
            intent_chain=chain,
            lineage_head_hash="hh",
            reason="r",
        )
        # The hash still carries its R9 disclaimer even in failsafe.
        assert "reproducibility, not correctness" in out


# ============================================================================
# 8. INTENT-FIRST ORDERING (acceptance #1)
# ============================================================================


class TestIntentFirstOrdering:
    """Every answer in the new lane echoes intent BEFORE the number.
    These tests pin the ordering across the three rendering surfaces
    (final assembly, clarification, refusal, failsafe)."""

    def test_final_assembly_intent_first(self):
        chain = _build_canonical_pass_chain()
        out = assemble_final_answer(
            chain=chain,
            answer_prose="The number is 0.62.",
            lineage_head_hash="h",
        )
        echo_idx = out.find("Here is what I understood and built:")
        body_idx = out.find("The number is 0.62.")
        assert 0 == echo_idx < body_idx

    def test_clarification_intent_first(self):
        chain = _build_canonical_clarify_chain()
        out = render_clarification(chain)
        echo_idx = out.find("Here is what I understood and built:")
        question_idx = out.find(chain.gate.clarification_question)
        assert 0 == echo_idx < question_idx

    def test_refusal_intent_first(self):
        chain = _build_canonical_refuse_chain()
        out = render_refusal(chain)
        echo_idx = out.find("Here is what I understood and built:")
        reason_idx = out.find(chain.gate.reason)
        assert 0 == echo_idx < reason_idx

    def test_failsafe_intent_first(self):
        chain = _build_canonical_pass_chain()
        out = _failsafe_answer(
            intent_chain=chain,
            lineage_head_hash="h",
            reason="r",
        )
        echo_idx = out.find("Here is what I understood and built:")
        # Echo must come before the "answer prose could not be
        # generated" failure note.
        failure_idx = out.find("answer prose could not be generated")
        assert 0 == echo_idx < failure_idx


# ============================================================================
# PR-9A — CODEX AUDIT CORRECTIVE TESTS
# ============================================================================


class TestPR9A_F1_RunLineageJoinsIntentChainAndComputeChain:
    """PR-9A Codex F1: the plan §PR-9 requires lineage to record the
    intent chain alongside the compute chain.  PR-9 had IntentChain
    standalone; PR-9A adds RunLineage as the typed join.  These tests
    assert the join carries both records + the consistency invariants
    + the convenience accessors."""

    @staticmethod
    def _fetch_step():
        from shared.artifacts.lineage import FetchStep
        return FetchStep.build(
            name="fetch_single_tenor",
            version="1.0.0",
            params={"curve_family": "UST", "tenor": "10Y"},
        )

    def _executed_lineage(self):
        from shared.artifacts.lineage import Lineage
        return Lineage.from_steps([self._fetch_step()])

    def test_runlineage_carries_both_records(self):
        from orchestrator.open_dag import RunLineage

        chain = _build_canonical_pass_chain()
        lineage = self._executed_lineage()
        run = RunLineage(intent_chain=chain, compute_lineage=lineage)

        # Both records present on the join.
        assert run.intent_chain is chain
        assert run.compute_lineage is lineage
        # Helper accessors.
        assert run.is_executed
        assert run.is_answerable
        assert run.head_hash == lineage.head_hash

    def test_runlineage_pass_dryrun_no_compute_lineage_allowed(self):
        # A PASS gate but no execution — dry-run / planning mode.
        from orchestrator.open_dag import RunLineage

        chain = _build_canonical_pass_chain()
        run = RunLineage(intent_chain=chain, compute_lineage=None)
        assert run.is_executed is False
        assert run.is_answerable is False  # answerable requires execution
        assert run.head_hash is None

    def test_runlineage_refuse_with_compute_lineage_rejected(self):
        from orchestrator.open_dag import RunLineage

        chain = _build_canonical_refuse_chain()
        lineage = self._executed_lineage()
        # Pydantic ValidationError wraps the underlying ValueError
        with pytest.raises(PydanticValidationError, match="REFUSE"):
            RunLineage(intent_chain=chain, compute_lineage=lineage)

    def test_runlineage_clarify_with_compute_lineage_rejected(self):
        from orchestrator.open_dag import RunLineage

        chain = _build_canonical_clarify_chain()
        lineage = self._executed_lineage()
        with pytest.raises(PydanticValidationError, match="CLARIFY"):
            RunLineage(intent_chain=chain, compute_lineage=lineage)

    def test_runlineage_refuse_no_compute_lineage_ok(self):
        from orchestrator.open_dag import RunLineage

        chain = _build_canonical_refuse_chain()
        run = RunLineage(intent_chain=chain, compute_lineage=None)
        assert run.is_executed is False
        assert run.head_hash is None
        # Convenience accessors still work.
        assert run.user_prompt == chain.user_prompt
        assert run.workflow_id == chain.composer.workflow_id

    def test_runlineage_roundtrips_through_json(self):
        from orchestrator.open_dag import RunLineage

        chain = _build_canonical_pass_chain()
        lineage = self._executed_lineage()
        run = RunLineage(intent_chain=chain, compute_lineage=lineage)

        as_json = run.model_dump_json()
        rebuilt = RunLineage.model_validate_json(as_json)
        assert rebuilt.is_executed
        assert rebuilt.head_hash == run.head_hash
        assert rebuilt.intent_chain.user_prompt == chain.user_prompt
        # The four sub-records are preserved through the round-trip.
        assert rebuilt.intent_chain.router.intent_tag == chain.router.intent_tag
        assert len(rebuilt.intent_chain.selectors) == len(chain.selectors)
        assert rebuilt.intent_chain.composer.terminal_operator_name == \
            chain.composer.terminal_operator_name
        assert rebuilt.intent_chain.gate.status == chain.gate.status

    def test_runlineage_summary_includes_both_halves(self):
        from orchestrator.open_dag import RunLineage

        chain = _build_canonical_pass_chain()
        lineage = self._executed_lineage()
        run = RunLineage(intent_chain=chain, compute_lineage=lineage)
        summary = run.summary()

        # Compute-chain side.
        assert summary["head_hash"] == lineage.head_hash
        assert summary["is_executed"] is True
        # Intent-chain side.
        assert summary["gate_status"] == "PASS"
        assert summary["intent_tag"] == "relationship"
        assert summary["selector_count"] == 2
        assert summary["composer_refused"] is False

    def test_build_run_lineage_factory(self):
        from orchestrator.open_dag import build_run_lineage

        chain = _build_canonical_pass_chain()
        lineage = self._executed_lineage()
        run = build_run_lineage(
            intent_chain=chain,
            compute_lineage=lineage,
        )
        # Same object the Pydantic ctor builds.
        assert run.is_executed
        assert run.head_hash == lineage.head_hash


class TestPR9A_F2_SelectorAndComposerRationalesPopulated:
    """PR-9A Codex F2: the plan §PR-9 explicitly lists "selector
    lingo-resolution rationales" and "composer wiring rationale" as
    fields the intent chain MUST capture.  PR-9 had only the
    structural records; PR-9A adds the rationale fields, derived
    deterministically from the existing structured data."""

    def test_bound_selector_rationale_is_populated(self):
        chain = _build_canonical_pass_chain()
        for s in chain.selectors:
            assert s.rationale, (
                f"Selector {s.leaf_id} has empty rationale; PR-9A "
                "requires every SelectorIntentRecord to carry one"
            )
            # The derivation includes the role + domain + confidence.
            assert s.declared_semantic_role in s.rationale
            assert s.domain in s.rationale

    def test_refused_selector_rationale_is_populated(self):
        refusal_leaf = BoundLeaf(
            leaf_id="leaf_a",
            domain="sovereign_bonds",
            fit_confidence=0.0,
            refusal="no tool fits the request",
        )
        chain = IntentChain.from_inputs(
            user_prompt="p",
            route_decision=_mk_route_decision(),
            bound_leaves=[refusal_leaf],
            shape_or_workflow=GOLDEN_TRANSFORM_ROLLING_ZSCORE,
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
        )
        s = chain.selectors[0]
        assert s.rationale
        # Refusal mode rationale surfaces the refusal reason.
        assert "REFUSED" in s.rationale
        assert "no tool fits the request" in s.rationale

    def test_composer_rationale_is_populated(self):
        chain = _build_canonical_pass_chain()
        # The composer rationale describes the wiring topology.
        assert chain.composer.rationale
        assert chain.composer.workflow_id in chain.composer.rationale
        assert chain.composer.terminal_operator_name in chain.composer.rationale

    def test_composer_refusal_rationale_carries_reason(self):
        chain = IntentChain.from_inputs(
            user_prompt="impossible",
            route_decision=_mk_route_decision(),
            bound_leaves=[_mk_bound_leaf("leaf_a")],
            shape_or_workflow=None,
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
            composer_refusal="no operator chain fits",
        )
        assert chain.composer.rationale
        assert "REFUSED" in chain.composer.rationale
        assert "no operator chain fits" in chain.composer.rationale

    def test_all_four_plan_required_rationales_present(self):
        """Plan §PR-9 lists 4 rationales the intent chain MUST capture:
        router decomposition, selector lingo-resolution rationales,
        composer wiring rationale, gate verdict.  Assert all 4 ARE
        on the canonical chain."""
        chain = _build_canonical_pass_chain()
        # Router: decomposition + rationale + adjustments.
        assert chain.router.decomposition
        assert chain.router.rationale
        # Selectors: each has a non-empty rationale.
        assert chain.selectors
        for s in chain.selectors:
            assert s.rationale
        # Composer: non-empty rationale on bound shapes.
        assert chain.composer.rationale
        # Gate: reason + status.
        assert chain.gate.reason
        assert chain.gate.status


class TestPR9A_F3_FailSafeScopeAccurate:
    """PR-9A Codex F3: the PR-9 commit message overclaimed "every
    failure mode... returns structured markdown."  Reality: runtime
    failures (timeout/exception/parsing-error/malformed) fail-safe;
    programmer errors (not-open) raise.  This is documented and
    asserted explicitly."""

    @pytest.mark.asyncio
    async def test_not_open_raises_runtime_error_not_failsafe(self):
        renderer = AnswerRenderer()
        chain = _build_canonical_pass_chain()
        # Programmer error: didn't call open().  MUST raise — not a
        # fail-safe markdown.
        with pytest.raises(RuntimeError, match="not open"):
            await renderer.render(
                intent_chain=chain,
                executed_summary="x",
                lineage_head_hash="h",
            )

    def test_render_docstring_documents_failure_mode_split(self):
        # The docstring explicitly distinguishes the two flavours
        # (runtime → fail-safe; programmer → raise) so a future
        # reader doesn't repeat the original overclaim.
        from orchestrator.open_dag.answer import AnswerRenderer
        doc = AnswerRenderer.render.__doc__ or ""
        assert "RUNTIME failures" in doc
        assert "PROGRAMMER errors" in doc
        # And calls out F4 (executed_summary scope) explicitly.
        assert "RENDERER CONTRACT" in doc
        assert "PR-10" in doc

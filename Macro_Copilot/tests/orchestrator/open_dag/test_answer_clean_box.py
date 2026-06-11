"""tests/orchestrator/open_dag/test_answer_clean_box.py — consolidation
target #3 lock tests (one Ask card).

The open-DAG Ask answer must read like a direct answer: the chat
stream carries the CONCISE L6 answer paragraph, not the full L6
document (intent echo + prose + provenance footer).  Three levers are
pinned here:

  1. ``AnswerRenderer.render_parts`` — the structured render output
     (``RenderedAnswer``): ``markdown`` is byte-identical to what
     ``render()`` returned pre-split (back-compat), ``answer_prose``
     carries the bare paragraph ONLY on the answer path, ``kind``
     classifies the path.
  2. ``render()`` — delegates to ``render_parts`` (one code path).
  3. ``orchestrator.session._open_dag_answer_text`` — the pure
     token-content chooser: PASS + prose → prose; everything else →
     the full markdown (refusals / clarifications / fail-safes ARE
     the user-facing message).

R9 discipline is preserved by construction: ``answer_prose`` is the
LLM-authored paragraph which the system prompt forbids from carrying
the lineage hash; the hash lives only in the assembled markdown's
footer (pinned by test_answer_intent_echo.py).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, List, Optional

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
    GOLDEN_RELATIONSHIP_CORRELATION,
    GateVerdict,
    IntentChain,
    render_clarification,
    render_refusal,
)
from orchestrator.open_dag.answer import RenderedAnswer, _AnswerLLMOutput
from orchestrator.open_dag.contracts import BoundLeaf, Frequency
from orchestrator.session import _open_dag_answer_text
from shared.artifacts.registry import ArtifactTypeName


# ============================================================================
# FIXTURES (mirror test_answer_intent_echo.py)
# ============================================================================


def _mk_route_decision() -> RouteDecision:
    return RouteDecision(
        action=RouteAction.SINGLE_DOMAIN,
        domains=[Domain.SOVEREIGN_BONDS],
        rationale="test fixture",
        intent_tag=IntentTag.RELATIONSHIP,
        decomposition=[
            EconomicQuantity(
                name="us_2s10s",
                nl_description="UST 2s10s curve spread",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ],
        adjustments=[],
    )


def _mk_bound_leaf(leaf_id: str) -> BoundLeaf:
    return BoundLeaf(
        leaf_id=leaf_id,
        domain="sovereign_bonds",
        mcp_tool_name="calculate_curve_spread_tool",
        resolver_tool_key="calculate_curve_spread_tool",
        params={"tenor": "2Y"},
        output_field="time_series",
        declared_output_artifact_type=ArtifactTypeName.SERIES,
        declared_units=None,
        declared_frequency=Frequency.DAILY,
        declared_semantic_role="spread_level",
        declared_output_meaning="UST curve spread series",
        fit_confidence=0.9,
    )


def _pass_chain() -> IntentChain:
    return IntentChain.from_inputs(
        user_prompt="How correlated are US 2s10s and UK 2s10s over 5y?",
        route_decision=_mk_route_decision(),
        bound_leaves=[_mk_bound_leaf("leaf_a"), _mk_bound_leaf("leaf_b")],
        shape_or_workflow=GOLDEN_RELATIONSHIP_CORRELATION,
        gate_verdict=GateVerdict(status="PASS", reason="clean coverage"),
    )


def _refuse_chain() -> IntentChain:
    return IntentChain.from_inputs(
        user_prompt="Compare apples to oranges.",
        route_decision=_mk_route_decision(),
        bound_leaves=[_mk_bound_leaf("leaf_a")],
        shape_or_workflow=GOLDEN_RELATIONSHIP_CORRELATION,
        gate_verdict=GateVerdict(
            status="REFUSE",
            reason="Coverage gap — refusing rather than half-answering.",
        ),
    )


def _clarify_chain() -> IntentChain:
    return IntentChain.from_inputs(
        user_prompt="Where is 5y5y vs history?",
        route_decision=_mk_route_decision(),
        bound_leaves=[_mk_bound_leaf("leaf_a")],
        shape_or_workflow=GOLDEN_RELATIONSHIP_CORRELATION,
        gate_verdict=GateVerdict(
            status="CLARIFY",
            reason="composite-noun 5y5y is ambiguous across markets",
            clarification_question="Which 5y5y forward do you mean?",
        ),
    )


class _MockAnswerModel:
    def __init__(self, *, parsed: Optional[BaseModel] = None):
        self._parsed = parsed
        self.calls: List[Any] = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return {
            "raw": SimpleNamespace(usage_metadata=None),
            "parsed": self._parsed,
            "parsing_error": None,
        }


def _renderer(*, model: Optional[_MockAnswerModel] = None) -> AnswerRenderer:
    from langchain_core.messages import SystemMessage

    r = AnswerRenderer()
    r._is_open = True
    r._cached_system_message = SystemMessage(content="(mock)")
    r._model = model
    return r


_PROSE = (
    "The US 2s10s and UK 2s10s have run ~62% correlated over the "
    "five-year window."
)


# ============================================================================
# 1. render_parts — structured output per path
# ============================================================================


@pytest.mark.asyncio
class TestRenderParts:
    async def test_answer_path_carries_bare_prose(self):
        renderer = _renderer(
            model=_MockAnswerModel(parsed=_AnswerLLMOutput(answer_prose=_PROSE)),
        )
        parts = await renderer.render_parts(
            intent_chain=_pass_chain(),
            executed_summary="ScalarMetric(correlation_coefficient=0.62 ratio)",
            lineage_head_hash="deadbeef00",
        )
        assert parts.kind == "answer"
        assert parts.answer_prose == _PROSE
        # The bare prose is CONCISE: no intent echo, no provenance,
        # no lineage hash — those belong to the assembled markdown.
        assert "Here is what I understood" not in parts.answer_prose
        assert "Provenance:" not in parts.answer_prose
        assert "deadbeef00" not in parts.answer_prose
        # The assembled markdown still carries all three sections.
        assert "Here is what I understood and built:" in parts.markdown
        assert _PROSE in parts.markdown
        assert "Provenance:" in parts.markdown

    async def test_refusal_path_has_no_prose(self):
        chain = _refuse_chain()
        mock = _MockAnswerModel(parsed=_AnswerLLMOutput(answer_prose="x"))
        renderer = _renderer(model=mock)
        parts = await renderer.render_parts(
            intent_chain=chain,
            executed_summary="(none)",
            lineage_head_hash="h",
        )
        assert parts.kind == "refusal"
        assert parts.answer_prose is None
        assert parts.markdown == render_refusal(chain)
        assert mock.calls == []

    async def test_clarification_path_has_no_prose(self):
        chain = _clarify_chain()
        mock = _MockAnswerModel(parsed=_AnswerLLMOutput(answer_prose="x"))
        renderer = _renderer(model=mock)
        parts = await renderer.render_parts(
            intent_chain=chain,
            executed_summary="(none)",
            lineage_head_hash="h",
        )
        assert parts.kind == "clarification"
        assert parts.answer_prose is None
        assert parts.markdown == render_clarification(chain)
        assert mock.calls == []


# ============================================================================
# 2. render() — single code path through render_parts
# ============================================================================


@pytest.mark.asyncio
class TestRenderDelegates:
    async def test_render_equals_render_parts_markdown(self):
        chain = _refuse_chain()
        renderer = _renderer()
        rendered = await renderer.render(
            intent_chain=chain,
            executed_summary="(none)",
            lineage_head_hash="h",
        )
        parts = await renderer.render_parts(
            intent_chain=chain,
            executed_summary="(none)",
            lineage_head_hash="h",
        )
        assert rendered == parts.markdown


# ============================================================================
# 3. _open_dag_answer_text — the token-content chooser
# ============================================================================


class TestOpenDagAnswerText:
    def test_pass_with_prose_emits_prose(self):
        outcome = SimpleNamespace(
            status="PASS",
            answer_prose=_PROSE,
            markdown="FULL L6 DOCUMENT",
        )
        assert _open_dag_answer_text(outcome) == _PROSE

    def test_pass_without_prose_falls_back_to_markdown(self):
        # L6 fail-safe on a PASS run: the fail-safe document IS the
        # user-facing message.
        outcome = SimpleNamespace(
            status="PASS",
            answer_prose=None,
            markdown="FAILSAFE DOCUMENT",
        )
        assert _open_dag_answer_text(outcome) == "FAILSAFE DOCUMENT"

    def test_refusal_emits_full_markdown(self):
        outcome = SimpleNamespace(
            status="GATE_REFUSE",
            answer_prose=None,
            markdown="REFUSAL MESSAGE",
        )
        assert _open_dag_answer_text(outcome) == "REFUSAL MESSAGE"

    def test_clarify_emits_full_markdown(self):
        outcome = SimpleNamespace(
            status="GATE_CLARIFY",
            answer_prose=None,
            markdown="CLARIFICATION QUESTION",
        )
        assert _open_dag_answer_text(outcome) == "CLARIFICATION QUESTION"


# ============================================================================
# 4. RenderedAnswer model invariants
# ============================================================================


class TestRenderedAnswerModel:
    def test_frozen_and_closed(self):
        ra = RenderedAnswer(markdown="m", kind="refusal")
        with pytest.raises(Exception):
            ra.markdown = "other"  # type: ignore[misc]

    def test_kind_closed_family(self):
        with pytest.raises(Exception):
            RenderedAnswer(markdown="m", kind="novel-kind")  # type: ignore[arg-type]

"""tests/test_workflow_router.py — plumbing tests for the
WorkflowRouter and its supporting prompt + contract modules.

These tests do NOT call a real LLM.  They cover:

  1. Catalogue rendering — the live template catalogue resolves into
     a system-prompt fragment that names every registered template
     and includes its slot_schema typed shape.
  2. Decision contract — Pydantic shape, default fields, frozen.
  3. ``_normalise_workflow_route`` repair logic — every path that
     promotes a malformed LLM decision to CLARIFY (unknown template,
     missing required slot, slot type mismatch, route-with-clarification-
     question, clarify-with-template_id, etc.).
  4. ``WorkflowRouter.execute`` end-to-end via a stubbed model that
     returns a hand-crafted ``WorkflowRouteDecision`` — proves the
     route → bind → run wiring works.

The real LLM eval is the manual ``WORKFLOW_GAUNTLET.md`` walkthrough
(Phase D) — driven from the CLI, scored by the developer.  These
tests cover the plumbing, NOT the routing-quality eval.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from orchestrator.workflow_contracts import (
    WorkflowExecutionResult,
    WorkflowRouteAction,
    WorkflowRouteDecision,
)
from orchestrator.workflow_prompts import (
    render_catalogue,
    render_router_system_prompt,
)
from orchestrator.workflow_router import (
    WorkflowRouter,
    _force_clarify,
    _normalise_workflow_route,
)
from shared.workflow import (
    clear_template_registry,
    clear_workflow_template_cache,
    known_template_ids,
)

from tests._workflow_synthetic_fetchers import (
    CANONICAL_Q1_SLOT_VALUES,
    q1_canonical_fetchers_context,
)


# ---------------------------------------------------------------------------
# Auto-fixture: ensure both V1 templates are registered for every test.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _registered_templates():
    from shared.config import clear_tool_config_cache
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()
    import rates_agent.workflows.event_study as es
    import rates_agent.workflows.regime_conditioned_relationship as rcr
    importlib.reload(es)
    importlib.reload(rcr)
    yield
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()


# ===========================================================================
# 1. Catalogue rendering
# ===========================================================================


class TestCatalogueRendering:
    def test_render_catalogue_includes_every_registered_template(self):
        catalogue = render_catalogue()
        for tid in known_template_ids():
            assert tid in catalogue, (
                f"catalogue missing template_id={tid!r}"
            )

    def test_render_catalogue_emits_explicit_marker_when_empty(self):
        """Edge case: a fresh registry with no templates renders an
        explicit empty marker so the LLM sees the registry is empty
        rather than a silently truncated prompt."""
        clear_template_registry()
        rendered = render_catalogue()
        assert "(no templates registered" in rendered
        # Re-register after the negative test so the autouse fixture's
        # teardown sees a known state.
        import importlib
        import rates_agent.workflows.event_study as es
        import rates_agent.workflows.regime_conditioned_relationship as rcr
        importlib.reload(es)
        importlib.reload(rcr)

    def test_render_catalogue_includes_slot_schema_json(self):
        """Each template's slot_schema must be visible in the
        catalogue so the LLM can bind typed slots."""
        catalogue = render_catalogue()
        # Slots from event_study:
        assert '"signal_tool_name"' in catalogue
        assert '"target_tool_name"' in catalogue
        assert '"threshold"' in catalogue
        # Slots from regime_conditioned_relationship:
        assert '"lhs_tool_name"' in catalogue
        assert '"regime_signal_tool_name"' in catalogue
        assert '"high_threshold"' in catalogue
        # Slot type tags appear too.
        assert '"type": "str"' in catalogue
        assert '"type": "float"' in catalogue or '"type": "int"' in catalogue

    def test_render_catalogue_includes_archetype_signature_cues(self):
        """The cues the LLM matches against prompts MUST appear in
        the catalogue."""
        catalogue = render_catalogue()
        assert "archetype_signature_cues" in catalogue
        # At least one event_study cue and one regime cue.
        assert "event study" in catalogue.lower()
        assert "regime" in catalogue.lower()

    def test_render_router_system_prompt_substitutes_catalogue(self):
        prompt = render_router_system_prompt()
        assert "WORKFLOW TEMPLATE CATALOGUE" in prompt
        assert "{catalogue}" not in prompt, (
            "system-prompt template's {catalogue} placeholder was "
            "not substituted"
        )
        assert "event_study" in prompt
        assert "regime_conditioned_relationship" in prompt


# ===========================================================================
# 2. Decision contract
# ===========================================================================


class TestDecisionContract:
    def test_route_decision_is_frozen(self):
        d = WorkflowRouteDecision(
            action=WorkflowRouteAction.OUT_OF_SCOPE,
            rationale="test",
        )
        with pytest.raises(Exception):
            d.template_id = "x"  # type: ignore[misc]

    def test_default_field_values(self):
        d = WorkflowRouteDecision(
            action=WorkflowRouteAction.OUT_OF_SCOPE,
            rationale="test",
        )
        assert d.template_id is None
        assert d.slot_values == {}
        assert d.clarification_question is None
        assert d.adjustments == []

    def test_action_enum_values(self):
        # Closed family: route / out_of_scope / clarify.
        assert WorkflowRouteAction.ROUTE.value == "route"
        assert WorkflowRouteAction.OUT_OF_SCOPE.value == "out_of_scope"
        assert WorkflowRouteAction.CLARIFY.value == "clarify"


# ===========================================================================
# 3. Normaliser repair logic
# ===========================================================================


class TestNormaliser:
    def test_clean_route_passes_through(self):
        """A correct ROUTE decision (known template, valid slots) is
        returned unchanged + ``adjustments == []``."""
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.ROUTE,
            template_id="event_study",
            slot_values=dict(CANONICAL_Q1_SLOT_VALUES),
            rationale="canonical Q1 binding",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.ROUTE
        assert out.template_id == "event_study"
        assert out.slot_values == CANONICAL_Q1_SLOT_VALUES
        assert out.adjustments == []

    def test_unknown_template_id_demoted_to_clarify(self):
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.ROUTE,
            template_id="not_a_template",
            slot_values={},
            rationale="bogus",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.CLARIFY
        assert out.template_id is None
        assert out.slot_values == {}
        assert any("unknown template_id" in a for a in out.adjustments)
        assert "not_a_template" in out.clarification_question

    def test_missing_template_id_on_route_action_demoted(self):
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.ROUTE,
            template_id=None,
            slot_values={},
            rationale="missing id",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.CLARIFY
        assert any("missing template_id" in a for a in out.adjustments)

    def test_missing_required_slot_on_route_action_demoted(self):
        partial = dict(CANONICAL_Q1_SLOT_VALUES)
        partial.pop("threshold")
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.ROUTE,
            template_id="event_study",
            slot_values=partial,
            rationale="missing threshold",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.CLARIFY
        assert any("bind failed" in a for a in out.adjustments)
        assert "threshold" in out.clarification_question

    def test_slot_type_mismatch_demoted(self):
        bad = dict(CANONICAL_Q1_SLOT_VALUES)
        bad["threshold"] = "not_a_float"
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.ROUTE,
            template_id="event_study",
            slot_values=bad,
            rationale="type mismatch",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.CLARIFY
        assert any("bind failed" in a for a in out.adjustments)

    def test_slot_constraint_violation_demoted(self):
        """Codex P2 follow-up to PR #86: slot_constraints are
        enforced at bind() time.  An inverted threshold binding for
        regime_conditioned_relationship MUST surface as a CLARIFY
        with the constraint rationale in the adjustments / question."""
        from tests._workflow_synthetic_fetchers import CANONICAL_Q2_SLOT_VALUES
        bad = dict(CANONICAL_Q2_SLOT_VALUES)
        bad["high_threshold"] = -5.0
        bad["low_threshold"] = +5.0
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.ROUTE,
            template_id="regime_conditioned_relationship",
            slot_values=bad,
            rationale="inverted thresholds",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.CLARIFY
        assert any("bind failed" in a for a in out.adjustments)

    def test_route_with_clarification_question_strips_it(self):
        """LLM accidentally populates clarification_question on a
        ROUTE action — normaliser strips it (a route doesn't need
        a question)."""
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.ROUTE,
            template_id="event_study",
            slot_values=dict(CANONICAL_Q1_SLOT_VALUES),
            rationale="extra field",
            clarification_question="this should not be here",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.ROUTE
        assert out.clarification_question is None
        assert any(
            "carried clarification_question" in a for a in out.adjustments
        )

    def test_clarify_with_template_id_strips_it(self):
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.CLARIFY,
            template_id="event_study",
            slot_values={},
            rationale="extra field",
            clarification_question="UST or Bund?",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.CLARIFY
        assert out.template_id is None
        assert any("clarify decision carried template_id" in a for a in out.adjustments)

    def test_clarify_with_slot_values_strips_them(self):
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.CLARIFY,
            template_id=None,
            slot_values={"foo": "bar"},
            rationale="extra slots",
            clarification_question="?",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.CLARIFY
        assert out.slot_values == {}
        assert any("clarify decision carried slot_values" in a for a in out.adjustments)

    def test_clarify_without_question_synthesizes_fallback(self):
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.CLARIFY,
            rationale="forgot question",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.CLARIFY
        assert out.clarification_question is not None
        assert out.clarification_question.strip()
        assert any(
            "missing clarification_question" in a for a in out.adjustments
        )

    def test_out_of_scope_with_template_id_strips_it(self):
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.OUT_OF_SCOPE,
            template_id="event_study",
            slot_values={"foo": "bar"},
            rationale="leaking fields",
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.OUT_OF_SCOPE
        assert out.template_id is None
        assert out.slot_values == {}
        assert len(out.adjustments) >= 2

    def test_llm_supplied_adjustments_are_discarded(self):
        """The LLM has no business writing into ``adjustments`` — the
        normaliser MUST clear that field even on otherwise-clean
        inputs so only real notes from this function survive."""
        decision = WorkflowRouteDecision(
            action=WorkflowRouteAction.ROUTE,
            template_id="event_study",
            slot_values=dict(CANONICAL_Q1_SLOT_VALUES),
            rationale="clean",
            adjustments=["LLM-injected note that should disappear"],
        )
        out = _normalise_workflow_route(decision)
        assert out.action == WorkflowRouteAction.ROUTE
        assert out.adjustments == []  # LLM's note discarded


class TestForceClarifyHelper:
    def test_force_clarify_shape(self):
        out = _force_clarify(
            rationale="r",
            adjustments=["x"],
            clarification_question="q?",
        )
        assert out.action == WorkflowRouteAction.CLARIFY
        assert out.template_id is None
        assert out.slot_values == {}
        assert out.rationale == "r"
        assert out.adjustments == ["x"]
        assert out.clarification_question == "q?"


# ===========================================================================
# 4. WorkflowRouter.execute end-to-end (stubbed LLM)
# ===========================================================================


class _StubModelResponse:
    """Stand-in for ``langchain_anthropic`` ainvoke() return value
    when ``include_raw=True`` is set.  The return is a dict with
    ``raw``, ``parsed``, and ``parsing_error`` keys."""

    def __init__(self, parsed: WorkflowRouteDecision):
        self._parsed = parsed

    def to_dict(self) -> dict:
        # No raw AIMessage needed — _log_usage tolerates None.
        return {
            "raw": None,
            "parsed": self._parsed,
            "parsing_error": None,
        }


class _StubRouteModel:
    """Replaces ``ChatAnthropic.with_structured_output(...)`` so
    ``WorkflowRouter.route`` runs without the LLM dependency."""

    def __init__(self, decision: WorkflowRouteDecision):
        self._decision = decision

    async def ainvoke(self, messages):  # noqa: D401 — match langchain signature
        # Pretend latency / async path.
        return _StubModelResponse(self._decision).to_dict()


def _make_router_with_stub(
    decision: WorkflowRouteDecision,
    *,
    system_prompt: str = "stub-system-prompt",
) -> WorkflowRouter:
    """Construct a WorkflowRouter, then swap its internal route
    model for a stub that always returns the supplied decision.
    Avoids any real langchain-anthropic call in the unit suite.
    """
    # We bypass __init__'s langchain imports by constructing without
    # invoking it — use ``object.__new__`` and populate the few
    # fields ``route`` reads.  This is deliberately tighter than a
    # MagicMock: we depend on a small known surface (the cached
    # system message + the route model), so we test against THAT
    # surface only.
    router = object.__new__(WorkflowRouter)
    router._model_name = "stub"
    router._temperature = 0.0
    router._max_tokens = 0

    # Build a minimal langchain SystemMessage replacement that
    # ``route`` only inserts into a list — its content shape
    # doesn't affect the stub model's behaviour.
    sysmsg = MagicMock()
    sysmsg.content = system_prompt
    router._cached_system_message = sysmsg

    router._base_model = MagicMock()
    router._route_model = _StubRouteModel(decision)
    return router


class TestRouterRouteStubbed:
    def test_route_returns_normalised_decision(self):
        router = _make_router_with_stub(
            WorkflowRouteDecision(
                action=WorkflowRouteAction.ROUTE,
                template_id="event_study",
                slot_values=dict(CANONICAL_Q1_SLOT_VALUES),
                rationale="canonical Q1",
            )
        )
        # ``route`` lazy-imports HumanMessage from langchain inside
        # its body; patch that import so the stub path works in
        # environments without langchain installed.
        fake_human = MagicMock()
        with patch.dict(
            "sys.modules",
            {
                "langchain_core": MagicMock(),
                "langchain_core.messages": MagicMock(
                    HumanMessage=lambda content: fake_human,
                ),
            },
        ):
            decision = asyncio.run(router.route("test prompt"))
        assert decision.action == WorkflowRouteAction.ROUTE
        assert decision.template_id == "event_study"
        assert decision.adjustments == []

    def test_route_normalises_unknown_template_id(self):
        router = _make_router_with_stub(
            WorkflowRouteDecision(
                action=WorkflowRouteAction.ROUTE,
                template_id="ghost_template",
                slot_values={},
                rationale="bogus id",
            )
        )
        with patch.dict(
            "sys.modules",
            {
                "langchain_core": MagicMock(),
                "langchain_core.messages": MagicMock(
                    HumanMessage=lambda content: MagicMock(),
                ),
            },
        ):
            decision = asyncio.run(router.route("test prompt"))
        assert decision.action == WorkflowRouteAction.CLARIFY
        assert any("unknown template_id" in a for a in decision.adjustments)


class TestRouterExecuteStubbed:
    """End-to-end: stub LLM returns a valid ROUTE decision; the
    router proceeds to bind + execute against the synthetic Q1
    fetchers; the WorkflowExecutionResult carries both the route
    decision AND a successful execution envelope."""

    def test_execute_routes_then_runs(self):
        router = _make_router_with_stub(
            WorkflowRouteDecision(
                action=WorkflowRouteAction.ROUTE,
                template_id="event_study",
                slot_values=dict(CANONICAL_Q1_SLOT_VALUES),
                rationale="canonical Q1",
            )
        )
        with patch.dict(
            "sys.modules",
            {
                "langchain_core": MagicMock(),
                "langchain_core.messages": MagicMock(
                    HumanMessage=lambda content: MagicMock(),
                ),
            },
        ), q1_canonical_fetchers_context():
            result: WorkflowExecutionResult = asyncio.run(
                router.execute("test prompt"),
            )
        assert result.route.action == WorkflowRouteAction.ROUTE
        assert result.execution is not None
        assert result.execution["ok"] is True
        assert result.execution["template_id"] == "event_study"
        assert result.execution["terminal_artifact"]["type"] == "Series"

    def test_execute_clarify_path_skips_run(self):
        """A CLARIFY decision means there's nothing to execute — the
        ``execution`` field MUST be ``None``."""
        router = _make_router_with_stub(
            WorkflowRouteDecision(
                action=WorkflowRouteAction.CLARIFY,
                rationale="ambiguous",
                clarification_question="UST or Bund?",
            )
        )
        with patch.dict(
            "sys.modules",
            {
                "langchain_core": MagicMock(),
                "langchain_core.messages": MagicMock(
                    HumanMessage=lambda content: MagicMock(),
                ),
            },
        ):
            result = asyncio.run(router.execute("ambiguous prompt"))
        assert result.route.action == WorkflowRouteAction.CLARIFY
        assert result.execution is None

    def test_execute_out_of_scope_path_skips_run(self):
        router = _make_router_with_stub(
            WorkflowRouteDecision(
                action=WorkflowRouteAction.OUT_OF_SCOPE,
                rationale="not a workflow question",
            )
        )
        with patch.dict(
            "sys.modules",
            {
                "langchain_core": MagicMock(),
                "langchain_core.messages": MagicMock(
                    HumanMessage=lambda content: MagicMock(),
                ),
            },
        ):
            result = asyncio.run(router.execute("hello"))
        assert result.route.action == WorkflowRouteAction.OUT_OF_SCOPE
        assert result.execution is None

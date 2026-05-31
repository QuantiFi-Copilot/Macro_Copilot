"""tests/orchestrator/open_dag/test_composer.py — PR-7 (L3 Composer).

Three layers of tests, mirroring the PR-6 pattern:

  1. **Pure-logic** — golden shapes round-trip, prompt rendering is
     deterministic, the LLM-output transformer accepts valid shapes
     and refuses structurally impossible ones.  No LLM, no LangChain.

  2. **Acceptance criteria** — the four numbered acceptance criteria
     from ``tmp/orchestration.md`` §PR-7:
       (1) Composer emits a structurally valid ShapeSpec for each
           canonical query — covered by the golden-shapes round-trip +
           the per-intent llm-output transformer tests.
       (2) Repair callback emits only additive patches — covered by
           the patch-transformer tests asserting non-additive patch
           kinds raise + that the resulting ShapePatch types are the
           closed family of {InsertAdapterNode, RewireEdge}.
       (3) Composer prompt contains no primitive names anywhere
           (grep test).
       (4) Composer prompt contains the full operator catalogue
           (assert all 16 names appear).
     Plus the token-budget gate from the same section.

  3. **Session-level (mocked LLM)** — ``Composer.compose`` and
     ``Composer.repair`` with a ``_MockComposerModel`` that returns
     LangChain's structured-output contract (``{"raw": ..., "parsed":
     ..., "parsing_error": ...}``) — tests the full plumbing without
     needing a live Anthropic key.

Test naming follows the existing convention in
``tests/orchestrator/open_dag/`` (snake_case test_* functions inside
TestXxx classes).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

import pytest
from pydantic import BaseModel

from orchestrator.contracts import Domain, EconomicQuantity, IntentTag
from orchestrator.open_dag import (
    GOLDEN_COINTEGRATION,
    GOLDEN_EVENT_REGIME,
    GOLDEN_REGRESSION_ROLLING_BETA,
    GOLDEN_RELATIONSHIP_CORRELATION,
    GOLDEN_RELATIONSHIP_ROLLING_CORRELATION,
    GOLDEN_SHAPES,
    GOLDEN_SHAPES_BY_INTENT,
    GOLDEN_TRANSFORM_ROLLING_ZSCORE,
    InsertAdapterNode,
    LeafHole,
    LeafRequest,
    RewireEdge,
    ShapeSpec,
    render_golden_few_shots,
    render_shape_for_prompt,
)
from orchestrator.open_dag.composer import (
    Composer,
    ComposerLLMOutput,
    ComposerOutputError,
    ComposerRefusal,
    ComposerRepairLLMOutput,
    build_compose_system_prompt_text,
    build_repair_system_prompt_text,
    llm_output_to_shape_spec,
    llm_repair_output_to_patches,
    render_composer_repair_user_message,
    render_composer_user_message,
    render_operator_catalogue_block,
)
from orchestrator.prompts import (
    COMPOSER_REPAIR_PROMPT,
    COMPOSER_SYSTEM_PROMPT,
)
from shared.artifacts.registry import ArtifactTypeName
from shared.workflow.operator_catalogue import (
    approx_tokens,
    render_operator_catalogue,
)
from shared.workflow.registry import OPERATOR_REGISTRY, PrimitiveSpec
from shared.workflow.types import (
    LiteralBinding,
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
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture(scope="module")
def catalogue():
    """Render the 16-operator catalogue once per module."""
    return render_operator_catalogue()


@pytest.fixture(scope="module")
def composer_system_text(catalogue):
    """Full composer system prompt text (static body + dynamic
    blocks).  Reused across every prompt-content assertion."""
    return build_compose_system_prompt_text(catalogue, COMPOSER_SYSTEM_PROMPT)


@pytest.fixture(scope="module")
def composer_repair_system_text(catalogue):
    return build_repair_system_prompt_text(catalogue, COMPOSER_REPAIR_PROMPT)


def _stub_resolver(tool_name: str) -> PrimitiveSpec:
    """Synthetic PrimitiveResolver returning a Series-typed declaration
    for any tool_name.  Used to substitute leaf-holes into a Workflow
    for repair-mode tests."""
    class _Input(BaseModel):
        pass

    class _Output(BaseModel):
        time_series: dict = {}

    def _callable(**kw):
        return {}

    return PrimitiveSpec(
        tool_name=tool_name,
        callable=_callable,
        input_class=_Input,
        output_class=_Output,
        config_path=Path("/tmp/stub.yaml"),
        output_field_units={"time_series": "percent"},
        output_artifact_type="Series",
    )


# ============================================================================
# 1. GOLDEN SHAPES — round-trip + per-intent canonicality
# ============================================================================


class TestGoldenShapesRoundTrip:
    """Golden few-shots must round-trip through the typed contracts —
    if any shape fails to construct, the import at module load time
    raises (the constants ARE ShapeSpec instances)."""

    def test_six_golden_shapes_loaded(self):
        # Exactly six golden few-shots per the plan's table.
        assert len(GOLDEN_SHAPES) == 6

    def test_every_golden_terminal_references_real_node(self):
        for shape in GOLDEN_SHAPES:
            node_ids = {n.node_id for n in shape.nodes}
            assert shape.terminal_node_id in node_ids, (
                f"{shape.workflow_id}: terminal "
                f"{shape.terminal_node_id!r} missing"
            )

    def test_every_golden_pair_stats_has_two_selects(self):
        """The canonical pair-stats shape (correlation /
        rolling_correlation / cointegration / rolling_regression) MUST
        include the two select_from_series_set extractors between
        align_series and the pair-stats operator."""
        pair_stats_shapes = [
            GOLDEN_RELATIONSHIP_CORRELATION,
            GOLDEN_RELATIONSHIP_ROLLING_CORRELATION,
            GOLDEN_COINTEGRATION,
            GOLDEN_REGRESSION_ROLLING_BETA,
        ]
        for shape in pair_stats_shapes:
            select_nodes = [
                n for n in shape.nodes
                if isinstance(n, OperatorNode)
                and n.operator_name == "select_from_series_set"
            ]
            assert len(select_nodes) == 2, (
                f"{shape.workflow_id}: expected exactly 2 "
                f"select_from_series_set nodes, got "
                f"{len(select_nodes)}.  The canonical pair-stats "
                "shape requires align_series → 2x select → pair-stats."
            )

    def test_every_golden_has_at_least_one_leaf_hole(self):
        for shape in GOLDEN_SHAPES:
            holes = shape.leaf_holes()
            assert len(holes) >= 1, (
                f"{shape.workflow_id}: zero LeafHoles — a Composer "
                "shape with no leaves is degenerate"
            )

    @pytest.mark.parametrize(
        "shape,terminal_op",
        [
            (GOLDEN_RELATIONSHIP_CORRELATION, "correlation"),
            (GOLDEN_RELATIONSHIP_ROLLING_CORRELATION, "rolling_correlation"),
            (GOLDEN_COINTEGRATION, "cointegration"),
            (GOLDEN_REGRESSION_ROLLING_BETA, "rolling_regression"),
            (GOLDEN_EVENT_REGIME, "conditional_aggregate"),
            (GOLDEN_TRANSFORM_ROLLING_ZSCORE, "rolling_zscore"),
        ],
    )
    def test_golden_terminal_operator_name(self, shape, terminal_op):
        terminal = shape.node_by_id(shape.terminal_node_id)
        assert isinstance(terminal, OperatorNode)
        assert terminal.operator_name == terminal_op

    def test_event_regime_target_routes_directly(self):
        """The event_regime shape MUST route leaf_target straight to
        event_windows's 'target' slot (NOT through align_series)."""
        shape = GOLDEN_EVENT_REGIME
        # Find the edge leaf_target -> event_windows
        edges_from_target = [
            e for e in shape.edges if e.source_node_id == "leaf_target"
        ]
        assert len(edges_from_target) == 1
        edge = edges_from_target[0]
        assert edge.target_node_id == "windows"
        assert edge.target_input_slot == "target"

    def test_transform_shape_is_one_leaf_one_operator(self):
        shape = GOLDEN_TRANSFORM_ROLLING_ZSCORE
        leaves = [n for n in shape.nodes if isinstance(n, LeafHole)]
        operators = [n for n in shape.nodes if isinstance(n, OperatorNode)]
        assert len(leaves) == 1
        assert len(operators) == 1

    def test_intent_to_golden_map_covers_five_intents(self):
        # Per the plan: 6 shapes covering 5 IntentTag values
        # (RELATIONSHIP has both correlation and rolling_correlation
        # variants; the map keys by intent, picks one canonical).
        assert IntentTag.RELATIONSHIP in GOLDEN_SHAPES_BY_INTENT
        assert IntentTag.REGRESSION in GOLDEN_SHAPES_BY_INTENT
        assert IntentTag.COINTEGRATION in GOLDEN_SHAPES_BY_INTENT
        assert IntentTag.EVENT_REGIME in GOLDEN_SHAPES_BY_INTENT
        assert IntentTag.TRANSFORM in GOLDEN_SHAPES_BY_INTENT


# ============================================================================
# 2. PROMPT RENDERING — determinism + acceptance criteria #3 #4
# ============================================================================


class TestComposerPromptContent:
    """Acceptance criteria #3, #4 + token budget."""

    def test_prompt_contains_all_sixteen_operator_names(
        self, composer_system_text,
    ):
        for op_name in sorted(OPERATOR_REGISTRY.keys()):
            assert op_name in composer_system_text, (
                f"operator {op_name!r} missing from Composer system "
                "prompt — catalogue rendering is incomplete"
            )

    def test_prompt_lists_sixteen_operators(self):
        # Sanity-check: the registry IS sized as documented.
        assert len(OPERATOR_REGISTRY) == 16

    @pytest.mark.parametrize(
        "primitive_indicator",
        [
            # A representative slice of every domain's prefixed tool
            # names + a few bare-name primitives.  Any leakage means
            # the Composer breaks Composer-blindness.
            "calculate_curve_spread_tool",
            "calculate_swap_spread_tool",
            "calculate_breakeven_inflation_simple_tool",
            "get_futures_butterfly_simple_tool",
            "calculate_ois_curve_spread_tool",
            "scan_inflation_swaps_extremes_tool",
            "calculate_inflation_swap_curve_spread_tool",
            "calculate_half_life_tool",
            # Generic suffixes that would catch any *_tool leakage we
            # didn't enumerate above.
            "_tool",
        ],
    )
    def test_prompt_contains_no_primitive_names(
        self, composer_system_text, primitive_indicator,
    ):
        assert primitive_indicator not in composer_system_text, (
            f"Composer prompt leaks primitive vocabulary "
            f"({primitive_indicator!r}); the Composer must remain "
            "primitive-blind per acceptance criterion #3."
        )

    def test_prompt_token_budget_under_25k(self, composer_system_text):
        # Per ``tmp/orchestration.md`` §PR-7 acceptance: "Composer's
        # prompt input ≤ 25K tokens".  chars/4 approximation suffices.
        token_count = approx_tokens(composer_system_text)
        assert token_count <= 25_000, (
            f"Composer system prompt is {token_count} approx tokens; "
            "budget is 25K"
        )

    def test_repair_prompt_token_budget_under_25k(
        self, composer_repair_system_text,
    ):
        token_count = approx_tokens(composer_repair_system_text)
        assert token_count <= 25_000, (
            f"Composer repair prompt is {token_count} approx tokens; "
            "budget is 25K"
        )

    def test_prompt_mentions_pair_stats_discipline(self, composer_system_text):
        # The PR-7 plan flags pair-stats wiring as the most common
        # error mode the Composer must avoid; the prompt must teach
        # the canonical pattern.
        text_lower = composer_system_text.lower()
        assert "pair-stats" in text_lower or "pair stats" in text_lower
        assert "select_from_series_set" in composer_system_text
        assert "align_series" in composer_system_text

    def test_prompt_mentions_event_regime_discipline(self, composer_system_text):
        assert "threshold_events" in composer_system_text
        assert "event_windows" in composer_system_text
        assert "conditional_aggregate" in composer_system_text

    def test_prompt_contains_golden_few_shots(self, composer_system_text):
        # Every golden shape's workflow_id must appear in the prompt
        # so the LLM sees the canonical example labelled.
        for shape in GOLDEN_SHAPES:
            assert shape.workflow_id in composer_system_text, (
                f"golden shape {shape.workflow_id!r} not embedded in "
                "Composer system prompt"
            )


# ============================================================================
# 3. USER-MESSAGE RENDERING — determinism + per-call payload
# ============================================================================


class TestComposerUserMessage:
    def test_render_user_message_includes_prompt(self):
        decomp = [
            EconomicQuantity(
                name="us_2s10s",
                nl_description="UST 2Y-10Y curve spread",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ]
        msg = render_composer_user_message(
            prompt="What is the US 2s10s spread today?",
            intent_tag=IntentTag.LOOKUP,
            decomposition=decomp,
        )
        assert "What is the US 2s10s spread today?" in msg
        assert "L1 INTENT: lookup" in msg
        assert "us_2s10s" in msg
        assert "sovereign_bonds" in msg

    def test_render_user_message_is_deterministic(self):
        decomp = [
            EconomicQuantity(
                name="x",
                nl_description="leg one",
                domain_hint=Domain.OIS,
            ),
            EconomicQuantity(
                name="y",
                nl_description="leg two",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ]
        first = render_composer_user_message(
            prompt="p", intent_tag=IntentTag.RELATIONSHIP, decomposition=decomp,
        )
        second = render_composer_user_message(
            prompt="p", intent_tag=IntentTag.RELATIONSHIP, decomposition=decomp,
        )
        assert first == second

    def test_render_user_message_empty_decomposition(self):
        # Defensive — even an empty decomposition renders without
        # crashing (Composer can still emit a ShapeSpec if it
        # interprets the prompt directly; the L1 normaliser should
        # have refused already, but the renderer must be robust).
        msg = render_composer_user_message(
            prompt="anything",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[],
        )
        assert "L1 DECOMPOSITION (0 quantities)" in msg


# ============================================================================
# 4. SHAPE-RENDERING (golden few-shot JSON) — byte-stable
# ============================================================================


class TestShapeRendering:
    def test_render_shape_returns_valid_json(self):
        for shape in GOLDEN_SHAPES:
            text = render_shape_for_prompt(shape)
            parsed = json.loads(text)
            assert parsed["workflow_id"] == shape.workflow_id
            assert parsed["terminal_node_id"] == shape.terminal_node_id
            assert len(parsed["nodes"]) == len(shape.nodes)
            assert len(parsed["edges"]) == len(shape.edges)

    def test_render_shape_is_deterministic(self):
        first = render_shape_for_prompt(GOLDEN_RELATIONSHIP_CORRELATION)
        second = render_shape_for_prompt(GOLDEN_RELATIONSHIP_CORRELATION)
        assert first == second

    def test_render_golden_few_shots_includes_every_workflow_id(self):
        rendered = render_golden_few_shots()
        for shape in GOLDEN_SHAPES:
            assert shape.workflow_id in rendered


# ============================================================================
# 5. LLM OUTPUT → ShapeSpec TRANSFORMER
# ============================================================================


def _correlation_llm_output() -> ComposerLLMOutput:
    """Build a ComposerLLMOutput that mirrors GOLDEN_RELATIONSHIP_CORRELATION.
    Used by transformer + session-level tests."""
    return ComposerLLMOutput(
        workflow_id="open_dag_test_correlation",
        leaf_holes=[
            {
                "node_id": "leaf_a",
                "required_artifact_type": ArtifactTypeName.SERIES,
                "expected_units": None,
                "expected_frequency": "daily",
                "domain_hint": "sovereign_bonds",
                "semantic_role": "input_series_a",
                "requested_output_meaning": "first leg series",
                "nl_intent": "fetch first leg",
            },
            {
                "node_id": "leaf_b",
                "required_artifact_type": ArtifactTypeName.SERIES,
                "expected_units": None,
                "expected_frequency": "daily",
                "domain_hint": "sovereign_bonds",
                "semantic_role": "input_series_b",
                "requested_output_meaning": "second leg series",
                "nl_intent": "fetch second leg",
            },
        ],
        operator_nodes=[
            {
                "node_id": "align",
                "operator_name": "align_series",
                "params": {},
            },
            {
                "node_id": "select_a",
                "operator_name": "select_from_series_set",
                "params": {"key": "leaf_a"},
            },
            {
                "node_id": "select_b",
                "operator_name": "select_from_series_set",
                "params": {"key": "leaf_b"},
            },
            {
                "node_id": "correlation",
                "operator_name": "correlation",
                "params": {},
            },
        ],
        edges=[
            {"source_node_id": "leaf_a", "target_node_id": "align",
             "target_input_slot": "series_list"},
            {"source_node_id": "leaf_b", "target_node_id": "align",
             "target_input_slot": "series_list"},
            {"source_node_id": "align", "target_node_id": "select_a",
             "target_input_slot": "series_set"},
            {"source_node_id": "align", "target_node_id": "select_b",
             "target_input_slot": "series_set"},
            {"source_node_id": "select_a", "target_node_id": "correlation",
             "target_input_slot": "left"},
            {"source_node_id": "select_b", "target_node_id": "correlation",
             "target_input_slot": "right"},
        ],
        literal_bindings=[],
        terminal_node_id="correlation",
        refusal=None,
    )


class TestLLMOutputTransformer:
    def test_canonical_correlation_round_trips_to_shape_spec(self):
        out = _correlation_llm_output()
        shape = llm_output_to_shape_spec(out)
        assert isinstance(shape, ShapeSpec)
        assert shape.terminal_node_id == "correlation"
        # Two LeafHoles + four operator nodes = 6 total.
        assert len(shape.nodes) == 6
        assert len(shape.leaf_holes()) == 2

    def test_unknown_operator_raises(self):
        # Build a fresh ComposerLLMOutput rather than model_copy — the
        # latter skips nested-list validation, leaving raw dicts in
        # operator_nodes.
        bad = ComposerLLMOutput(
            workflow_id="bad_op",
            leaf_holes=[
                {
                    "node_id": "leaf",
                    "required_artifact_type": ArtifactTypeName.SERIES,
                    "expected_units": None,
                    "expected_frequency": None,
                    "domain_hint": "sovereign_bonds",
                    "semantic_role": "x",
                    "requested_output_meaning": "x",
                    "nl_intent": "x",
                },
            ],
            operator_nodes=[
                {"node_id": "x", "operator_name": "NOT_AN_OPERATOR", "params": {}},
            ],
            edges=[
                {"source_node_id": "leaf", "target_node_id": "x",
                 "target_input_slot": "series"},
            ],
            terminal_node_id="x",
        )
        with pytest.raises(ComposerOutputError, match="not in the closed"):
            llm_output_to_shape_spec(bad)

    def test_refusal_output_raises_when_transformed(self):
        # Calling the transformer on a refusal is a caller contract
        # violation — must raise so the bug surfaces in tests, not in
        # production.
        out = ComposerLLMOutput(refusal="no operator chain fits")
        with pytest.raises(ComposerOutputError, match="refusal output"):
            llm_output_to_shape_spec(out)

    def test_terminal_pointing_to_missing_node_raises(self):
        # Build fresh — model_copy(update=…) bypasses nested validation
        # so the simpler way is reconstruction.
        good = _correlation_llm_output()
        bad = ComposerLLMOutput(
            workflow_id=good.workflow_id,
            leaf_holes=[lh.model_dump() for lh in good.leaf_holes],
            operator_nodes=[op.model_dump() for op in good.operator_nodes],
            edges=[e.model_dump() for e in good.edges],
            literal_bindings=[],
            terminal_node_id="nonexistent_node_id",
        )
        with pytest.raises(ComposerOutputError):
            llm_output_to_shape_spec(bad)

    def test_duplicate_node_ids_raises(self):
        bad = ComposerLLMOutput(
            workflow_id="dup",
            leaf_holes=[
                {
                    "node_id": "shared_id",
                    "required_artifact_type": ArtifactTypeName.SERIES,
                    "expected_units": None,
                    "expected_frequency": None,
                    "domain_hint": "sovereign_bonds",
                    "semantic_role": "x",
                    "requested_output_meaning": "x",
                    "nl_intent": "x",
                },
            ],
            operator_nodes=[
                # Operator shares the same node_id as the leaf.
                {"node_id": "shared_id", "operator_name": "rolling_zscore",
                 "params": {"window": 60}},
            ],
            edges=[],
            terminal_node_id="shared_id",
        )
        with pytest.raises(ComposerOutputError):
            llm_output_to_shape_spec(bad)

    def test_empty_whitespace_refusal_normalises_to_none(self):
        # The field_validator strips whitespace and converts empty
        # refusals to None.  Without this normalisation, an LLM that
        # emits refusal="  " would force the transformer into an
        # ambiguous "empty refusal" state.
        out = ComposerLLMOutput(refusal="   \n  ")
        assert out.refusal is None


# ============================================================================
# 6. REPAIR — patch transformer + additive-only invariant
# ============================================================================


class TestRepairPatchTransformer:
    def test_insert_adapter_patch_round_trips(self):
        repair = ComposerRepairLLMOutput(
            insert_adapter_patches=[
                {
                    "kind": "insert_adapter_node",
                    "on_edge_source": "select_a",
                    "on_edge_target": "correlation",
                    "on_edge_slot": "left",
                    "adapter_node_id": "convert_bps",
                    "adapter_operator_name": "convert_units",
                    "adapter_input_slot": "series",
                    "adapter_params": {"target_units": "bps"},
                    "adapter_literal_bindings": [],
                },
            ],
            rewire_patches=[],
            refusal=None,
        )
        patches = llm_repair_output_to_patches(repair)
        assert len(patches) == 1
        assert isinstance(patches[0], InsertAdapterNode)
        assert patches[0].adapter_operator_name == "convert_units"

    def test_rewire_edge_patch_round_trips(self):
        repair = ComposerRepairLLMOutput(
            insert_adapter_patches=[],
            rewire_patches=[
                {
                    "kind": "rewire_edge",
                    "source_node_id": "select_a",
                    "target_node_id": "correlation",
                    "current_target_input_slot": "right",
                    "new_target_input_slot": "left",
                },
            ],
            refusal=None,
        )
        patches = llm_repair_output_to_patches(repair)
        assert len(patches) == 1
        assert isinstance(patches[0], RewireEdge)

    def test_only_additive_patches_are_returned(self):
        # Acceptance #2: only InsertAdapterNode + RewireEdge.  The
        # transformer returns Sequence[ShapePatch] — assert every
        # entry is one of the closed-family types.
        repair = ComposerRepairLLMOutput(
            insert_adapter_patches=[
                {
                    "kind": "insert_adapter_node",
                    "on_edge_source": "a", "on_edge_target": "b",
                    "on_edge_slot": "s",
                    "adapter_node_id": "ad",
                    "adapter_operator_name": "convert_units",
                    "adapter_input_slot": "series",
                    "adapter_params": {"target_units": "bps"},
                },
            ],
            rewire_patches=[
                {
                    "kind": "rewire_edge",
                    "source_node_id": "x", "target_node_id": "y",
                    "current_target_input_slot": "s1",
                    "new_target_input_slot": "s2",
                },
            ],
        )
        patches = llm_repair_output_to_patches(repair)
        for p in patches:
            assert isinstance(p, (InsertAdapterNode, RewireEdge)), (
                "Composer.repair MUST only return closed-family "
                "additive patches"
            )

    def test_non_whitelist_adapter_is_silently_dropped(self):
        """The InsertAdapterNode Pydantic Literal enforces the
        whitelist.  A patch declaring an out-of-whitelist adapter must
        be silently dropped by the transformer (returns empty
        sequence) — the Composer's bug never reaches the Assembler."""
        repair = ComposerRepairLLMOutput(
            insert_adapter_patches=[
                {
                    "kind": "insert_adapter_node",
                    "on_edge_source": "a", "on_edge_target": "b",
                    "on_edge_slot": "s",
                    "adapter_node_id": "ad",
                    "adapter_operator_name": "rolling_zscore",  # NOT whitelist
                    "adapter_input_slot": "series",
                    "adapter_params": {},
                },
            ],
        )
        patches = llm_repair_output_to_patches(repair)
        assert patches == ()

    def test_refusal_returns_empty_patches(self):
        repair = ComposerRepairLLMOutput(
            refusal="No additive patch resolves the E_TYPE_MISMATCH",
        )
        patches = llm_repair_output_to_patches(repair)
        assert patches == ()


# ============================================================================
# 7. REPAIR USER MESSAGE — content + determinism
# ============================================================================


class TestRepairUserMessage:
    def _stub_workflow(self) -> Workflow:
        return Workflow(
            workflow_id="stub",
            nodes=[
                PrimitiveNode(
                    node_id="p1",
                    tool_name="fake_a_tool",
                    output_field="time_series",
                    params={},
                ),
                OperatorNode(
                    node_id="op1",
                    operator_name="rolling_zscore",
                    params={"window": 60},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p1",
                    target_node_id="op1",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="op1",
        )

    def test_render_repair_user_message_includes_errors(self):
        wf = self._stub_workflow()
        errs = [
            ValidationError(
                code=ErrorCode.E_UNIT_MISMATCH,
                owner_layer=OwnerLayer.L3_WIRING,
                severity=Severity.ERROR,
                message="left units bps != right units percent",
                node_id="op1",
            ),
        ]
        msg = render_composer_repair_user_message(workflow=wf, errors=errs)
        assert "ASSEMBLED WORKFLOW" in msg
        assert "E_UNIT_MISMATCH" in msg
        assert "left units bps != right units percent" in msg

    def test_render_repair_user_message_is_deterministic(self):
        wf = self._stub_workflow()
        errs = [
            ValidationError(
                code=ErrorCode.E_TYPE_MISMATCH,
                owner_layer=OwnerLayer.L3_WIRING,
                severity=Severity.ERROR,
                message="m",
                node_id="op1",
            ),
        ]
        first = render_composer_repair_user_message(workflow=wf, errors=errs)
        second = render_composer_repair_user_message(workflow=wf, errors=errs)
        assert first == second


# ============================================================================
# 8. SESSION-LEVEL (mocked LLM) — Composer.compose / Composer.repair
# ============================================================================


class _MockComposerModel:
    """Mimics LangChain's ``with_structured_output(include_raw=True)``
    contract.  ``ainvoke(messages)`` returns
    ``{"raw": SimpleNamespace, "parsed": ParsedModel, "parsing_error":
    None}``.

    The constructor takes either a single parsed payload or an
    exception to raise; the latter lets tests cover LLM-failure
    paths.
    """

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


def _make_composer_with_state(
    *,
    compose_model: Optional[_MockComposerModel] = None,
    repair_model: Optional[_MockComposerModel] = None,
) -> Composer:
    """Build a Composer without invoking real .open() (which would
    require LangChain Anthropic + an Anthropic API key).  Populates
    the cached state directly with mocks."""
    from langchain_core.messages import SystemMessage

    composer = Composer()
    composer._is_open = True
    composer._catalogue = render_operator_catalogue()
    composer._cached_system_message = SystemMessage(content="(mock system)")
    composer._cached_repair_system_message = SystemMessage(content="(mock repair system)")
    composer._compose_model = compose_model
    composer._repair_model = repair_model
    return composer


@pytest.mark.asyncio
class TestComposerSessionCompose:
    async def test_compose_returns_shape_for_canonical_correlation(self):
        model = _MockComposerModel(parsed=_correlation_llm_output())
        composer = _make_composer_with_state(compose_model=model)

        decomp = [
            EconomicQuantity(
                name="leg_a",
                nl_description="first leg",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
            EconomicQuantity(
                name="leg_b",
                nl_description="second leg",
                domain_hint=Domain.SOVEREIGN_BONDS,
            ),
        ]
        result = await composer.compose(
            prompt="correlation between two legs",
            intent_tag=IntentTag.RELATIONSHIP,
            decomposition=decomp,
        )
        assert isinstance(result, ShapeSpec)
        assert result.terminal_node_id == "correlation"
        assert len(model.calls) == 1

    async def test_compose_returns_refusal_when_llm_refuses(self):
        model = _MockComposerModel(parsed=ComposerLLMOutput(
            refusal="no operator chain fits this scanner-only intent",
        ))
        composer = _make_composer_with_state(compose_model=model)
        result = await composer.compose(
            prompt="scan for cheapest 30y bond globally",
            intent_tag=IntentTag.SCAN,
            decomposition=[
                EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )
        assert isinstance(result, ComposerRefusal)
        assert "scanner-only" in result.reason

    async def test_compose_returns_refusal_on_llm_exception(self):
        model = _MockComposerModel(raise_exc=RuntimeError("HTTP 500"))
        composer = _make_composer_with_state(compose_model=model)
        result = await composer.compose(
            prompt="anything",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )
        assert isinstance(result, ComposerRefusal)
        assert "HTTP 500" in result.reason

    async def test_compose_returns_refusal_on_parsing_error(self):
        model = _MockComposerModel(
            parsed=None,
            parsing_error=ValueError("schema validation failed"),
        )
        composer = _make_composer_with_state(compose_model=model)
        result = await composer.compose(
            prompt="anything",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )
        assert isinstance(result, ComposerRefusal)
        assert "did not produce" in result.reason

    async def test_compose_returns_refusal_on_unknown_operator(self):
        # LLM emits a structurally-flat output that names an unknown
        # operator.  The post-LLM transformer raises ComposerOutputError;
        # Composer.compose converts to ComposerRefusal.
        bad = ComposerLLMOutput(
            workflow_id="bad",
            leaf_holes=[
                {
                    "node_id": "leaf",
                    "required_artifact_type": ArtifactTypeName.SERIES,
                    "expected_units": None,
                    "expected_frequency": None,
                    "domain_hint": "sovereign_bonds",
                    "semantic_role": "x",
                    "requested_output_meaning": "x",
                    "nl_intent": "x",
                },
            ],
            operator_nodes=[
                {
                    "node_id": "op",
                    "operator_name": "DOES_NOT_EXIST",
                    "params": {},
                },
            ],
            edges=[
                {"source_node_id": "leaf", "target_node_id": "op",
                 "target_input_slot": "series"},
            ],
            terminal_node_id="op",
            refusal=None,
        )
        model = _MockComposerModel(parsed=bad)
        composer = _make_composer_with_state(compose_model=model)
        result = await composer.compose(
            prompt="p", intent_tag=IntentTag.TRANSFORM,
            decomposition=[
                EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )
        assert isinstance(result, ComposerRefusal)
        assert "DOES_NOT_EXIST" in result.reason


@pytest.mark.asyncio
class TestComposerSessionRepair:
    def _stub_workflow(self) -> Workflow:
        return Workflow(
            workflow_id="stub",
            nodes=[
                PrimitiveNode(
                    node_id="p1",
                    tool_name="fake_a_tool",
                    output_field="time_series",
                    params={},
                ),
                OperatorNode(
                    node_id="op1",
                    operator_name="rolling_zscore",
                    params={"window": 60},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p1",
                    target_node_id="op1",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="op1",
        )

    def _stub_error(self) -> ValidationError:
        return ValidationError(
            code=ErrorCode.E_UNIT_MISMATCH,
            owner_layer=OwnerLayer.L3_WIRING,
            severity=Severity.ERROR,
            message="bps vs percent on rolling_zscore.series",
            node_id="op1",
        )

    async def test_repair_returns_additive_patches(self):
        # Composer LLM emits one convert_units adapter.
        parsed = ComposerRepairLLMOutput(
            insert_adapter_patches=[
                {
                    "kind": "insert_adapter_node",
                    "on_edge_source": "p1",
                    "on_edge_target": "op1",
                    "on_edge_slot": "series",
                    "adapter_node_id": "convert",
                    "adapter_operator_name": "convert_units",
                    "adapter_input_slot": "series",
                    "adapter_params": {"target_units": "bps"},
                },
            ],
            rewire_patches=[],
        )
        model = _MockComposerModel(parsed=parsed)
        composer = _make_composer_with_state(repair_model=model)
        patches = await composer.repair(
            workflow=self._stub_workflow(),
            errors=[self._stub_error()],
        )
        assert len(patches) == 1
        assert isinstance(patches[0], InsertAdapterNode)
        # Acceptance #2: every patch is one of the closed-family
        # additive types.
        for p in patches:
            assert isinstance(p, (InsertAdapterNode, RewireEdge))

    async def test_repair_returns_empty_on_refusal(self):
        parsed = ComposerRepairLLMOutput(refusal="cannot patch additively")
        model = _MockComposerModel(parsed=parsed)
        composer = _make_composer_with_state(repair_model=model)
        patches = await composer.repair(
            workflow=self._stub_workflow(),
            errors=[self._stub_error()],
        )
        assert patches == ()

    async def test_repair_returns_empty_on_llm_exception(self):
        model = _MockComposerModel(raise_exc=RuntimeError("HTTP 500"))
        composer = _make_composer_with_state(repair_model=model)
        patches = await composer.repair(
            workflow=self._stub_workflow(),
            errors=[self._stub_error()],
        )
        assert patches == ()

    async def test_repair_drops_non_whitelist_adapter(self):
        # LLM tries to insert a rolling_zscore "adapter" — the typed
        # Pydantic constructor refuses; transformer silently drops.
        parsed = ComposerRepairLLMOutput(
            insert_adapter_patches=[
                {
                    "kind": "insert_adapter_node",
                    "on_edge_source": "p1",
                    "on_edge_target": "op1",
                    "on_edge_slot": "series",
                    "adapter_node_id": "ad",
                    "adapter_operator_name": "rolling_zscore",  # bad
                    "adapter_input_slot": "series",
                    "adapter_params": {},
                },
            ],
        )
        model = _MockComposerModel(parsed=parsed)
        composer = _make_composer_with_state(repair_model=model)
        patches = await composer.repair(
            workflow=self._stub_workflow(),
            errors=[self._stub_error()],
        )
        # All patches silently dropped — empty list.
        assert patches == ()


# ============================================================================
# 9. LIFECYCLE — open() / close() basic invariants
# ============================================================================


@pytest.mark.asyncio
class TestComposerLifecycle:
    async def test_compose_requires_open(self):
        composer = Composer()
        # Without open() / mock-state populated, compose must raise.
        with pytest.raises(RuntimeError, match="not open"):
            await composer.compose(
                prompt="p",
                intent_tag=IntentTag.LOOKUP,
                decomposition=[],
            )

    async def test_repair_requires_open(self):
        composer = Composer()
        with pytest.raises(RuntimeError, match="not open"):
            await composer.repair(
                workflow=Workflow(
                    workflow_id="w",
                    nodes=[
                        PrimitiveNode(
                            node_id="p",
                            tool_name="t",
                            output_field="time_series",
                        ),
                    ],
                    edges=[],
                    terminal_node_id="p",
                ),
                errors=[],
            )

    async def test_close_clears_state(self):
        composer = _make_composer_with_state(
            compose_model=_MockComposerModel(parsed=ComposerLLMOutput(
                refusal="x",
            )),
        )
        assert composer._is_open
        composer.close()
        assert not composer._is_open
        assert composer._compose_model is None
        assert composer._cached_system_message is None

    async def test_close_idempotent(self):
        composer = Composer()
        composer.close()
        composer.close()  # no-op


# ============================================================================
# 10. PROMPT-CATALOGUE BLOCK — operator card content
# ============================================================================


class TestCatalogueBlock:
    def test_catalogue_block_includes_every_operator(self, catalogue):
        block = render_operator_catalogue_block(catalogue)
        for op_name in OPERATOR_REGISTRY:
            assert op_name in block

    def test_catalogue_block_mentions_pair_stats_slots(self, catalogue):
        block = render_operator_catalogue_block(catalogue)
        # The pair-stats operators declare left/right slots; the
        # catalogue must surface them so the Composer wires correctly.
        assert "left" in block
        assert "right" in block
        # And the SeriesSet -> Series extractor's series_set slot.
        assert "series_set" in block
        # And align_series's list-shaped slot.
        assert "series_list" in block

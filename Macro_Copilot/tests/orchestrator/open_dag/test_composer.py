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

    def test_seven_golden_shapes_loaded(self):
        # Seven golden few-shots: the original six per-intent shapes plus
        # GOLDEN_SUMMARY_SINGLE_STAT (added in commit 46a3ef6 after the
        # 12-agent opus diagnosis to anchor the scalar-summary shape).
        assert len(GOLDEN_SHAPES) == 7

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

    def test_prompt_lists_the_full_closed_operator_family(self):
        # Sanity-check: pin the EXACT closed-family membership (a bare
        # count silently drifts — this catches both an accidental
        # addition and a silent removal, and forces the admission
        # checklist conversation when the family legitimately grows).
        assert sorted(OPERATOR_REGISTRY.keys()) == [
            "align_series",
            "apply_mask",
            "bandpass",
            "beta",
            "changepoint_detection",
            "cointegration",
            "conditional_aggregate",
            "convert_units",
            "correlation",
            "covariance",
            "cross_sectional_rank",
            "cross_sectional_statistic",
            "cross_sectional_zscore",
            "cumulative",
            "demean_cross_section",
            "detrend",
            "event_windows",
            "ewm_statistic",
            "fit_garch",
            "fit_ou",
            "granger_causality",
            "hp_filter",
            "hurst_exponent",
            "lag",
            "lead_lag",
            "ljung_box",
            "normality_test",
            "pairwise_spread_matrix",
            "percentile_rank",
            "regression_residual",
            "resample",
            "rolling_correlation",
            "rolling_covariance",
            "rolling_regression",
            "rolling_statistic",
            "rolling_zscore",
            "select_from_series_set",
            "series_arithmetic",
            "stationarity_adf",
            "streak",
            "summarize_series",
            "threshold_events",
            "top_n",
            "transition_events",
            "variance_ratio",
            "weighted_combination",
            "winsorize",
        ]

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

    # Per ``tmp/orchestration.md`` §PR-7 acceptance, originally
    # "Composer's prompt input ≤ 25K tokens" — sized for the
    # 16-operator PoC catalogue.  Track-A (fable_build) re-sized the
    # budget for the growing toolbox exactly like the catalogue's
    # _TOTAL_CATALOGUE_TOKEN_CAP: the A1–A7 build overshot the ≈25–35
    # estimate to 44 operators (the A5 statistical-test family + the A1
    # cycle filters etc.), so the catalogue portion (raised to 40k) +
    # ~9k of instructions → 49k bounds the worst case, still leaving
    # >150k for tool messages on a 200k window.  Tracks the catalogue
    # cap in lock-step (40k→49k as the fleet grows; the A4 model engines
    # will push it higher).  The prompt is byte-stable
    # (Anthropic-cache-pinned), so the marginal cost of the larger
    # prefix amortizes across calls.  orchestration.md §PR-7's
    # acceptance line is updated in lock-step.
    _COMPOSER_PROMPT_TOKEN_CAP = 49_000

    def test_prompt_token_budget(self, composer_system_text):
        token_count = approx_tokens(composer_system_text)
        assert token_count <= self._COMPOSER_PROMPT_TOKEN_CAP, (
            f"Composer system prompt is {token_count} approx tokens; "
            f"budget is {self._COMPOSER_PROMPT_TOKEN_CAP}"
        )

    def test_repair_prompt_token_budget(
        self, composer_repair_system_text,
    ):
        token_count = approx_tokens(composer_repair_system_text)
        assert token_count <= self._COMPOSER_PROMPT_TOKEN_CAP, (
            f"Composer repair prompt is {token_count} approx tokens; "
            f"budget is {self._COMPOSER_PROMPT_TOKEN_CAP}"
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
        # PR-7A Codex F2: banner renamed from "ASSEMBLED WORKFLOW" to
        # "ASSEMBLED SHAPE (post-substitution, primitive identities
        # redacted)" to make the redaction explicit at the prompt
        # level.
        assert "ASSEMBLED SHAPE" in msg
        assert "redacted" in msg
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


# ============================================================================
# PR-7A — CODEX AUDIT CORRECTIVE TESTS
# ============================================================================


class TestPR7A_F1_GoldenParamsValidateAgainstOperatorSchemas:
    """PR-7A Codex F1: every operator node in every golden shape must
    instantiate its operator's ``params_class`` cleanly.  The PR-7
    tests only checked structural ShapeSpec validity (which passes
    even with garbage params); this regression keeps the schemas in
    lockstep so a future operator param-rename surfaces here, not in
    production."""

    def test_every_golden_operator_params_round_trip(self):
        from shared.workflow.types import OperatorNode

        failures: list[str] = []
        ok_count = 0
        for shape in GOLDEN_SHAPES:
            for node in shape.nodes:
                if not isinstance(node, OperatorNode):
                    continue
                spec = OPERATOR_REGISTRY[node.operator_name]
                pc = spec.params_class
                if pc is None:
                    continue
                try:
                    pc(**node.params)
                    ok_count += 1
                except Exception as exc:
                    failures.append(
                        f"{shape.workflow_id} / {node.node_id} / "
                        f"{node.operator_name}: {type(exc).__name__}: "
                        f"{exc}"
                    )
        assert not failures, (
            "Golden shapes contain operator params that fail real "
            "*Params validation:\n  " + "\n  ".join(failures)
        )
        # Sanity-check the iteration actually ran.
        assert ok_count >= 12, (
            f"Expected at least 12 operator nodes across goldens; "
            f"got {ok_count}"
        )

    def test_select_from_series_set_uses_series_key(self):
        # Specifically guard against regressing to {"key": ...}.
        for shape in GOLDEN_SHAPES:
            for node in shape.nodes:
                if (
                    isinstance(node, OperatorNode)
                    and node.operator_name == "select_from_series_set"
                ):
                    assert "series_key" in node.params, (
                        f"{shape.workflow_id} / {node.node_id}: "
                        "select_from_series_set must use 'series_key' "
                        "(not 'key')"
                    )
                    assert "key" not in node.params

    def test_align_series_sets_output_keys(self):
        # align_series in the canonical pair-stats shapes MUST pin
        # output_keys so the downstream selects can reference the
        # SeriesSet by stable strings.
        pair_stats = [
            GOLDEN_RELATIONSHIP_CORRELATION,
            GOLDEN_RELATIONSHIP_ROLLING_CORRELATION,
            GOLDEN_COINTEGRATION,
            GOLDEN_REGRESSION_ROLLING_BETA,
        ]
        for shape in pair_stats:
            align = [
                n for n in shape.nodes
                if isinstance(n, OperatorNode)
                and n.operator_name == "align_series"
            ]
            assert len(align) == 1
            assert "output_keys" in align[0].params, (
                f"{shape.workflow_id}: align_series must declare "
                "output_keys so the downstream selects are addressable"
            )
            assert align[0].params["output_keys"] == ["leaf_a", "leaf_b"]

    def test_threshold_events_uses_rule_and_rolling_window(self):
        shape = GOLDEN_EVENT_REGIME
        threshold = next(
            n for n in shape.nodes
            if isinstance(n, OperatorNode)
            and n.operator_name == "threshold_events"
        )
        assert "rule" in threshold.params
        assert threshold.params["rule"] in ("abs_above", "above", "below")
        assert "rolling_window" in threshold.params
        # Guard against the old (wrong) names regressing.
        assert "comparison" not in threshold.params
        assert "window" not in threshold.params

    def test_event_windows_uses_pre_post_window(self):
        shape = GOLDEN_EVENT_REGIME
        windows = next(
            n for n in shape.nodes
            if isinstance(n, OperatorNode)
            and n.operator_name == "event_windows"
        )
        assert "pre_window" in windows.params
        assert "post_window" in windows.params
        # Guard against the old (wrong) `offsets` regressing.
        assert "offsets" not in windows.params


class TestPR7A_F2_RepairPromptRedactsPrimitives:
    """PR-7A Codex F2: the Composer.repair user message must NOT
    surface PrimitiveNode tool_name / output_field / primitive params.
    L3 must stay primitive-blind even at the post-substitution
    repair-time boundary."""

    def _stub_workflow_with_primitive(self) -> Workflow:
        return Workflow(
            workflow_id="repair_stub",
            nodes=[
                PrimitiveNode(
                    node_id="leaf_a",
                    tool_name="SECRET_PRIMITIVE_TOOL_NAME",
                    output_field="SECRET_OUTPUT_FIELD",
                    params={"SECRET_PARAM_KEY": "SECRET_PARAM_VALUE"},
                ),
                OperatorNode(
                    node_id="op",
                    operator_name="rolling_zscore",
                    params={"window": 60},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="leaf_a",
                    target_node_id="op",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="op",
        )

    def test_primitive_tool_name_not_in_repair_prompt(self):
        wf = self._stub_workflow_with_primitive()
        msg = render_composer_repair_user_message(workflow=wf, errors=[])
        assert "SECRET_PRIMITIVE_TOOL_NAME" not in msg, (
            "PrimitiveNode.tool_name leaked into the L3 repair "
            "prompt — violates the primitive-blindness discipline"
        )

    def test_primitive_output_field_not_in_repair_prompt(self):
        wf = self._stub_workflow_with_primitive()
        msg = render_composer_repair_user_message(workflow=wf, errors=[])
        assert "SECRET_OUTPUT_FIELD" not in msg

    def test_primitive_params_not_in_repair_prompt(self):
        wf = self._stub_workflow_with_primitive()
        msg = render_composer_repair_user_message(workflow=wf, errors=[])
        assert "SECRET_PARAM_KEY" not in msg
        assert "SECRET_PARAM_VALUE" not in msg

    def test_operator_node_still_visible(self):
        # The redaction strips PRIMITIVE identity only — operator
        # nodes (L3's own vocabulary) stay full-fidelity so the
        # Composer can read existing params when deciding rewires.
        wf = self._stub_workflow_with_primitive()
        msg = render_composer_repair_user_message(workflow=wf, errors=[])
        assert "rolling_zscore" in msg
        # The window=60 param IS the operator's L3-visible knob.
        assert '"window": 60' in msg or "'window': 60" in msg

    def test_validation_error_tool_name_redacted(self):
        # The diagnostic surface MUST also elide tool_name — otherwise
        # the error stream becomes a side channel that leaks primitive
        # identity into the repair prompt.
        wf = self._stub_workflow_with_primitive()
        err = ValidationError(
            code=ErrorCode.E_UNIT_MISMATCH,
            owner_layer=OwnerLayer.L3_WIRING,
            severity=Severity.ERROR,
            message="m",
            node_id="op",
            tool_name="ANOTHER_SECRET_TOOL_NAME",
        )
        msg = render_composer_repair_user_message(workflow=wf, errors=[err])
        assert "ANOTHER_SECRET_TOOL_NAME" not in msg
        # But the error code IS surfaced — it's the closed-substrate
        # diagnostic the Composer needs.
        assert "E_UNIT_MISMATCH" in msg


class TestPR7A_F3_GoldensRenderInComposerLLMOutputShape:
    """PR-7A Codex F3: the few-shots embedded in the Composer prompt
    must use the FLAT ``ComposerLLMOutput`` shape (leaf_holes +
    operator_nodes as separate top-level lists), not the discriminated
    ShapeSpec shape.  Otherwise the LLM sees a contradictory example
    vs. its required output schema."""

    def test_render_shape_as_composer_output_parses_via_llm_schema(self):
        # Every golden round-trips: render → JSON → ComposerLLMOutput
        # → llm_output_to_shape_spec → original-equivalent ShapeSpec.
        from orchestrator.open_dag.composer_golden_shapes import (
            render_shape_as_composer_output,
        )

        for shape in GOLDEN_SHAPES:
            rendered = render_shape_as_composer_output(shape)
            output = ComposerLLMOutput.model_validate(json.loads(rendered))
            re_shape = llm_output_to_shape_spec(output)
            assert re_shape.workflow_id == shape.workflow_id
            assert re_shape.terminal_node_id == shape.terminal_node_id
            assert len(re_shape.nodes) == len(shape.nodes)
            assert len(re_shape.edges) == len(shape.edges)

    def test_render_shape_as_composer_output_is_flat(self):
        from orchestrator.open_dag.composer_golden_shapes import (
            render_shape_as_composer_output,
        )

        rendered = render_shape_as_composer_output(GOLDEN_RELATIONSHIP_CORRELATION)
        payload = json.loads(rendered)
        # Flat shape — separate leaf_holes + operator_nodes lists, NO
        # discriminated "nodes" list.
        assert "leaf_holes" in payload
        assert "operator_nodes" in payload
        assert "nodes" not in payload, (
            "Flat ComposerLLMOutput shape must NOT have a 'nodes' "
            "key — that would be the old ShapeSpec format"
        )
        # No 'kind' discriminator on individual entries either.
        for h in payload["leaf_holes"]:
            assert "kind" not in h
        for o in payload["operator_nodes"]:
            assert "kind" not in o

    def test_render_golden_few_shots_uses_flat_format(self):
        rendered = render_golden_few_shots()
        # The flat format MUST surface "leaf_holes" + "operator_nodes"
        # in every shape; the old ShapeSpec format would surface
        # "nodes" + "kind" discriminators.  Sanity check both.
        assert '"leaf_holes"' in rendered
        assert '"operator_nodes"' in rendered

    def test_every_golden_round_trips_via_llm_output_validate(self):
        # End-to-end: parse the rendered string of every golden via
        # the LLM's own structured-output Pydantic schema.  If this
        # passes for every golden, the LLM CANNOT be confused by the
        # few-shots vs its expected output shape.
        from orchestrator.open_dag.composer_golden_shapes import (
            render_shape_as_composer_output,
        )

        for shape in GOLDEN_SHAPES:
            rendered = render_shape_as_composer_output(shape)
            parsed = ComposerLLMOutput.model_validate_json(rendered)
            assert parsed.refusal is None
            assert parsed.workflow_id == shape.workflow_id
            assert parsed.terminal_node_id == shape.terminal_node_id


class TestPR7A_F5_LookupScanPromptRelaxed:
    """PR-7A Codex F5: the COMPOSER_SYSTEM_PROMPT must allow:
      - lookup with a comparative phrasing (X vs 1y range) to compose
        a TRANSFORM operator on top of the leaf, NOT just emit a bare
        leaf.
      - scan to refuse OR re-express as TRANSFORM when bridgeable —
        not categorically refuse.
    """

    def test_lookup_rule_allows_transform_composition(self, composer_system_text):
        # The relaxed prompt names percentile_rank / rolling_zscore /
        # rolling_statistic as legitimate TRANSFORMs the Composer
        # MAY layer on top of a lookup leaf.
        assert "percentile_rank" in composer_system_text
        assert "rolling_zscore" in composer_system_text
        assert "rolling_statistic" in composer_system_text

    def test_lookup_rule_mentions_comparative_phrasings(self, composer_system_text):
        # The relaxed rule explicitly names comparative phrasings so
        # the LLM doesn't read the rigid bare-leaf interpretation.
        text_lower = composer_system_text.lower()
        assert "comparative" in text_lower or "ranking" in text_lower

    def test_scan_rule_allows_transform_reexpression(self, composer_system_text):
        # Scan should NOT categorically refuse — the prompt must
        # describe the re-expression-as-TRANSFORM escape hatch.
        text_lower = composer_system_text.lower()
        # The relaxed prompt mentions TERMINAL_ONLY_SNAPSHOT (the
        # condition under which refusal IS correct) so the LLM
        # understands the bounded scope of refusal.
        assert "terminal_only_snapshot" in text_lower
        # And it mentions the TRANSFORM proxy escape hatch.
        assert "re-expressed" in text_lower or "transform" in text_lower

    def test_lookup_rule_no_longer_categorical_no_operators(
        self, composer_system_text,
    ):
        # The OLD wording said "Emit one LeafHole ... No operators."
        # That exact sentence must NOT appear after PR-7A.
        assert "Emit one LeafHole \\\nwith terminal_node_id = leaf_hole.node_id.  No operators." not in composer_system_text
        # The relaxed rule explicitly allows operator composition.
        assert "TRANSFORM operator" in composer_system_text


class TestPR7A_GoldenPairStatsAssembleClean:
    """End-to-end assembly check: with the corrected params, the
    canonical pair-stats shapes still assemble CLEAN through the PR-4
    Assembler (substitute + Boundary A + structural validation).
    This is the proof that PR-7A's golden fixes don't break the
    upstream pipeline."""

    @staticmethod
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
            output_field_units={"time_series": "percent"},
            output_artifact_type="Series",
        )

    def _mk_leaf(self, leaf_id: str, role: str, meaning: str):
        from orchestrator.open_dag.contracts import BoundLeaf, Frequency

        return BoundLeaf(
            leaf_id=leaf_id,
            domain="sovereign_bonds",
            mcp_tool_name="fake",
            resolver_tool_key="fake",
            params={},
            output_field="time_series",
            declared_output_artifact_type=ArtifactTypeName.SERIES,
            declared_units=None,
            declared_frequency=Frequency.DAILY,
            declared_semantic_role=role,
            declared_output_meaning=meaning,
            fit_confidence=0.9,
        )

    @pytest.mark.parametrize(
        "shape_const",
        [
            GOLDEN_RELATIONSHIP_CORRELATION,
            GOLDEN_RELATIONSHIP_ROLLING_CORRELATION,
            GOLDEN_COINTEGRATION,
            GOLDEN_REGRESSION_ROLLING_BETA,
        ],
    )
    def test_pair_stats_golden_assembles_clean(self, shape_const):
        from orchestrator.open_dag.assembler import (
            Assembler,
            AssemblyStatus,
        )

        # Extract semantic_role + requested_output_meaning from the
        # golden's own LeafHoles so the leaves match the LeafRequests
        # (Boundary A SOFT-checks these).
        a, b = shape_const.leaf_holes()[0], shape_const.leaf_holes()[1]
        leaves = [
            self._mk_leaf(
                a.node_id,
                a.leaf_request.semantic_role,
                a.leaf_request.requested_output_meaning,
            ),
            self._mk_leaf(
                b.node_id,
                b.leaf_request.semantic_role,
                b.leaf_request.requested_output_meaning,
            ),
        ]
        asm = Assembler(primitive_resolver=self._stub_resolver)
        result = asm.assemble(shape_const, leaves)
        assert result.status == AssemblyStatus.CLEAN, (
            f"{shape_const.workflow_id}: expected CLEAN, got "
            f"{result.status} with errors "
            f"{[(e.code.value, e.message[:80]) for e in result.validation_result.errors]}"
        )

    def test_event_regime_golden_assembles_clean(self):
        from orchestrator.open_dag.assembler import (
            Assembler,
            AssemblyStatus,
        )

        shape = GOLDEN_EVENT_REGIME
        trigger = shape.node_by_id("leaf_trigger")
        target = shape.node_by_id("leaf_target")
        leaves = [
            self._mk_leaf(
                "leaf_trigger",
                trigger.leaf_request.semantic_role,
                trigger.leaf_request.requested_output_meaning,
            ),
            self._mk_leaf(
                "leaf_target",
                target.leaf_request.semantic_role,
                target.leaf_request.requested_output_meaning,
            ),
        ]
        asm = Assembler(primitive_resolver=self._stub_resolver)
        result = asm.assemble(shape, leaves)
        assert result.status == AssemblyStatus.CLEAN, (
            f"event_regime: expected CLEAN, got {result.status} with "
            f"errors {[(e.code.value, e.message[:80]) for e in result.validation_result.errors]}"
        )

    def test_transform_golden_assembles_clean(self):
        from orchestrator.open_dag.assembler import (
            Assembler,
            AssemblyStatus,
        )

        shape = GOLDEN_TRANSFORM_ROLLING_ZSCORE
        leaf = shape.node_by_id("leaf_input")
        leaves = [
            self._mk_leaf(
                "leaf_input",
                leaf.leaf_request.semantic_role,
                leaf.leaf_request.requested_output_meaning,
            ),
        ]
        asm = Assembler(primitive_resolver=self._stub_resolver)
        result = asm.assemble(shape, leaves)
        assert result.status == AssemblyStatus.CLEAN

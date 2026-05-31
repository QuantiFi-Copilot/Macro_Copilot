"""tests/workflow/test_operator_catalogue.py — PR-2 acceptance suite.

Covers ``shared.workflow.operator_catalogue`` + the per-operator
``card:`` blocks in each ``shared/operators/*/config.yaml``.

The plan (``tmp/orchestration.md`` §PR-2) specifies:

  1. Every registered operator has a non-empty ``card:`` block; loader
     green.
  2. Each card passes schema validation (all fields populated; lists
     non-empty per the OperatorCard contract).
  3. Token-budget cap: rendered card ≤ 600 tokens / total ≤ 10K
     tokens (Composer prompt budget for PR-7).
  4. Sibling cross-reference: each of the four pair-stats operators
     (correlation, rolling_correlation, cointegration,
     rolling_regression) names the other three in
     ``sibling_operators``.
  5. Renderer determinism: same OperatorSpec + same config.yaml →
     byte-identical ``OperatorCard.model_dump_json()``.
  6. P10: description content lives ONLY in config.yaml — not
     duplicated into ``OPERATOR_REGISTRY``.
  7. P11 / P9: this module imports nothing from ``rates_agent/``.

All tests are fully offline.  No DB, no MCP subprocess.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict

import pytest

from shared.workflow import OPERATOR_REGISTRY
from shared.workflow.operator_catalogue import (
    ExampleShape,
    OperatorCard,
    OperatorCardError,
    approx_tokens,
    card_to_prompt_block,
    clear_catalogue_cache,
    render_operator_card,
    render_operator_catalogue,
)


# ============================================================================
# CONSTANTS
# ============================================================================


# Per the plan's acceptance criterion (§PR-2 "Acceptance criteria" #4):
#   "Each card ≤ 600 tokens; total catalogue ≤ 10K tokens."
_PER_CARD_TOKEN_CAP = 600
_TOTAL_CATALOGUE_TOKEN_CAP = 10_000


# Per the plan's "Decisions enforced" #3:
#   "The four pair-stats operators MUST cross-reference each other in
#   their sibling_operators blocks — this is the most common Composer
#   mistake and the cross-refs are the primary mitigation."
_PAIR_STATS_OPERATORS: tuple[str, ...] = (
    "correlation",
    "rolling_correlation",
    "cointegration",
    "rolling_regression",
)


# ============================================================================
# FIXTURE — render once per session, share across tests
# ============================================================================


@pytest.fixture(scope="module")
def catalogue() -> Dict[str, OperatorCard]:
    """The full rendered catalogue — what the L3 Composer will see in
    PR-7.  Cleared first to guarantee fresh rendering."""
    clear_catalogue_cache()
    return render_operator_catalogue()


# ============================================================================
# COVERAGE — every registered operator has a card
# ============================================================================


class TestEveryOperatorHasCard:
    def test_catalogue_covers_every_registered_operator(
        self, catalogue: Dict[str, OperatorCard],
    ) -> None:
        registry_names = set(OPERATOR_REGISTRY.keys())
        catalogue_names = set(catalogue.keys())
        missing = registry_names - catalogue_names
        extra = catalogue_names - registry_names
        assert not missing, (
            f"OPERATOR_REGISTRY has {len(missing)} operator(s) with no "
            f"catalogue entry: {sorted(missing)}"
        )
        assert not extra, (
            f"catalogue has entries not in OPERATOR_REGISTRY: "
            f"{sorted(extra)}"
        )

    def test_catalogue_size_matches_registry(
        self, catalogue: Dict[str, OperatorCard],
    ) -> None:
        assert len(catalogue) == len(OPERATOR_REGISTRY) == 16, (
            f"Expected 16 registered operators; got "
            f"registry={len(OPERATOR_REGISTRY)} catalogue={len(catalogue)}.  "
            "If this changed deliberately, update the assertion AND review "
            "tmp/orchestration.md §2.1 for the operator inventory."
        )

    @pytest.mark.parametrize("operator_name", sorted(OPERATOR_REGISTRY.keys()))
    def test_each_operator_loads_without_error(
        self, operator_name: str,
    ) -> None:
        """Render each operator's card individually so a single failure
        surfaces the offending operator clearly."""
        card = render_operator_card(operator_name)
        assert isinstance(card, OperatorCard)
        assert card.operator_name == operator_name


# ============================================================================
# SCHEMA — each card has the required fields
# ============================================================================


class TestCardSchemaPopulated:
    @pytest.mark.parametrize("operator_name", sorted(OPERATOR_REGISTRY.keys()))
    def test_one_line_present(
        self, operator_name: str, catalogue: Dict[str, OperatorCard],
    ) -> None:
        card = catalogue[operator_name]
        assert card.one_line and len(card.one_line.strip()) >= 10, (
            f"{operator_name}: one_line missing or too short"
        )

    @pytest.mark.parametrize("operator_name", sorted(OPERATOR_REGISTRY.keys()))
    def test_when_to_use_non_empty(
        self, operator_name: str, catalogue: Dict[str, OperatorCard],
    ) -> None:
        card = catalogue[operator_name]
        assert card.when_to_use, (
            f"{operator_name}: when_to_use must have at least one entry"
        )
        for entry in card.when_to_use:
            assert entry.strip(), (
                f"{operator_name}: when_to_use contains an empty entry"
            )

    @pytest.mark.parametrize("operator_name", sorted(OPERATOR_REGISTRY.keys()))
    def test_when_not_to_use_non_empty(
        self, operator_name: str, catalogue: Dict[str, OperatorCard],
    ) -> None:
        card = catalogue[operator_name]
        assert card.when_not_to_use, (
            f"{operator_name}: when_not_to_use must have at least one entry "
            "(every operator has a wrong-use case worth naming)"
        )

    @pytest.mark.parametrize("operator_name", sorted(OPERATOR_REGISTRY.keys()))
    def test_input_slots_match_registry(
        self, operator_name: str, catalogue: Dict[str, OperatorCard],
    ) -> None:
        """The rendered card's slot structure must match the registry's
        — P10 single source of truth.  The renderer fuses the YAML's
        description with the registry's structure; this test guards
        against renderer drift."""
        card = catalogue[operator_name]
        spec = OPERATOR_REGISTRY[operator_name]
        assert card.input_slots == spec.input_slots
        assert card.output == spec.output


# ============================================================================
# TOKEN BUDGET (the PR-2 acceptance criterion)
# ============================================================================


class TestTokenBudget:
    @pytest.mark.parametrize("operator_name", sorted(OPERATOR_REGISTRY.keys()))
    def test_per_card_token_cap(
        self, operator_name: str, catalogue: Dict[str, OperatorCard],
    ) -> None:
        card = catalogue[operator_name]
        block = card_to_prompt_block(card)
        tokens = approx_tokens(block)
        assert tokens <= _PER_CARD_TOKEN_CAP, (
            f"{operator_name}: card renders to {tokens} tokens, exceeding "
            f"the {_PER_CARD_TOKEN_CAP}-token cap.  Trim when_to_use / "
            "when_not_to_use / examples / sibling cross-refs in "
            f"shared/operators/{operator_name}/config.yaml `card:` block."
        )

    def test_total_catalogue_token_cap(
        self, catalogue: Dict[str, OperatorCard],
    ) -> None:
        total = sum(
            approx_tokens(card_to_prompt_block(card))
            for card in catalogue.values()
        )
        assert total <= _TOTAL_CATALOGUE_TOKEN_CAP, (
            f"Total catalogue renders to {total} tokens, exceeding the "
            f"{_TOTAL_CATALOGUE_TOKEN_CAP}-token budget.  Trim individual "
            "cards or review the L3 Composer prompt-budget assumption."
        )


# ============================================================================
# PAIR-STATS CROSS-REFERENCE DISCIPLINE
# ============================================================================


class TestPairStatsCrossReferences:
    """The four pair-stats operators MUST name each other in their
    sibling_operators block.  This is the primary mitigation for the
    Composer's most common mistake — picking the wrong member of the
    {correlation, rolling_correlation, cointegration,
    rolling_regression} family."""

    @pytest.mark.parametrize(
        "operator_name", _PAIR_STATS_OPERATORS,
    )
    def test_pair_stats_operator_cross_references_other_three(
        self,
        operator_name: str,
        catalogue: Dict[str, OperatorCard],
    ) -> None:
        card = catalogue[operator_name]
        expected_siblings = set(_PAIR_STATS_OPERATORS) - {operator_name}
        actual_siblings = set(card.sibling_operators.keys())
        missing = expected_siblings - actual_siblings
        assert not missing, (
            f"{operator_name}: pair-stats sibling cross-reference incomplete.  "
            f"Expected to name {sorted(expected_siblings)} in sibling_operators; "
            f"got {sorted(actual_siblings)}.  Missing: {sorted(missing)}.  "
            "Per tmp/orchestration.md §PR-2 'Decisions enforced #3', the four "
            "pair-stats operators MUST cross-reference each other."
        )

    @pytest.mark.parametrize(
        "operator_name", _PAIR_STATS_OPERATORS,
    )
    def test_pair_stats_cross_reference_is_non_empty_text(
        self,
        operator_name: str,
        catalogue: Dict[str, OperatorCard],
    ) -> None:
        card = catalogue[operator_name]
        for sibling, blurb in card.sibling_operators.items():
            assert blurb.strip(), (
                f"{operator_name}: sibling_operators entry for "
                f"{sibling!r} is empty.  Provide a one-sentence "
                "explanation of when to pick the sibling instead."
            )


# ============================================================================
# RENDERER DETERMINISM
# ============================================================================


class TestRendererDeterminism:
    """Same OperatorSpec + same config.yaml → byte-identical
    OperatorCard.model_dump_json().  Required so the L3 Composer's
    Anthropic prompt cache can pin the catalogue once per process."""

    def test_render_is_byte_stable_across_calls(self) -> None:
        clear_catalogue_cache()
        a = render_operator_catalogue()
        clear_catalogue_cache()
        b = render_operator_catalogue()
        for name in a:
            ja = a[name].model_dump_json()
            jb = b[name].model_dump_json()
            assert ja == jb, (
                f"render of {name!r} is not byte-stable: \n  first={ja}\n"
                f"  second={jb}"
            )

    def test_prompt_block_is_byte_stable_across_calls(self) -> None:
        clear_catalogue_cache()
        a = render_operator_catalogue()
        clear_catalogue_cache()
        b = render_operator_catalogue()
        for name in a:
            assert card_to_prompt_block(a[name]) == card_to_prompt_block(b[name])


# ============================================================================
# P10 — NO CONTENT DUPLICATION BETWEEN config.yaml AND REGISTRY
# ============================================================================


class TestP10NoContentDuplication:
    """The registry's OperatorSpec field set must NOT carry description
    text that duplicates config.yaml content.  PR-2 lifts content into
    config.yaml; the registry holds ONLY structural metadata
    (callable, params_class, input_slots, output, config_path,
    arity/unit validators)."""

    _ALLOWED_OPERATOR_SPEC_FIELDS = {
        "operator_name",
        "callable",
        "params_class",
        "input_slots",
        "output",
        "config_path",
        "discriminator_args",
        "arity_validator",
        "unit_validator",
    }

    def test_operator_spec_carries_no_description_field(self) -> None:
        """Adding a description / one_liner / when_to_use field to
        OperatorSpec would violate P10.  Guard against drift."""
        # Read OperatorSpec's declared field set via Pydantic.
        from shared.workflow.registry import OperatorSpec
        declared = set(OperatorSpec.model_fields.keys())
        unexpected = declared - self._ALLOWED_OPERATOR_SPEC_FIELDS
        assert not unexpected, (
            f"OperatorSpec carries unexpected fields {sorted(unexpected)}.  "
            "If a card-content field was added to OperatorSpec, this "
            "duplicates content from config.yaml `card:` block and "
            "violates P10.  Move the field back to the YAML."
        )


# ============================================================================
# P11 / P9 — module is finance-blind
# ============================================================================


class TestModuleFinanceBlind:
    """``shared.workflow.operator_catalogue`` must NOT import from any
    domain-specific path under ``rates_agent/``.  Static AST check."""

    def test_no_rates_agent_imports(self) -> None:
        path = (
            Path(__file__).resolve().parents[2]
            / "shared" / "workflow" / "operator_catalogue.py"
        )
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("rates_agent"), (
                        f"operator_catalogue.py imports {alias.name} — "
                        "this module must stay finance-blind (P11/P9)."
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("rates_agent"), (
                        f"operator_catalogue.py imports from "
                        f"{node.module} — this module must stay "
                        "finance-blind (P11/P9)."
                    )


# ============================================================================
# CACHE BEHAVIOUR
# ============================================================================


class TestCardCache:
    def test_clear_catalogue_cache_works(self) -> None:
        clear_catalogue_cache()
        a = render_operator_card("correlation")
        b = render_operator_card("correlation")
        # Same instance because cached.
        assert a is b
        clear_catalogue_cache()
        c = render_operator_card("correlation")
        # Distinct instance after cache clear (same content but new render).
        assert c is not a
        assert c.model_dump() == a.model_dump()


# ============================================================================
# MISSING YAML BLOCK — graceful refusal
# ============================================================================


class TestMissingCardBlock:
    def test_render_raises_for_unknown_operator(self) -> None:
        with pytest.raises(KeyError):
            render_operator_card("definitely_not_a_registered_operator")

    def test_render_raises_for_missing_card_block(self, tmp_path: Path) -> None:
        from shared.workflow.registry import OperatorSpec
        from shared.workflow.operator_catalogue import (
            OperatorCardError,
            render_operator_card,
        )

        # Build a synthetic config.yaml with NO `card:` block.
        bad_config = tmp_path / "no_card.yaml"
        bad_config.write_text(
            "operator:\n  name: synthetic\n  version: '1.0.0'\n",
            encoding="utf-8",
        )

        # Build a synthetic spec pointing at it (re-using an existing
        # operator's slots/output for the fixture).
        real_spec = OPERATOR_REGISTRY["correlation"]
        synth_spec = OperatorSpec(
            operator_name="synthetic_no_card",
            callable=real_spec.callable,
            params_class=real_spec.params_class,
            config_path=bad_config,
            input_slots=real_spec.input_slots,
            output=real_spec.output,
        )

        clear_catalogue_cache()
        with pytest.raises(OperatorCardError, match="missing the `card:` block"):
            render_operator_card("synthetic_no_card", spec=synth_spec)


# ============================================================================
# EXAMPLE SHAPES — at least one per operator
# ============================================================================


class TestExampleShapes:
    @pytest.mark.parametrize("operator_name", sorted(OPERATOR_REGISTRY.keys()))
    def test_at_least_one_example_shape(
        self, operator_name: str, catalogue: Dict[str, OperatorCard],
    ) -> None:
        card = catalogue[operator_name]
        assert card.example_shapes, (
            f"{operator_name}: card has no example_shapes entries.  "
            "Every operator needs at least one worked example for the "
            "Composer to anchor on."
        )

    @pytest.mark.parametrize("operator_name", sorted(OPERATOR_REGISTRY.keys()))
    def test_example_shapes_are_well_formed(
        self, operator_name: str, catalogue: Dict[str, OperatorCard],
    ) -> None:
        card = catalogue[operator_name]
        for example in card.example_shapes:
            assert isinstance(example, ExampleShape)
            assert example.description.strip()
            assert example.shape.strip()

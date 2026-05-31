"""tests/orchestrator/test_l1_decomposition.py — PR-5 acceptance suite.

Covers the L1 router decomposition extension:

  - ``IntentTag`` closed family (9 values per the plan).
  - ``EconomicQuantity`` frozen Pydantic contract.
  - ``RouteDecision`` extended with ``intent_tag`` + ``decomposition``;
    backward-compatible defaults preserved.
  - ``_normalise_route_decision`` extension: CLARIFY clears
    decomposition; out-of-domain entries dropped with adjustment
    notes; empty decomposition on non-CLARIFY surfaces as a soft
    adjustment.
  - The supervisor prompt embeds the decomposition rules + 9-intent
    few-shots so the LLM has the priors it needs (structural prompt
    audit; the LLM behaviour itself is verified empirically by the
    eval suite in PR-10).

All tests are fully offline.  No live LLM call.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.prompts import SUPERVISOR_SYSTEM_PROMPT
from orchestrator.supervisor import _normalise_route_decision


# ============================================================================
# CLOSED-FAMILY DISCIPLINE (P8)
# ============================================================================


class TestIntentTagClosedFamily:
    def test_intent_tag_size_pinned(self) -> None:
        # Per plan tmp/orchestration.md §PR-5: nine tags.  Extension is
        # an ADR change and bumps this assertion + the supervisor prompt
        # in lock-step.
        assert len(IntentTag) == 9

    def test_intent_tag_exact_membership(self) -> None:
        expected = {
            "lookup", "relationship", "regression", "cointegration",
            "transform", "event_regime", "scan", "panel", "basis",
        }
        assert {t.value for t in IntentTag} == expected

    def test_intent_tag_is_str_enum(self) -> None:
        # Pydantic coerces strings to enum members at construction —
        # useful for the LLM's structured-output JSON.
        assert IntentTag("lookup") == IntentTag.LOOKUP


# ============================================================================
# ECONOMIC QUANTITY
# ============================================================================


class TestEconomicQuantity:
    def test_constructs_with_required_fields(self) -> None:
        q = EconomicQuantity(
            name="us_2s10s",
            nl_description="UST curve spread, 2Y minus 10Y",
            domain_hint=Domain.SOVEREIGN_BONDS,
        )
        assert q.name == "us_2s10s"
        assert q.domain_hint == Domain.SOVEREIGN_BONDS

    def test_is_frozen(self) -> None:
        q = EconomicQuantity(
            name="x", nl_description="x", domain_hint=Domain.OIS,
        )
        with pytest.raises(ValidationError):
            q.name = "mutated"  # type: ignore[misc]

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValidationError):
            EconomicQuantity(
                name="", nl_description="x", domain_hint=Domain.OIS,
            )

    def test_rejects_empty_nl_description(self) -> None:
        with pytest.raises(ValidationError):
            EconomicQuantity(
                name="x", nl_description="", domain_hint=Domain.OIS,
            )

    def test_rejects_unknown_domain(self) -> None:
        with pytest.raises(ValidationError):
            EconomicQuantity(
                name="x", nl_description="x", domain_hint="fx",  # not in Domain
            )

    def test_rejects_extra_fields(self) -> None:
        # extra="forbid" — silent typos in LLM JSON surface as
        # validation errors, not as silent drops.
        with pytest.raises(ValidationError):
            EconomicQuantity(
                name="x", nl_description="x",
                domain_hint=Domain.OIS, unknown_field=42,
            )


# ============================================================================
# ROUTE DECISION — EXTENDED CONTRACT
# ============================================================================


class TestRouteDecisionExtended:
    def test_accepts_new_fields(self) -> None:
        rd = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS],
            rationale="single lookup",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="us_10y_yield",
                    nl_description="UST 10Y benchmark yield level",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )
        assert rd.intent_tag == IntentTag.LOOKUP
        assert len(rd.decomposition) == 1

    def test_backward_compat_defaults(self) -> None:
        rd = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.OIS],
            rationale="single lookup",
        )
        assert rd.intent_tag is None
        assert rd.decomposition == []

    def test_clarify_with_null_intent_tag_accepted(self) -> None:
        rd = RouteDecision(
            action=RouteAction.CLARIFY,
            domains=[],
            rationale="ambiguous",
            clarification_question="Sovereign or OIS?",
        )
        assert rd.intent_tag is None
        assert rd.decomposition == []


# ============================================================================
# NORMALISER — DECOMPOSITION HANDLING (PR-5 extension)
# ============================================================================


class TestNormaliserClarifyClearsDecomposition:
    def test_clarify_with_decomposition_cleared(self) -> None:
        raw = RouteDecision(
            action=RouteAction.CLARIFY,
            domains=[],
            rationale="ambiguous",
            clarification_question="Which one?",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.OIS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        assert out.action == RouteAction.CLARIFY
        assert out.decomposition == []
        assert out.intent_tag is None
        # Both clears noted as adjustments.
        assert any("clarify with decomposition" in a for a in out.adjustments)
        assert any("clarify with intent_tag" in a for a in out.adjustments)


class TestNormaliserPreservesDecompositionEvidence:
    """PR-5A corrective per Codex finding #2: the normaliser MUST NOT
    silently drop decomposition entries whose domain_hint isn't in
    routing domains.  Doing so destroys the gold signal for the
    'L1 dropped a domain' under-scoping failure mode that Boundary B
    (PR-8) is built to catch.  Instead, KEEP every entry and append
    a structured adjustment naming the mismatch."""

    def test_out_of_domain_entry_kept_with_adjustment(self) -> None:
        raw = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS],
            rationale="lookup",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="us_10y_yield", nl_description="UST 10Y",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
                # Leaked entry — domain_hint not in active domains.
                EconomicQuantity(
                    name="ois_5y_rate", nl_description="SOFR 5Y rate",
                    domain_hint=Domain.OIS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        # BOTH entries kept — the leaked one is evidence for
        # Boundary B, not a typo to delete.
        names = {q.name for q in out.decomposition}
        assert names == {"us_10y_yield", "ois_5y_rate"}
        # Adjustment note naming the offending entry + its
        # disagreement with the routing.
        assert any(
            "decomposition implies domain 'ois'" in a
            and "ois_5y_rate" in a
            and "Possible under-scoped routing" in a
            for a in out.adjustments
        )

    def test_all_entries_out_of_domains_kept_no_empty_alarm(self) -> None:
        raw = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS],
            rationale="lookup",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="leaked_a", nl_description="leaked a",
                    domain_hint=Domain.OIS,
                ),
                EconomicQuantity(
                    name="leaked_b", nl_description="leaked b",
                    domain_hint=Domain.INFLATION_SWAPS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        # All entries preserved.
        assert len(out.decomposition) == 2
        # Two routing-vs-decomposition mismatch adjustments.
        mismatch_notes = [
            a for a in out.adjustments
            if "Possible under-scoped routing" in a
        ]
        assert len(mismatch_notes) == 2
        # The "empty decomposition" warning MUST NOT fire — the
        # decomposition isn't empty.
        assert all(
            "empty decomposition" not in a for a in out.adjustments
        )


class TestNormaliserEmptyDecompositionSoftSignal:
    def test_non_clarify_empty_decomposition_notes_adjustment(self) -> None:
        raw = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS],
            rationale="lookup",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[],
        )
        out = _normalise_route_decision(raw)
        # Action and intent preserved (no clearing).
        assert out.action == RouteAction.SINGLE_DOMAIN
        assert out.intent_tag == IntentTag.LOOKUP
        # Soft signal recorded as an adjustment.
        assert any(
            "non-clarify action with empty decomposition" in a
            for a in out.adjustments
        )

    def test_non_clarify_decomposition_present_no_empty_adjustment(self) -> None:
        raw = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS],
            rationale="lookup",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="us_10y_yield", nl_description="x",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        assert all(
            "empty decomposition" not in a for a in out.adjustments
        )


# ============================================================================
# MISSING INTENT_TAG ON NON-CLARIFY (PR-5A corrective Codex #3)
# ============================================================================


class TestNormaliserFlagsMissingIntentTagOnNonClarify:
    """The prompt + contract require intent_tag for non-clarify actions
    (L3 / PR-7 frames operator choice off it).  When the LLM omits it,
    the normaliser MUST record an adjustment so observability + PR-8
    can detect the gap.  We do NOT demote to clarify — intent_tag is
    metadata for L3, not a routing decision."""

    def test_non_clarify_without_intent_tag_records_adjustment(self) -> None:
        raw = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.OIS],
            rationale="single ois",
            # intent_tag deliberately omitted.
            decomposition=[
                EconomicQuantity(
                    name="sofr_5y_rate", nl_description="SOFR 5Y rate",
                    domain_hint=Domain.OIS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        # Action and decomposition preserved (no demotion).
        assert out.action == RouteAction.SINGLE_DOMAIN
        assert out.intent_tag is None
        assert len(out.decomposition) == 1
        # Soft signal recorded.
        assert any(
            "intent_tag=null" in a
            and "Boundary B" in a
            for a in out.adjustments
        )

    def test_non_clarify_with_intent_tag_no_intent_adjustment(self) -> None:
        raw = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.OIS],
            rationale="single ois",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="sofr_5y_rate", nl_description="SOFR 5Y rate",
                    domain_hint=Domain.OIS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        # No adjustment about missing intent_tag.
        assert all(
            "intent_tag=null" not in a for a in out.adjustments
        )


# ============================================================================
# BACKWARD COMPATIBILITY — EXISTING NORMALISER BEHAVIOUR PRESERVED
# ============================================================================


class TestNormaliserBackwardCompat:
    """The pre-PR-5 normaliser logic (dedup, action↔domain length
    reconciliation, clarify clearing of domains) must still work."""

    def test_duplicate_domains_deduped(self) -> None:
        raw = RouteDecision(
            action=RouteAction.MULTI_DOMAIN,
            domains=[Domain.OIS, Domain.OIS, Domain.SOVEREIGN_BONDS],
            rationale="multi",
            intent_tag=IntentTag.RELATIONSHIP,
            decomposition=[
                EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.OIS,
                ),
                EconomicQuantity(
                    name="y", nl_description="y",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        assert out.domains == [Domain.OIS, Domain.SOVEREIGN_BONDS]
        assert any("deduplicated domains" in a for a in out.adjustments)

    def test_single_domain_with_two_domains_keeps_first(self) -> None:
        raw = RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.OIS, Domain.SOVEREIGN_BONDS],
            rationale="single",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.OIS,
                ),
                EconomicQuantity(
                    name="y", nl_description="y",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        assert out.domains == [Domain.OIS]
        # PR-5A corrective: the Y entry whose domain_hint (SOVEREIGN_BONDS)
        # was dropped during action↔domain reconciliation is now KEPT
        # in the decomposition; Boundary B (PR-8) reads the
        # routing-vs-decomposition mismatch as gold evidence that the
        # supervisor may have over-narrowed the action.  Both names
        # survive.
        assert {q.name for q in out.decomposition} == {"x", "y"}
        # And an adjustment surfaces the mismatch for downstream
        # inspection.
        assert any(
            "decomposition implies domain 'sovereign_bonds'" in a
            and "Possible under-scoped routing" in a
            for a in out.adjustments
        )

    def test_clarify_clears_domains_and_decomposition_and_intent(self) -> None:
        raw = RouteDecision(
            action=RouteAction.CLARIFY,
            domains=[Domain.OIS],
            rationale="amb",
            clarification_question="OIS or sovereign?",
            intent_tag=IntentTag.LOOKUP,
            decomposition=[
                EconomicQuantity(
                    name="x", nl_description="x",
                    domain_hint=Domain.OIS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        assert out.domains == []
        assert out.decomposition == []
        assert out.intent_tag is None


# ============================================================================
# CANONICAL / COMPOSITE / CLARIFY FIXTURE TESTS (PR-5A corrective Codex #4)
# ============================================================================
#
# Plan tmp/orchestration.md §PR-5 calls for tests of the canonical
# query, composite-noun query, and clarify path with actual
# RouteDecision fixtures.  These tests construct the expected shape
# the LLM should produce (per the prompt few-shots) and verify it
# survives ``_normalise_route_decision`` cleanly — i.e. no spurious
# adjustments fire on a contract-correct decision.


class TestCanonicalQueryFixture:
    """The plan's canonical PoC query.  Verifies the contract shape
    survives normalisation: cross-domain action, two-leg
    decomposition, intent=relationship, no adjustments."""

    def _fixture(self) -> RouteDecision:
        return RouteDecision(
            action=RouteAction.MULTI_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS, Domain.INFLATION_INDEXED_BONDS],
            rationale="pair-stats over a 5y window across two named quantities",
            intent_tag=IntentTag.RELATIONSHIP,
            decomposition=[
                EconomicQuantity(
                    name="us_2s10s",
                    nl_description="UST curve spread, 2Y minus 10Y",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
                EconomicQuantity(
                    name="us_5y_breakeven",
                    nl_description="USD breakeven at 5Y tenor from TIPS",
                    domain_hint=Domain.INFLATION_INDEXED_BONDS,
                ),
            ],
        )

    def test_canonical_passes_normaliser_unchanged(self) -> None:
        raw = self._fixture()
        out = _normalise_route_decision(raw)
        assert out.action == RouteAction.MULTI_DOMAIN
        assert out.domains == raw.domains
        assert out.intent_tag == IntentTag.RELATIONSHIP
        assert [q.name for q in out.decomposition] == [
            "us_2s10s", "us_5y_breakeven",
        ]
        # No adjustments — contract-correct decision.
        assert out.adjustments == []

    def test_canonical_decomposition_pairs_with_domains(self) -> None:
        # Every decomposition entry's domain_hint must be in domains.
        raw = self._fixture()
        domain_set = set(raw.domains)
        for q in raw.decomposition:
            assert q.domain_hint in domain_set


class TestCompositeNounFixture:
    """The plan's composite-noun example: '5y5y real yield' decomposes
    into forward(nominal sovereign, breakeven from linkers).  Two
    legs, each in its own domain, intent=transform."""

    def _fixture(self) -> RouteDecision:
        return RouteDecision(
            action=RouteAction.MULTI_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS, Domain.INFLATION_INDEXED_BONDS],
            rationale=(
                "composite noun decomposed into forward nominal + "
                "forward breakeven legs"
            ),
            intent_tag=IntentTag.TRANSFORM,
            decomposition=[
                EconomicQuantity(
                    name="us_5y5y_nominal_forward",
                    nl_description="5y-forward 5y nominal UST yield",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
                EconomicQuantity(
                    name="us_5y5y_breakeven_forward",
                    nl_description=(
                        "5y-forward 5y breakeven inflation from linkers"
                    ),
                    domain_hint=Domain.INFLATION_INDEXED_BONDS,
                ),
            ],
        )

    def test_composite_noun_passes_normaliser_unchanged(self) -> None:
        raw = self._fixture()
        out = _normalise_route_decision(raw)
        assert out.action == RouteAction.MULTI_DOMAIN
        assert {q.name for q in out.decomposition} == {
            "us_5y5y_nominal_forward",
            "us_5y5y_breakeven_forward",
        }
        assert out.intent_tag == IntentTag.TRANSFORM
        assert out.adjustments == []

    def test_composite_noun_does_not_collapse_to_one_entry(self) -> None:
        # Plan §PR-5 explicitly bans collapsing the composite to one
        # entry — both constituent legs must be present.
        raw = self._fixture()
        assert len(raw.decomposition) == 2


class TestClarifyPathFixture:
    """The plan's clarify path: ambiguous query → action=clarify,
    null intent_tag, empty decomposition, populated
    clarification_question."""

    def _fixture(self) -> RouteDecision:
        return RouteDecision(
            action=RouteAction.CLARIFY,
            domains=[],
            rationale="no tenor or curve identifier given",
            clarification_question="Sovereign 10Y or SOFR 10Y?",
            intent_tag=None,
            decomposition=[],
        )

    def test_clarify_passes_normaliser_unchanged(self) -> None:
        raw = self._fixture()
        out = _normalise_route_decision(raw)
        assert out.action == RouteAction.CLARIFY
        assert out.domains == []
        assert out.intent_tag is None
        assert out.decomposition == []
        assert out.clarification_question == "Sovereign 10Y or SOFR 10Y?"
        # Contract-correct clarify: no adjustments.
        assert out.adjustments == []


# ============================================================================
# DECOMPOSITION-SHAPE-RULE FIXTURES (PR-5A corrective Codex #1)
# ============================================================================


class TestZScoreDecomposesToInput:
    """Plan + prompt rule: 'z-score of SOFR 5Y' decomposes to the
    INPUT rate, not the already-standardised output.  L3 wires
    rolling_zscore."""

    def _fixture(self) -> RouteDecision:
        return RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.OIS],
            rationale="single-series transform (rolling z-score) on SOFR 5Y",
            intent_tag=IntentTag.TRANSFORM,
            decomposition=[
                EconomicQuantity(
                    name="sofr_5y_rate",
                    nl_description="SOFR OIS 5Y rate level",
                    domain_hint=Domain.OIS,
                ),
            ],
        )

    def test_zscore_decomposition_is_input_quantity(self) -> None:
        raw = self._fixture()
        out = _normalise_route_decision(raw)
        # The leaf is the input RATE — NOT a z-score-named entry.
        names = [q.name for q in out.decomposition]
        assert "sofr_5y_rate" in names
        assert not any("zscore" in n for n in names), (
            "decomposition must NOT name the post-operator output "
            "(z-score); per plan, decompose to the INPUT rate and "
            "let L3 apply rolling_zscore."
        )
        assert out.adjustments == []


class TestCointegrationDecomposesToTwoLegs:
    """Plan + prompt rule: cointegration questions decompose into the
    TWO input series the operator needs.  Never one precomputed spread."""

    def _fixture(self) -> RouteDecision:
        return RouteDecision(
            action=RouteAction.SINGLE_DOMAIN,
            domains=[Domain.SOVEREIGN_BONDS],
            rationale="Engle-Granger cointegration on the 5Y / 30Y pair",
            intent_tag=IntentTag.COINTEGRATION,
            decomposition=[
                EconomicQuantity(
                    name="ust_5y_yield",
                    nl_description="UST 5Y benchmark yield level",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
                EconomicQuantity(
                    name="ust_30y_yield",
                    nl_description="UST 30Y benchmark yield level",
                    domain_hint=Domain.SOVEREIGN_BONDS,
                ),
            ],
        )

    def test_cointegration_decomposition_has_two_input_legs(self) -> None:
        raw = self._fixture()
        out = _normalise_route_decision(raw)
        assert len(out.decomposition) == 2
        names = [q.name for q in out.decomposition]
        assert names == ["ust_5y_yield", "ust_30y_yield"]
        # And NOT a single precomputed spread name.
        assert not any("spread" in n for n in names)
        assert not any("5s30s" in n for n in names)
        assert out.adjustments == []


# ============================================================================
# SUPERVISOR PROMPT — STRUCTURAL AUDIT
# ============================================================================


class TestSupervisorPromptCarriesPR5Guidance:
    """The LLM behaviour is verified empirically in PR-10's eval set.
    These tests only assert the PROMPT contains the structural
    priors required for PR-5 — so a future edit cannot silently
    delete the guidance."""

    def test_prompt_mentions_decomposition(self) -> None:
        assert "DECOMPOSITION" in SUPERVISOR_SYSTEM_PROMPT
        assert "decomposition" in SUPERVISOR_SYSTEM_PROMPT

    def test_prompt_mentions_intent_tag(self) -> None:
        assert "intent_tag" in SUPERVISOR_SYSTEM_PROMPT

    @pytest.mark.parametrize("tag_value", [
        "lookup", "relationship", "regression", "cointegration",
        "transform", "event_regime", "scan", "panel", "basis",
    ])
    def test_prompt_names_every_intent_tag_value(
        self, tag_value: str,
    ) -> None:
        # The prompt enumerates the closed-family values so the LLM's
        # structured output stays disciplined.
        assert tag_value in SUPERVISOR_SYSTEM_PROMPT, (
            f"SUPERVISOR_SYSTEM_PROMPT does not mention IntentTag "
            f"value {tag_value!r}; the LLM may emit an invalid tag "
            "and trip Pydantic enum coercion."
        )

    def test_prompt_mentions_economic_quantity_fields(self) -> None:
        for field in ("name", "nl_description", "domain_hint"):
            assert field in SUPERVISOR_SYSTEM_PROMPT, (
                f"SUPERVISOR_SYSTEM_PROMPT does not mention "
                f"EconomicQuantity field {field!r}."
            )

    def test_prompt_mentions_composite_noun_pattern(self) -> None:
        # Plan §PR-5 example: "5y5y real yield" → decomposed into
        # forward(nominal_sovereign_yield, breakeven_from_linkers).
        # The prompt MUST teach the LLM to decompose composite nouns
        # rather than collapse them to one entry.
        assert "COMPOSITE NOUN" in SUPERVISOR_SYSTEM_PROMPT.upper()
        # Specifically the "5y5y real yield" few-shot.
        assert "5y5y" in SUPERVISOR_SYSTEM_PROMPT

    def test_prompt_clarifies_single_domain_decomposition_rule(self) -> None:
        # Acceptance criterion 2: single-domain queries STILL produce
        # decomposition (one entry).  The prompt must say this.
        msg = SUPERVISOR_SYSTEM_PROMPT.upper()
        assert "SINGLE-DOMAIN" in msg or "SINGLE DOMAIN" in msg
        assert "STILL PRODUCE DECOMPOSITION" in SUPERVISOR_SYSTEM_PROMPT.upper()

    def test_prompt_carries_decomposition_shape_rule(self) -> None:
        # PR-5A corrective per Codex finding #1: the prompt must teach
        # L1 to decompose to INPUT quantities (DAG leaves), not
        # already-computed outputs.  This is the rule that prevents
        # 'z-score' / 'rolling correlation' / 'beta' from leaking into
        # decomposition entries.
        assert "DECOMPOSITION SHAPE RULE" in SUPERVISOR_SYSTEM_PROMPT
        # The rule must explicitly name the two failure modes the
        # downstream verification step needs to detect.
        assert "z-score" in SUPERVISOR_SYSTEM_PROMPT.lower()
        assert "rolling_zscore" in SUPERVISOR_SYSTEM_PROMPT
        assert "rolling correlation" in SUPERVISOR_SYSTEM_PROMPT.lower()


# ============================================================================
# PER-INTENT EVAL COVERAGE
# ============================================================================


class TestPromptHasFewShotPerIntent:
    """Plan §PR-5 'Tests' line: 'Eval set covering each IntentTag: at
    least one prompt per tag.'  The eval set lives in PR-10; PR-5's
    test asserts the supervisor PROMPT has at least one few-shot per
    tag — that's the LLM's priors per the closed family."""

    @pytest.mark.parametrize("tag", [t for t in IntentTag])
    def test_few_shot_present_per_intent(self, tag: IntentTag) -> None:
        # Each tag's value appears as a JSON string in at least one
        # few-shot example block in the prompt.
        marker = f'"intent_tag": "{tag.value}"'
        assert marker in SUPERVISOR_SYSTEM_PROMPT, (
            f"SUPERVISOR_SYSTEM_PROMPT has no few-shot example with "
            f"intent_tag={tag.value!r}.  Without an example, the LLM "
            "has weaker grounding for that tag."
        )

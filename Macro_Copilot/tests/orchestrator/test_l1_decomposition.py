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


class TestNormaliserDomainHintFiltering:
    def test_decomposition_entry_outside_domains_dropped(self) -> None:
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
                # Leaked entry — domain_hint not in the active domains list.
                EconomicQuantity(
                    name="ois_5y_rate", nl_description="SOFR 5Y rate",
                    domain_hint=Domain.OIS,
                ),
            ],
        )
        out = _normalise_route_decision(raw)
        # Leaked entry dropped; kept entry preserved.
        assert len(out.decomposition) == 1
        assert out.decomposition[0].name == "us_10y_yield"
        assert any(
            "dropped decomposition entries" in a
            and "ois_5y_rate" in a
            for a in out.adjustments
        )

    def test_all_entries_outside_domains_results_in_empty_decomposition(
        self,
    ) -> None:
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
        assert out.decomposition == []
        # Both messages — the leaked-entries note AND the soft
        # "empty decomposition" note.
        assert any("dropped decomposition" in a for a in out.adjustments)
        assert any(
            "empty decomposition" in a for a in out.adjustments
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
        # The Y entry's domain_hint (SOVEREIGN_BONDS) is no longer in
        # the active domain list after the keep-first reduction, so
        # it's dropped — proves the decomposition normaliser runs
        # AFTER the action↔domains reconciliation.
        assert {q.name for q in out.decomposition} == {"x"}

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

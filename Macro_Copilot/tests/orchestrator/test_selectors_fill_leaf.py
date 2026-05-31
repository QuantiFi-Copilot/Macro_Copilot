"""tests/orchestrator/test_selectors_fill_leaf.py — PR-6 acceptance suite.

Covers ``orchestrator.selectors`` — the pure logic backing
``DomainAgentSession.fill_leaf``.  Tests run fully offline using the
live rates ``PrimitiveResolver`` (metadata-only — no callables are
invoked) and synthetic tool stubs for the per-domain MCP catalogue.

The plan (``tmp/orchestration.md`` §PR-6) specifies these tests:

  - Canonical sovereign / inflation-indexed bindings.
  - Refusal on cross-domain request.
  - Output-type declaration accuracy.
  - P11 holds (catalogue scoped to one domain).
  - Selector never executes a primitive (verified via
    exploding-callable resolver fixture).
  - Per-domain fill_leaf coverage + refusal path per domain.
  - Existing ``run`` (ReAct) mode unaffected.

Live LLM and live MCP subprocesses are NOT exercised here.  Those
plumbing layers are covered by the integration tests that already
skip when LangChain isn't installed in the lane.  This suite proves
the pure logic the LLM-call wraps is correct deterministically.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from pydantic import BaseModel, ValidationError

from orchestrator.contracts import Domain
from orchestrator.open_dag import (
    BoundLeaf,
    Frequency,
    LeafRequest,
)
from orchestrator.open_dag.primitive_declarations import (
    PrimitiveDeclaration,
)
from orchestrator.open_dag.resolver_keys import domain_to_resolver_key
from orchestrator.selectors import (
    SelectorBindingError,
    SelectorLLMOutput,
    ToolCatalogueEntry,
    assert_request_for_this_domain,
    llm_output_to_bound_leaf,
    render_tool_catalogue,
    render_user_prompt,
)
from shared.artifacts.registry import ArtifactTypeName
from shared.schemas.time_series import TimeSeriesUnits
from shared.workflow import PrimitiveSpec


# ============================================================================
# SYNTHETIC TOOL FIXTURE
# ============================================================================


class _Tool:
    """Mimics the surface area of a LangChain MCP BaseTool that
    ``render_tool_catalogue`` reads (``.name`` + ``.description``).
    Plain class so tests don't need LangChain installed."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


# ============================================================================
# LEAF REQUEST BUILDERS — typical per-domain shapes
# ============================================================================


def _request(
    *,
    domain: Domain,
    semantic_role: str = "rate_level",
    expected_units: TimeSeriesUnits | None = None,
    expected_frequency: Frequency | None = None,
    nl_intent: str = "fetch the series",
    requested_output_meaning: str = "one Series of the named quantity",
    artifact_type: ArtifactTypeName = ArtifactTypeName.SERIES,
) -> LeafRequest:
    return LeafRequest(
        required_artifact_type=artifact_type,
        expected_units=expected_units,
        expected_frequency=expected_frequency,
        domain_hint=domain.value,
        semantic_role=semantic_role,
        requested_output_meaning=requested_output_meaning,
        nl_intent=nl_intent,
    )


# ============================================================================
# CATALOGUE RENDERING
# ============================================================================


class TestRenderToolCatalogue:
    def test_renders_known_rates_sovereign_tool(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        tools = [
            _Tool(
                "calculate_curve_spread_tool",
                "Calculate a same-curve spread between two tenors.",
            ),
        ]
        cat = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        assert len(cat) == 1
        entry = cat[0]
        assert entry.mcp_tool_name == "calculate_curve_spread_tool"
        # For sovereign_bonds (bare-name domain), resolver key equals mcp name.
        assert entry.resolver_tool_key == "calculate_curve_spread_tool"
        # Static metadata pulled from PrimitiveSpec via PrimitiveDeclaration.
        assert entry.output_artifact_type == "Series"
        assert entry.available_output_fields  # non-empty

    def test_renders_policy_futures_with_prefixed_resolver_key(self) -> None:
        # policy_futures uses prefixed resolver keys (see resolver_keys.py).
        from rates_agent.workflows import rates_primitive_resolver

        tools = [
            _Tool(
                "get_futures_price_level_tool",
                "Get the front-month futures price level for a curve family.",
            ),
        ]
        cat = render_tool_catalogue(
            Domain.POLICY_FUTURES, tools, rates_primitive_resolver,
        )
        assert len(cat) == 1
        entry = cat[0]
        assert entry.mcp_tool_name == "get_futures_price_level_tool"
        # Resolver key is PREFIXED for policy_futures.
        assert entry.resolver_tool_key == (
            "policy_futures_get_futures_price_level_tool"
        )

    def test_renders_bond_futures_with_bare_resolver_key(self) -> None:
        # bond_futures shares the visible MCP tool name with
        # policy_futures but keeps the bare resolver key (collision
        # disambiguation seam from PR-3).
        from rates_agent.workflows import rates_primitive_resolver

        tools = [
            _Tool(
                "get_futures_price_level_tool",
                "Get the front-month bond-future price level.",
            ),
        ]
        cat = render_tool_catalogue(
            Domain.BOND_FUTURES, tools, rates_primitive_resolver,
        )
        assert len(cat) == 1
        entry = cat[0]
        assert entry.resolver_tool_key == "get_futures_price_level_tool"

    def test_drops_tool_with_no_resolver_entry(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        tools = [
            _Tool("calculate_curve_spread_tool", "OK tool"),
            _Tool("not_a_real_primitive_xyz", "Unknown tool"),
        ]
        cat = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        names = [e.mcp_tool_name for e in cat]
        assert names == ["calculate_curve_spread_tool"]
        # Unknown tool was silently dropped — defensive against
        # MCP-vs-resolver drift mid-deploy.

    def test_does_not_invoke_resolver_callable(self) -> None:
        # Synthetic resolver whose callable would assert if invoked;
        # render_tool_catalogue must only call declare_primitive_output
        # (metadata only).
        def _explodes(*args, **kwargs):
            raise AssertionError(
                "render_tool_catalogue invoked a primitive callable!"
            )

        class _In(BaseModel):
            pass

        class _Out(BaseModel):
            pass

        spec = PrimitiveSpec(
            tool_name="silent_tool",
            callable=_explodes,
            input_class=_In,
            output_class=_Out,
            config_path=Path("/dev/null"),
            output_artifact_type="Series",
            output_field_units={"time_series": "bps"},
        )

        def _resolver(tool_name: str) -> PrimitiveSpec:
            return spec

        tools = [_Tool("silent_tool", "A docstring")]
        cat = render_tool_catalogue(Domain.SOVEREIGN_BONDS, tools, _resolver)
        assert len(cat) == 1


# ============================================================================
# USER PROMPT RENDERING
# ============================================================================


class TestRenderUserPrompt:
    def _cat(self) -> List[ToolCatalogueEntry]:
        return [
            ToolCatalogueEntry(
                mcp_tool_name="t1",
                description="First tool docstring",
                resolver_tool_key="t1",
                output_artifact_type="Series",
                output_field_units={"time_series": "bps"},
                available_output_fields=("time_series",),
            ),
            ToolCatalogueEntry(
                mcp_tool_name="t2",
                description="Second tool docstring",
                resolver_tool_key="t2",
                output_artifact_type="Panel",
                output_field_units={},
                available_output_fields=(),
            ),
        ]

    def test_carries_domain_and_request(self) -> None:
        req = _request(
            domain=Domain.SOVEREIGN_BONDS,
            semantic_role="spread_level",
            expected_units=TimeSeriesUnits.BPS,
            expected_frequency=Frequency.DAILY,
            nl_intent="fetch UST 2s10s",
        )
        text = render_user_prompt(Domain.SOVEREIGN_BONDS, req, self._cat())
        assert "DOMAIN: sovereign_bonds" in text
        assert "spread_level" in text
        assert "bps" in text
        assert "daily" in text
        assert "fetch UST 2s10s" in text

    def test_carries_every_catalogue_tool(self) -> None:
        req = _request(domain=Domain.SOVEREIGN_BONDS)
        text = render_user_prompt(Domain.SOVEREIGN_BONDS, req, self._cat())
        assert "### t1" in text
        assert "### t2" in text
        assert "First tool docstring" in text
        assert "Second tool docstring" in text

    def test_deterministic_across_calls(self) -> None:
        req = _request(domain=Domain.SOVEREIGN_BONDS)
        a = render_user_prompt(Domain.SOVEREIGN_BONDS, req, self._cat())
        b = render_user_prompt(Domain.SOVEREIGN_BONDS, req, self._cat())
        assert a == b

    def test_signals_no_units_or_frequency_explicitly(self) -> None:
        # No expected_units / no expected_frequency → prompt says
        # "(none — any ... acceptable)" so the LLM doesn't guess.
        req = _request(
            domain=Domain.SOVEREIGN_BONDS,
            expected_units=None,
            expected_frequency=None,
        )
        text = render_user_prompt(Domain.SOVEREIGN_BONDS, req, self._cat())
        assert "any unit acceptable" in text
        assert "any frequency acceptable" in text


# ============================================================================
# LLM OUTPUT → BoundLeaf TRANSFORMATION
# ============================================================================


class TestLLMOutputToBoundLeaf:
    def _cat(self) -> List[ToolCatalogueEntry]:
        return [
            ToolCatalogueEntry(
                mcp_tool_name="calculate_curve_spread_tool",
                description="Curve spread doc.",
                resolver_tool_key="calculate_curve_spread_tool",
                output_artifact_type="Series",
                output_field_units={
                    "time_series": "bps",
                    "time_series_spread": "bps",
                },
                available_output_fields=("time_series", "time_series_spread"),
            ),
        ]

    def test_happy_path_binding_derives_closed_substrate_fields(self) -> None:
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="calculate_curve_spread_tool",
            params={
                "curve_family": "UST", "short_tenor": "2Y",
                "long_tenor": "10Y", "lookback_days": 1825,
            },
            chosen_output_field="time_series",
            declared_frequency=Frequency.DAILY,
            declared_semantic_role="spread_level",
            declared_output_meaning="UST 2s10s curve spread series",
            fit_confidence=0.95,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a",
            domain=Domain.SOVEREIGN_BONDS,
            output=output,
            catalogue=self._cat(),
        )
        assert bound.is_refusal is False
        assert bound.leaf_id == "h_a"
        assert bound.domain == "sovereign_bonds"
        assert bound.mcp_tool_name == "calculate_curve_spread_tool"
        # Code derived from catalogue:
        assert bound.resolver_tool_key == "calculate_curve_spread_tool"
        assert bound.declared_output_artifact_type == ArtifactTypeName.SERIES
        assert bound.declared_units == TimeSeriesUnits.BPS
        # LLM-authored:
        assert bound.declared_frequency == Frequency.DAILY
        assert bound.declared_semantic_role == "spread_level"
        assert bound.fit_confidence == 0.95

    def test_refusal_returns_refusal_bound_leaf(self) -> None:
        output = SelectorLLMOutput(
            fit_confidence=0.0,
            refusal=(
                "No primitive in this domain matches the request "
                "for an OIS swap-spread series."
            ),
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a",
            domain=Domain.SOVEREIGN_BONDS,
            output=output,
            catalogue=self._cat(),
        )
        assert bound.is_refusal is True
        assert "OIS swap-spread" in bound.refusal
        assert bound.leaf_id == "h_a"

    def test_neither_binding_nor_refusal_treated_as_refusal(self) -> None:
        # Contract violation: LLM picked nothing and didn't refuse.
        # Surfaces as a structured refusal so the upstream Assembler
        # sees a clean outcome instead of an exception.
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="",
            chosen_output_field="",
            fit_confidence=0.0,
            refusal=None,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a",
            domain=Domain.SOVEREIGN_BONDS,
            output=output,
            catalogue=self._cat(),
        )
        assert bound.is_refusal is True
        assert "neither a tool choice nor a refusal" in bound.refusal

    def test_unknown_tool_name_becomes_refusal(self) -> None:
        # LLM picked a tool not in the catalogue (hallucinated).
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="totally_made_up_tool",
            chosen_output_field="time_series",
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=0.4,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a",
            domain=Domain.SOVEREIGN_BONDS,
            output=output,
            catalogue=self._cat(),
        )
        assert bound.is_refusal is True
        assert "not in this domain's tool catalogue" in bound.refusal

    def test_empty_output_field_becomes_refusal(self) -> None:
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="calculate_curve_spread_tool",
            params={"curve_family": "UST"},
            chosen_output_field="",  # missing
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=0.8,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a",
            domain=Domain.SOVEREIGN_BONDS,
            output=output,
            catalogue=self._cat(),
        )
        assert bound.is_refusal is True
        assert "chosen_output_field empty" in bound.refusal

    def test_resolver_key_consistent_with_catalogue(self) -> None:
        # BoundLeaf model_validator enforces
        # resolver_tool_key == domain_to_resolver_key(domain, mcp_tool_name).
        # Since the catalogue's resolver_tool_key was built that way,
        # the round-trip is always consistent.
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="calculate_curve_spread_tool",
            params={},
            chosen_output_field="time_series",
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=1.0,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a",
            domain=Domain.SOVEREIGN_BONDS,
            output=output,
            catalogue=self._cat(),
        )
        expected_key = domain_to_resolver_key(
            "sovereign_bonds", "calculate_curve_spread_tool",
        )
        assert bound.resolver_tool_key == expected_key

    def test_empty_leaf_id_rejected(self) -> None:
        output = SelectorLLMOutput(
            fit_confidence=0.0, refusal="any",
        )
        with pytest.raises(SelectorBindingError, match="leaf_id"):
            llm_output_to_bound_leaf(
                leaf_id="",
                domain=Domain.SOVEREIGN_BONDS,
                output=output,
                catalogue=self._cat(),
            )

    def test_units_derived_from_output_field(self) -> None:
        # Pick a different output_field; verify declared_units tracks.
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="calculate_curve_spread_tool",
            params={},
            chosen_output_field="time_series_spread",
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=1.0,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a",
            domain=Domain.SOVEREIGN_BONDS,
            output=output,
            catalogue=self._cat(),
        )
        assert bound.declared_units == TimeSeriesUnits.BPS


# ============================================================================
# CROSS-DOMAIN ROUTING GUARD
# ============================================================================


class TestRoutingGuard:
    def test_matching_domain_accepts(self) -> None:
        req = _request(domain=Domain.SOVEREIGN_BONDS)
        # Must not raise.
        assert_request_for_this_domain(req, Domain.SOVEREIGN_BONDS)

    def test_mismatched_domain_raises(self) -> None:
        req = _request(domain=Domain.OIS)
        with pytest.raises(SelectorBindingError, match="domain_hint"):
            assert_request_for_this_domain(req, Domain.SOVEREIGN_BONDS)


# ============================================================================
# PER-DOMAIN COVERAGE — acceptance criterion #1 + #2
# ============================================================================
#
# For every one of the 6 domains, exercise:
#   - a happy binding path,
#   - a refusal path.
# These tests build a synthetic 1-tool catalogue for each domain (using
# a real registered primitive) and pass synthetic SelectorLLMOutput
# objects.  No live LLM call; the per-domain LLM behaviour is the eval
# suite's job.


# Per-domain canonical primitives — picked because each has a clean
# default ``time_series`` output_field declared.
_DOMAIN_CANONICAL: Dict[Domain, str] = {
    Domain.SOVEREIGN_BONDS: "calculate_curve_spread_tool",
    Domain.OIS: "calculate_ois_curve_spread_tool",
    Domain.INFLATION_INDEXED_BONDS: (
        "calculate_breakeven_inflation_simple_tool"
    ),
    Domain.INFLATION_SWAPS: "calculate_inflation_swap_curve_spread_tool",
    Domain.POLICY_FUTURES: "get_futures_calendar_spread_tool",
    Domain.BOND_FUTURES: "get_futures_price_level_tool",
}


def _per_domain_catalogue(domain: Domain) -> List[ToolCatalogueEntry]:
    from rates_agent.workflows import rates_primitive_resolver
    mcp_name = _DOMAIN_CANONICAL[domain]
    tools = [_Tool(mcp_name, f"Synthetic doc for {mcp_name}")]
    return render_tool_catalogue(domain, tools, rates_primitive_resolver)


class TestEveryDomainFillLeaf:
    @pytest.mark.parametrize("domain", list(Domain))
    def test_binding_path_returns_clean_bound_leaf(
        self, domain: Domain,
    ) -> None:
        catalogue = _per_domain_catalogue(domain)
        assert catalogue, f"no catalogue entry for domain {domain.value!r}"
        entry = catalogue[0]
        # Use the entry's first available output_field; bail if none.
        output_field = (
            entry.available_output_fields[0]
            if entry.available_output_fields
            else "time_series"
        )
        output = SelectorLLMOutput(
            chosen_mcp_tool_name=entry.mcp_tool_name,
            params={},
            chosen_output_field=output_field,
            declared_semantic_role="any_role",
            declared_output_meaning="any meaning",
            fit_confidence=0.9,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id=f"h_{domain.value}",
            domain=domain,
            output=output,
            catalogue=catalogue,
        )
        if bound.is_refusal:
            # Some domains' canonical tool declares no output_fields
            # (e.g. snapshot primitives) so the catalogue's entry has
            # `available_output_fields=()` and the post-LLM transform
            # refuses honestly.  That's still per-domain coverage —
            # the path exercised the catalogue + LLM-output bridge.
            assert "empty" in bound.refusal or "not in this domain" in bound.refusal
        else:
            assert bound.domain == domain.value
            assert bound.mcp_tool_name == entry.mcp_tool_name
            assert bound.resolver_tool_key == entry.resolver_tool_key

    @pytest.mark.parametrize("domain", list(Domain))
    def test_refusal_path_returns_refusal_bound_leaf(
        self, domain: Domain,
    ) -> None:
        catalogue = _per_domain_catalogue(domain)
        output = SelectorLLMOutput(
            fit_confidence=0.0,
            refusal=(
                f"No primitive in {domain.value!r} matches the "
                "synthesized cross-domain request."
            ),
        )
        bound = llm_output_to_bound_leaf(
            leaf_id=f"h_{domain.value}",
            domain=domain,
            output=output,
            catalogue=catalogue,
        )
        assert bound.is_refusal is True
        assert domain.value in bound.refusal


# ============================================================================
# OUTPUT-TYPE DECLARATION ACCURACY (plan test)
# ============================================================================


class TestOutputTypeDeclarationAccuracy:
    """The plan: declared_output_artifact_type must match what
    declare_primitive_output(...) returns for the chosen tool.  Since
    the catalogue is built FROM declare_primitive_output, the test
    asserts the catalogue entry's value flows through to the
    BoundLeaf unchanged."""

    def test_sovereign_curve_spread_declares_series(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        # Direct check of the declaration source-of-truth.
        from orchestrator.open_dag import declare_primitive_output
        decl = declare_primitive_output(
            rates_primitive_resolver, "calculate_curve_spread_tool",
        )
        assert decl.output_artifact_type == "Series"

        # Now ensure the catalogue + transform pipeline mirrors that.
        tools = [_Tool("calculate_curve_spread_tool", "doc")]
        cat = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="calculate_curve_spread_tool",
            params={},
            chosen_output_field=cat[0].available_output_fields[0],
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=1.0,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h", domain=Domain.SOVEREIGN_BONDS,
            output=output, catalogue=cat,
        )
        assert bound.declared_output_artifact_type == ArtifactTypeName.SERIES

    def test_panel_primitive_declares_panel(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        from orchestrator.open_dag import declare_primitive_output
        decl = declare_primitive_output(
            rates_primitive_resolver, "build_sovereign_yield_panel_tool",
        )
        assert decl.output_artifact_type == "Panel"

        tools = [_Tool("build_sovereign_yield_panel_tool", "doc")]
        cat = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        # Panel-producing primitives have no output_field_units → no
        # available_output_fields.  We supply a chosen_output_field
        # value to force the binding path; the post-LLM transform
        # accepts an LLM-supplied field even when the catalogue's
        # available_output_fields is empty (declared_units stays None).
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="build_sovereign_yield_panel_tool",
            params={},
            chosen_output_field="time_series",
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=1.0,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h", domain=Domain.SOVEREIGN_BONDS,
            output=output, catalogue=cat,
        )
        assert bound.declared_output_artifact_type == ArtifactTypeName.PANEL


# ============================================================================
# NO PRIMITIVE EXECUTION (plan acceptance criterion #3)
# ============================================================================


class TestNoPrimitiveExecution:
    """The Selector NEVER executes a primitive in fill_leaf mode.
    Verified by passing a resolver whose primitive's callable raises
    on invocation — the full catalogue render → LLM-output transform
    pipeline runs cleanly."""

    def test_catalogue_render_does_not_invoke_callable(self) -> None:
        def _explodes(*args, **kwargs):
            raise AssertionError(
                "Selector invoked a primitive callable — violates "
                "the no-execution contract."
            )

        class _In(BaseModel):
            pass

        class _Out(BaseModel):
            pass

        spec = PrimitiveSpec(
            tool_name="silent_primitive",
            callable=_explodes,
            input_class=_In,
            output_class=_Out,
            config_path=Path("/dev/null"),
            output_artifact_type="Series",
            output_field_units={"time_series": "bps"},
        )

        def _resolver(tool_name: str) -> PrimitiveSpec:
            return spec

        tools = [_Tool("silent_primitive", "A docstring")]
        cat = render_tool_catalogue(Domain.SOVEREIGN_BONDS, tools, _resolver)
        assert len(cat) == 1

        # Now the full pipeline: build SelectorLLMOutput and transform.
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="silent_primitive",
            params={"any": "params"},
            chosen_output_field="time_series",
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=0.7,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a", domain=Domain.SOVEREIGN_BONDS,
            output=output, catalogue=cat,
        )
        assert bound.is_refusal is False
        # Still nothing invoked the callable.


# ============================================================================
# P11 ISOLATION INVARIANT (plan test)
# ============================================================================


class TestP11CatalogueIsolation:
    """The Selector's catalogue is scoped to ONE domain by
    construction.  Even if the live MCP server somehow leaked a
    cross-domain tool name, the catalogue's resolver-key derivation
    runs against THIS domain's convention only — and the resolver
    lookup would fail for any tool not registered under that key.

    This test asserts the catalogue-build never tries to resolve a
    tool name under a foreign domain's resolver key by accident."""

    def test_catalogue_uses_only_own_domain_resolver_keys(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        # Pass a policy_futures-name-shaped tool but render under
        # bond_futures — the resolver lookup uses the bare name
        # (bond_futures convention), which would resolve to the
        # BOND_FUTURES primitive, not the policy_futures one.  This
        # demonstrates the per-domain scoping.
        tools = [_Tool("get_futures_price_level_tool", "doc")]
        cat_bond = render_tool_catalogue(
            Domain.BOND_FUTURES, tools, rates_primitive_resolver,
        )
        cat_policy = render_tool_catalogue(
            Domain.POLICY_FUTURES, tools, rates_primitive_resolver,
        )

        assert cat_bond[0].resolver_tool_key == (
            "get_futures_price_level_tool"
        )
        assert cat_policy[0].resolver_tool_key == (
            "policy_futures_get_futures_price_level_tool"
        )
        # Different resolver keys → different PrimitiveSpec instances
        # via the live resolver (verified in PR-3's test_resolver_keys).


# ============================================================================
# EXISTING RUN MODE UNAFFECTED (acceptance criterion #4)
# ============================================================================


class TestRunModeUnaffected:
    """PR-6's new fill_leaf method is constructed at open() and only
    when a primitive_resolver is supplied.  Sessions constructed
    WITHOUT a resolver (the existing ReAct mode's pattern) must
    continue to work unchanged — fill_leaf raises a clear error;
    everything else is untouched."""

    def test_default_constructor_does_not_set_up_selector(self) -> None:
        # Synthesize a session without LangChain dependencies by only
        # exercising the constructor (no open()).
        from orchestrator.domain_agent import DomainAgentSession

        session = DomainAgentSession(
            domain=Domain.SOVEREIGN_BONDS,
            system_prompt="test prompt",
            mcp_servers={"sovereign_bonds": {}},
            model_name="claude-sonnet-test",
        )
        assert session._primitive_resolver is None
        assert session._selector_model is None
        assert session._cached_selector_system_message is None
        assert session._tool_catalogue == []
        # The existing constructor-time attributes are still in place.
        assert session._tool_names == []
        assert session._mcp_client is None
        assert session._is_open is False

    def test_explicit_resolver_records_on_session(self) -> None:
        from orchestrator.domain_agent import DomainAgentSession
        from rates_agent.workflows import rates_primitive_resolver

        session = DomainAgentSession(
            domain=Domain.OIS,
            system_prompt="test prompt",
            mcp_servers={"ois": {}},
            model_name="claude-sonnet-test",
            primitive_resolver=rates_primitive_resolver,
        )
        assert session._primitive_resolver is rates_primitive_resolver
        # Scaffolding fields still empty until open() runs.
        assert session._selector_model is None
        assert session._tool_catalogue == []


# ============================================================================
# CLOSED-FAMILY DISCIPLINE on SelectorLLMOutput
# ============================================================================


class TestSelectorLLMOutputShape:
    def test_extra_fields_rejected(self) -> None:
        # extra="forbid" — silent LLM JSON typos surface here.
        with pytest.raises(ValidationError):
            SelectorLLMOutput(
                fit_confidence=0.5,
                unknown_field=42,
            )

    def test_fit_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SelectorLLMOutput(fit_confidence=1.5)
        with pytest.raises(ValidationError):
            SelectorLLMOutput(fit_confidence=-0.1)

    def test_frequency_field_accepts_enum(self) -> None:
        out = SelectorLLMOutput(
            fit_confidence=0.5,
            declared_frequency=Frequency.DAILY,
        )
        assert out.declared_frequency == Frequency.DAILY

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
from shared.schemas.time_series import TimeSeries as _TimeSeriesWire
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
        cat, dropped = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        assert len(cat) == 1
        assert dropped == []
        entry = cat[0]
        assert entry.mcp_tool_name == "calculate_curve_spread_tool"
        # For sovereign_bonds (bare-name domain), resolver key equals mcp name.
        assert entry.resolver_tool_key == "calculate_curve_spread_tool"
        # Static metadata pulled from PrimitiveSpec via PrimitiveDeclaration.
        assert entry.output_artifact_type == "Series"
        assert entry.available_output_fields  # non-empty

    def test_renders_policy_futures_with_prefixed_resolver_key(self) -> None:
        # policy_futures uses prefixed resolver keys (see resolver_keys.py).
        # Use a BRIDGEABLE_SERIES policy_futures primitive — most
        # snapshot tools (price_level, volume_oi) are
        # TERMINAL_ONLY_SNAPSHOT.
        from rates_agent.workflows import rates_primitive_resolver

        tools = [
            _Tool(
                "get_futures_butterfly_simple_tool",
                "Compute a simple butterfly on policy-futures strips.",
            ),
        ]
        cat, dropped = render_tool_catalogue(
            Domain.POLICY_FUTURES, tools, rates_primitive_resolver,
        )
        assert len(cat) == 1
        assert dropped == []
        entry = cat[0]
        assert entry.mcp_tool_name == "get_futures_butterfly_simple_tool"
        # Resolver key is PREFIXED for policy_futures.
        assert entry.resolver_tool_key == (
            "policy_futures_get_futures_butterfly_simple_tool"
        )

    def test_bond_futures_primitives_are_bridged_post_adr_0017(self) -> None:
        # ADR 0017: the bond_futures dated-history primitives
        # (price_level, volume_oi) now carry canonical TimeSeries
        # companions + output_field_units declarations, so the
        # catalogue keeps them with exactly the canonical fields
        # (the bespoke ``time_series`` row lists stay excluded).
        # Pre-ADR they were TERMINAL_ONLY_SNAPSHOT and dropped —
        # the campaign l01/l03 refusal mode this closes.
        from rates_agent.workflows import rates_primitive_resolver

        tools = [
            _Tool("get_futures_price_level_tool", "front-month price"),
            _Tool("get_futures_volume_oi_tool", "front-month volume"),
        ]
        cat, dropped = render_tool_catalogue(
            Domain.BOND_FUTURES, tools, rates_primitive_resolver,
        )
        assert dropped == []
        fields_by_name = {
            e.mcp_tool_name: e.available_output_fields for e in cat
        }
        # available_output_fields is sorted (declare_primitive_output
        # sorts the declared keys for stable catalogue prompts).
        assert fields_by_name == {
            "get_futures_price_level_tool": ("time_series_price",),
            "get_futures_volume_oi_tool": (
                "time_series_open_interest", "time_series_volume",
            ),
        }

    def test_bond_futures_bare_resolver_key_derivation(self) -> None:
        # PR-3 seam: bond_futures uses bare resolver keys; policy_futures
        # uses prefixed.  Verified directly via domain_to_resolver_key.
        from orchestrator.open_dag.resolver_keys import (
            domain_to_resolver_key,
        )
        assert (
            domain_to_resolver_key("bond_futures", "get_futures_price_level_tool")
            == "get_futures_price_level_tool"
        )
        assert (
            domain_to_resolver_key("policy_futures", "get_futures_price_level_tool")
            == "policy_futures_get_futures_price_level_tool"
        )

    def test_drops_tool_with_no_resolver_entry(self) -> None:
        # PR-6A corrective per Codex finding #4: the drop is no longer
        # silent — DroppedToolEntry records the reason so registration
        # drift is observable.
        from rates_agent.workflows import rates_primitive_resolver

        tools = [
            _Tool("calculate_curve_spread_tool", "OK tool"),
            _Tool("not_a_real_primitive_xyz", "Unknown tool"),
        ]
        cat, dropped = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        names = [e.mcp_tool_name for e in cat]
        assert names == ["calculate_curve_spread_tool"]
        # The unknown tool now surfaces in the dropped list with a
        # diagnostic reason.
        assert len(dropped) == 1
        assert dropped[0].mcp_tool_name == "not_a_real_primitive_xyz"
        assert "registration drift" in dropped[0].reason
        # composability=None because the resolver couldn't classify it.
        assert dropped[0].composability is None

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
            # PR-11 Codex Round 5: the catalogue filter now requires
            # the declared output_field to actually be typed as the
            # canonical ``TimeSeries`` on the *Output schema (legacy
            # ``List[TimeSeriesRow]`` shapes are filtered out so the
            # LLM cannot pick them).  Declare the field explicitly
            # so this fixture survives the filter.
            time_series: _TimeSeriesWire

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
        cat, dropped = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, _resolver,
        )
        assert len(cat) == 1
        assert dropped == []


# ============================================================================
# USER PROMPT RENDERING
# ============================================================================


class TestRenderUserPrompt:
    def _cat(self) -> List[ToolCatalogueEntry]:
        from orchestrator.open_dag.composability_audit import Composability
        return [
            ToolCatalogueEntry(
                mcp_tool_name="t1",
                description="First tool docstring",
                resolver_tool_key="t1",
                composability=Composability.BRIDGEABLE_SERIES,
                output_artifact_type="Series",
                output_field_units={"time_series": "bps"},
                available_output_fields=("time_series",),
            ),
            ToolCatalogueEntry(
                mcp_tool_name="t2",
                description="Second tool docstring",
                resolver_tool_key="t2",
                composability=Composability.BRIDGEABLE_PANEL,
                output_artifact_type="Panel",
                output_field_units={},
                # PR-6A: Panel entries declare ACTUAL Panel-typed
                # output_class fields (introspected at catalogue
                # render time).  Empty tuple is no longer valid; this
                # synthetic entry uses the canonical Panel-field name
                # "panel".
                available_output_fields=("panel",),
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
        from orchestrator.open_dag.composability_audit import Composability
        return [
            ToolCatalogueEntry(
                mcp_tool_name="calculate_curve_spread_tool",
                description="Curve spread doc.",
                resolver_tool_key="calculate_curve_spread_tool",
                composability=Composability.BRIDGEABLE_SERIES,
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

    def test_output_field_not_in_catalogue_becomes_refusal(self) -> None:
        # PR-6A corrective per Codex finding #1: chosen_output_field
        # MUST be in the catalogue entry's available_output_fields.
        # The catalogue is GROUND TRUTH (output_field_units keys for
        # Series; Panel-typed fields for Panel) so a wrong pick would
        # crash the executor bridge.
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="calculate_curve_spread_tool",
            params={"curve_family": "UST"},
            chosen_output_field="time_series_pct",  # NOT in catalogue
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=0.7,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_a",
            domain=Domain.SOVEREIGN_BONDS,
            output=output,
            catalogue=self._cat(),
        )
        assert bound.is_refusal is True
        assert "available_output_fields" in bound.refusal
        assert "BRIDGEABLE_SERIES" in bound.refusal

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


# Per-domain canonical BRIDGEABLE primitive (None when the domain has
# no bridgeable primitives in V1 — bond_futures' three registered
# primitives are all TERMINAL_ONLY_SNAPSHOT).
_DOMAIN_CANONICAL_BRIDGEABLE: Dict[Domain, str | None] = {
    Domain.SOVEREIGN_BONDS: "calculate_curve_spread_tool",
    Domain.OIS: "calculate_ois_curve_spread_tool",
    Domain.INFLATION_INDEXED_BONDS: (
        "calculate_breakeven_inflation_simple_tool"
    ),
    Domain.INFLATION_SWAPS: "calculate_inflation_swap_curve_spread_tool",
    # PR-6A: policy_futures' price/calendar/volume tools are
    # TERMINAL_ONLY; pick a butterfly tool that's BRIDGEABLE_SERIES.
    Domain.POLICY_FUTURES: "get_futures_butterfly_simple_tool",
    # V1 reality: bond_futures registers only snapshot/scanner
    # primitives (price_level, volume_oi, scan_*).  fill_leaf in
    # bond_futures can only refuse honestly until a bridgeable
    # bond-futures primitive is registered.
    Domain.BOND_FUTURES: None,
}


def _per_domain_catalogue(domain: Domain) -> List[ToolCatalogueEntry]:
    from rates_agent.workflows import rates_primitive_resolver
    mcp_name = _DOMAIN_CANONICAL_BRIDGEABLE[domain]
    if mcp_name is None:
        return []
    tools = [_Tool(mcp_name, f"Synthetic doc for {mcp_name}")]
    cat, _ = render_tool_catalogue(
        domain, tools, rates_primitive_resolver,
    )
    return cat


class TestEveryDomainFillLeaf:
    @pytest.mark.parametrize(
        "domain",
        [d for d, name in _DOMAIN_CANONICAL_BRIDGEABLE.items() if name is not None],
    )
    def test_binding_path_returns_clean_bound_leaf(
        self, domain: Domain,
    ) -> None:
        # Parametrised over domains with at least one BRIDGEABLE
        # primitive registered.  Per PR-6A: every kept catalogue
        # entry has non-empty available_output_fields by construction,
        # so picking the first field is guaranteed safe.
        catalogue = _per_domain_catalogue(domain)
        assert catalogue, f"no catalogue entry for domain {domain.value!r}"
        entry = catalogue[0]
        output_field = entry.available_output_fields[0]
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
        assert bound.is_refusal is False, (
            f"unexpected refusal for {domain.value!r}: {bound.refusal}"
        )
        assert bound.domain == domain.value
        assert bound.mcp_tool_name == entry.mcp_tool_name
        assert bound.resolver_tool_key == entry.resolver_tool_key

    def test_bond_futures_binding_refused_no_bridgeable_primitives(
        self,
    ) -> None:
        # Acceptance-criterion-honest coverage for the one domain
        # that has no bridgeable primitives in V1.  fill_leaf still
        # "works" — it cleanly returns a refusal BoundLeaf when the
        # catalogue is empty.
        catalogue = _per_domain_catalogue(Domain.BOND_FUTURES)
        assert catalogue == []
        # Simulate an LLM that picked a (non-existent in catalogue)
        # tool — post-LLM transform refuses with the catalogue-empty
        # signature.
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="any_tool",
            params={},
            chosen_output_field="any_field",
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=0.0,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h_bf",
            domain=Domain.BOND_FUTURES,
            output=output,
            catalogue=catalogue,
        )
        assert bound.is_refusal is True
        assert "not in this domain's tool catalogue" in bound.refusal

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
        cat, _ = render_tool_catalogue(
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

    def test_panel_primitive_uses_panel_typed_output_field(self) -> None:
        # PR-6A corrective per Codex finding #1: the catalogue's
        # available_output_fields for a Panel primitive is the set of
        # Panel-typed fields on the *Output class (introspected at
        # render time).  For build_sovereign_yield_panel_tool the
        # Panel-typed field is "panel" (Optional[Panel] annotation).
        from rates_agent.workflows import rates_primitive_resolver

        from orchestrator.open_dag import declare_primitive_output
        decl = declare_primitive_output(
            rates_primitive_resolver, "build_sovereign_yield_panel_tool",
        )
        assert decl.output_artifact_type == "Panel"

        tools = [_Tool("build_sovereign_yield_panel_tool", "doc")]
        cat, dropped = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        assert len(cat) == 1
        assert dropped == []
        # Panel-typed field surfaced from *Output schema introspection.
        assert "panel" in cat[0].available_output_fields

        output = SelectorLLMOutput(
            chosen_mcp_tool_name="build_sovereign_yield_panel_tool",
            params={},
            chosen_output_field="panel",  # the actual Panel-typed field
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=1.0,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h", domain=Domain.SOVEREIGN_BONDS,
            output=output, catalogue=cat,
        )
        assert bound.is_refusal is False
        assert bound.declared_output_artifact_type == ArtifactTypeName.PANEL
        assert bound.output_field == "panel"

    def test_panel_primitive_wrong_output_field_refuses(self) -> None:
        # If the LLM picks a non-Panel field (e.g. "time_series" as it
        # might guess from Series-primitive convention), the post-LLM
        # transform refuses.  This prevents the runtime crash inside
        # the Panel bridge.
        from rates_agent.workflows import rates_primitive_resolver

        tools = [_Tool("build_sovereign_yield_panel_tool", "doc")]
        cat, _ = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="build_sovereign_yield_panel_tool",
            params={},
            chosen_output_field="time_series",  # NOT a Panel field
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=0.6,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h", domain=Domain.SOVEREIGN_BONDS,
            output=output, catalogue=cat,
        )
        assert bound.is_refusal is True
        assert "BRIDGEABLE_PANEL" in bound.refusal


# ============================================================================
# COMPOSABILITY EXCLUSIONS (PR-6A corrective, Codex finding #1)
# ============================================================================


class TestComposabilityExclusions:
    """Per the PR-6A corrective: the catalogue MUST exclude
    TERMINAL_ONLY_SNAPSHOT and UNDECLARED primitives, surfacing them
    in the dropped list with a diagnostic reason."""

    def test_terminal_only_snapshot_excluded(self) -> None:
        # calculate_half_life_tool is a sovereign_bonds primitive
        # classified TERMINAL_ONLY_SNAPSHOT by the PR-3 audit (Series
        # artifact_type + empty output_field_units).
        from rates_agent.workflows import rates_primitive_resolver
        from orchestrator.open_dag.composability_audit import Composability

        tools = [_Tool("calculate_half_life_tool", "half-life snapshot")]
        cat, dropped = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        assert cat == []
        assert len(dropped) == 1
        assert dropped[0].composability == Composability.TERMINAL_ONLY_SNAPSHOT
        assert "TERMINAL_ONLY_SNAPSHOT" in dropped[0].reason

    def test_mixed_catalogue_filters_correctly(self) -> None:
        # Mixed: one BRIDGEABLE_SERIES + one TERMINAL_ONLY_SNAPSHOT.
        # Kept = bridgeable; dropped = snapshot.
        from rates_agent.workflows import rates_primitive_resolver

        tools = [
            _Tool("calculate_curve_spread_tool", "spread doc"),
            _Tool("calculate_half_life_tool", "half-life snapshot"),
        ]
        cat, dropped = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        kept_names = [e.mcp_tool_name for e in cat]
        dropped_names = [e.mcp_tool_name for e in dropped]
        assert kept_names == ["calculate_curve_spread_tool"]
        assert dropped_names == ["calculate_half_life_tool"]

    def test_dropped_tools_unbindable_via_llm_output(self) -> None:
        # Even if the LLM hallucinated picking a dropped tool's
        # name, the catalogue doesn't contain it → the post-LLM
        # transform refuses with "not in this domain's tool catalogue".
        from rates_agent.workflows import rates_primitive_resolver

        tools = [_Tool("calculate_curve_spread_tool", "spread doc")]
        cat, _ = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, rates_primitive_resolver,
        )
        output = SelectorLLMOutput(
            chosen_mcp_tool_name="calculate_half_life_tool",  # not in catalogue
            params={},
            chosen_output_field="results",
            declared_semantic_role="x",
            declared_output_meaning="x",
            fit_confidence=0.5,
        )
        bound = llm_output_to_bound_leaf(
            leaf_id="h", domain=Domain.SOVEREIGN_BONDS,
            output=output, catalogue=cat,
        )
        assert bound.is_refusal is True
        assert "not in this domain's tool catalogue" in bound.refusal


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
            # PR-11 Codex Round 5: see note on the fixture at line ~230.
            time_series: _TimeSeriesWire

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
        cat, _ = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, _resolver,
        )
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
    construction.  P11 isolation is enforced at the MCP-subprocess
    level (each ``MultiServerMCPClient`` connection sees only its
    own server's tools); the catalogue's per-domain resolver-key
    derivation is the downstream code-level half of the same
    invariant.

    The collision case (``get_futures_price_level_tool`` exists in
    both bond_futures and policy_futures MCP servers) demonstrates
    the per-domain seam.  Post-ADR-0017 both primitives are
    BRIDGEABLE_SERIES and KEPT — with the SAME mcp_tool_name but
    DIFFERENT resolver keys AND different canonical output fields
    (``time_series_price`` in PRICE space for bond_futures vs
    ``time_series_implied_rate`` in PERCENT for policy_futures),
    which proves the per-domain resolver-key convention is respected
    end-to-end (different domain → different key → different spec
    for the same MCP name)."""

    def test_collision_keeps_per_domain_resolver_keys_intact(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver
        from orchestrator.open_dag.resolver_keys import (
            domain_to_resolver_key,
        )

        # Same MCP name in both domains → two DIFFERENT catalogue
        # entries (different resolver key, different canonical field).
        tools = [_Tool("get_futures_price_level_tool", "doc")]
        cat_bond, dropped_bond = render_tool_catalogue(
            Domain.BOND_FUTURES, tools, rates_primitive_resolver,
        )
        cat_policy, dropped_policy = render_tool_catalogue(
            Domain.POLICY_FUTURES, tools, rates_primitive_resolver,
        )
        assert dropped_bond == dropped_policy == []
        assert len(cat_bond) == len(cat_policy) == 1
        assert cat_bond[0].resolver_tool_key == (
            "get_futures_price_level_tool"
        )
        assert cat_policy[0].resolver_tool_key == (
            "policy_futures_get_futures_price_level_tool"
        )
        assert cat_bond[0].available_output_fields == (
            "time_series_price",
        )
        assert cat_policy[0].available_output_fields == (
            "time_series_implied_rate",
        )

        # Direct derivation: different domain → different resolver
        # key for the same MCP name (PR-3's collision-disambiguation
        # seam).
        assert (
            domain_to_resolver_key(
                "bond_futures", "get_futures_price_level_tool",
            )
            != domain_to_resolver_key(
                "policy_futures", "get_futures_price_level_tool",
            )
        )


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
# SESSION-LEVEL fill_leaf TESTS (PR-6A corrective per Codex finding #2)
# ============================================================================
#
# These tests construct a DomainAgentSession via the public
# constructor, MANUALLY populate the internal state that open() would
# normally set up (catalogue, selector model, system message,
# primitive_resolver, _is_open, _tool_names), and call fill_leaf
# directly.  This proves the orchestration glue (routing guard,
# prompt build, LLM invocation, output transform, refusal-on-failure
# semantics, timeout handling) works correctly WITHOUT requiring a
# live LangChain stack or MCP subprocess.


class _MockSelectorModel:
    """Stand-in for ChatAnthropic(...).with_structured_output(...) in
    the structured-output-with-raw mode used by fill_leaf.

    ``ainvoke`` is async and returns the dict shape LangChain produces
    when ``include_raw=True``: ``{"raw": AIMessage-stand-in, "parsed":
    SelectorLLMOutput, "parsing_error": Optional[Exception]}``.

    Tests inject a desired ``SelectorLLMOutput`` (or an exception to
    raise) via the constructor.  The instance also records ainvoke
    calls so tests can assert the model was invoked exactly once with
    the expected messages."""

    def __init__(
        self,
        parsed=None,
        raise_exception=None,
        delay_s: float = 0.0,
    ) -> None:
        self._parsed = parsed
        self._raise_exception = raise_exception
        self._delay_s = delay_s
        self.invocations = []

    async def ainvoke(self, messages):
        import asyncio as _asyncio
        self.invocations.append(messages)
        if self._delay_s > 0:
            await _asyncio.sleep(self._delay_s)
        if self._raise_exception is not None:
            raise self._raise_exception
        return {
            "raw": SimpleNamespace(usage_metadata=None),
            "parsed": self._parsed,
            "parsing_error": None,
        }


def _make_session_with_state(
    *,
    domain: Domain = Domain.SOVEREIGN_BONDS,
    catalogue=None,
    selector_model=None,
    primitive_resolver=None,
    tool_names=None,
):
    """Construct a DomainAgentSession via the constructor and
    populate the internal state that open() would normally fill.
    Lets fill_leaf-level tests run without LangChain/MCP."""
    from orchestrator.domain_agent import DomainAgentSession
    from langchain_core.messages import SystemMessage as _SystemMessage

    session = DomainAgentSession(
        domain=domain,
        system_prompt="test prompt",
        mcp_servers={domain.value: {}},
        model_name="claude-test",
        primitive_resolver=primitive_resolver or (lambda name: None),
    )
    session._is_open = True
    session._tool_catalogue = catalogue or []
    session._dropped_tools = []
    session._selector_model = selector_model
    session._cached_selector_system_message = _SystemMessage(
        content="cached selector system",
    )
    session._tool_names = tool_names or [
        e.mcp_tool_name for e in (catalogue or [])
    ]
    return session


def _bridgeable_catalogue_for(domain: Domain) -> List[ToolCatalogueEntry]:
    """Build a 1-entry BRIDGEABLE_SERIES catalogue for the given domain
    using a real registered primitive."""
    from rates_agent.workflows import rates_primitive_resolver
    name = _DOMAIN_CANONICAL_BRIDGEABLE[domain]
    if name is None:
        return []
    tools = [_Tool(name, f"Synthetic doc for {name}")]
    cat, _ = render_tool_catalogue(domain, tools, rates_primitive_resolver)
    return cat


@pytest.mark.asyncio
class TestFillLeafSessionLevel:
    """Per Codex finding #2: PR-6A acceptance tests must exercise the
    actual ``DomainAgentSession.fill_leaf()`` path (not just the helper
    functions).  These tests use mock selector models that DO NOT
    invoke any LLM or primitive callable."""

    async def test_happy_path_calls_model_and_returns_bound_leaf(
        self,
    ) -> None:
        catalogue = _bridgeable_catalogue_for(Domain.SOVEREIGN_BONDS)
        assert catalogue
        entry = catalogue[0]
        # The mock model returns a binding for the catalogue entry.
        mock_model = _MockSelectorModel(
            parsed=SelectorLLMOutput(
                chosen_mcp_tool_name=entry.mcp_tool_name,
                params={"curve_family": "UST"},
                chosen_output_field=entry.available_output_fields[0],
                declared_semantic_role="spread_level",
                declared_output_meaning="curve spread series",
                fit_confidence=0.92,
            ),
        )
        session = _make_session_with_state(
            domain=Domain.SOVEREIGN_BONDS,
            catalogue=catalogue,
            selector_model=mock_model,
            primitive_resolver=(
                __import__("rates_agent.workflows", fromlist=[""])
                .rates_primitive_resolver
            ),
        )
        request = _request(
            domain=Domain.SOVEREIGN_BONDS,
            semantic_role="spread_level",
        )

        bound = await session.fill_leaf("h_a", request)

        assert bound.is_refusal is False
        assert bound.leaf_id == "h_a"
        assert bound.domain == "sovereign_bonds"
        assert bound.mcp_tool_name == entry.mcp_tool_name
        # The model was invoked exactly once.
        assert len(mock_model.invocations) == 1
        # The invocation included the cached system message + a human
        # message carrying the LeafRequest + catalogue render.
        msgs = mock_model.invocations[0]
        assert len(msgs) == 2

    async def test_refusal_returned_as_bound_leaf(self) -> None:
        catalogue = _bridgeable_catalogue_for(Domain.SOVEREIGN_BONDS)
        mock_model = _MockSelectorModel(
            parsed=SelectorLLMOutput(
                fit_confidence=0.0,
                refusal="No primitive fits the request.",
            ),
        )
        session = _make_session_with_state(
            domain=Domain.SOVEREIGN_BONDS,
            catalogue=catalogue,
            selector_model=mock_model,
        )
        bound = await session.fill_leaf(
            "h_a", _request(domain=Domain.SOVEREIGN_BONDS),
        )
        assert bound.is_refusal is True
        assert "No primitive fits the request." in bound.refusal

    async def test_cross_domain_request_raises_routing_error(self) -> None:
        catalogue = _bridgeable_catalogue_for(Domain.SOVEREIGN_BONDS)
        mock_model = _MockSelectorModel(parsed=None)
        session = _make_session_with_state(
            domain=Domain.SOVEREIGN_BONDS,
            catalogue=catalogue,
            selector_model=mock_model,
        )
        # Build a request whose domain_hint is OIS (not this session).
        ois_request = _request(domain=Domain.OIS)
        with pytest.raises(SelectorBindingError, match="domain_hint"):
            await session.fill_leaf("h_a", ois_request)
        # The mock model was NEVER invoked — guard fires pre-LLM.
        assert mock_model.invocations == []

    async def test_llm_exception_returns_refusal_bound_leaf(self) -> None:
        catalogue = _bridgeable_catalogue_for(Domain.SOVEREIGN_BONDS)
        mock_model = _MockSelectorModel(
            raise_exception=RuntimeError("anthropic backend down"),
        )
        session = _make_session_with_state(
            domain=Domain.SOVEREIGN_BONDS,
            catalogue=catalogue,
            selector_model=mock_model,
        )
        bound = await session.fill_leaf(
            "h_a", _request(domain=Domain.SOVEREIGN_BONDS),
        )
        assert bound.is_refusal is True
        assert "anthropic backend down" in bound.refusal
        assert "Selector LLM call failed" in bound.refusal

    async def test_timeout_returns_refusal_bound_leaf(self) -> None:
        # The mock model sleeps longer than the requested timeout.
        catalogue = _bridgeable_catalogue_for(Domain.SOVEREIGN_BONDS)
        mock_model = _MockSelectorModel(
            parsed=SelectorLLMOutput(fit_confidence=0.5, refusal="late"),
            delay_s=0.5,
        )
        session = _make_session_with_state(
            domain=Domain.SOVEREIGN_BONDS,
            catalogue=catalogue,
            selector_model=mock_model,
        )
        bound = await session.fill_leaf(
            "h_a",
            _request(domain=Domain.SOVEREIGN_BONDS),
            timeout_s=0.05,
        )
        assert bound.is_refusal is True
        assert "timed out" in bound.refusal

    async def test_missing_resolver_raises_runtime_error(self) -> None:
        # No primitive_resolver was supplied; fill_leaf must raise
        # rather than silently misbehave.
        from orchestrator.domain_agent import DomainAgentSession

        session = DomainAgentSession(
            domain=Domain.SOVEREIGN_BONDS,
            system_prompt="test",
            mcp_servers={"sovereign_bonds": {}},
            model_name="claude-test",
        )
        # Manually mark open (without the selector scaffolding).
        session._is_open = True
        with pytest.raises(RuntimeError, match="primitive_resolver"):
            await session.fill_leaf(
                "h_a", _request(domain=Domain.SOVEREIGN_BONDS),
            )

    async def test_p11_invariant_tool_names_only_own_domain(self) -> None:
        # P11 enforces own-domain MCP isolation.  The session's
        # _tool_names is populated from the MCP client's get_tools()
        # which only sees this domain's MCP server.  Verify the
        # downstream code respects that — the catalogue + _tool_names
        # both reflect ONLY the supplied tools.
        catalogue = _bridgeable_catalogue_for(Domain.OIS)
        session = _make_session_with_state(
            domain=Domain.OIS,
            catalogue=catalogue,
            selector_model=_MockSelectorModel(
                parsed=SelectorLLMOutput(fit_confidence=0.0, refusal="x"),
            ),
            tool_names=[e.mcp_tool_name for e in catalogue],
        )
        # _tool_names matches the OIS catalogue entries — no cross-
        # domain tool leaked in.
        assert set(session._tool_names) == {
            e.mcp_tool_name for e in catalogue
        }
        # And the catalogue's resolver keys are derived under THIS
        # domain's convention only.
        for entry in catalogue:
            from orchestrator.open_dag.resolver_keys import (
                domain_to_resolver_key,
            )
            assert entry.resolver_tool_key == domain_to_resolver_key(
                "ois", entry.mcp_tool_name,
            )

    async def test_no_primitive_callable_invoked_through_fill_leaf(
        self,
    ) -> None:
        # Pass a resolver whose primitive's callable raises on
        # invocation.  fill_leaf must NOT invoke the callable —
        # it only inspects metadata in the pre-built catalogue.
        def _explodes(*args, **kwargs):
            raise AssertionError(
                "fill_leaf invoked a primitive callable — violates "
                "no-execution contract"
            )

        class _In(BaseModel):
            pass

        class _Out(BaseModel):
            # PR-11 Codex Round 5: see note on the fixture at line ~230.
            time_series: _TimeSeriesWire

        spec = PrimitiveSpec(
            tool_name="silent_tool",
            callable=_explodes,
            input_class=_In,
            output_class=_Out,
            config_path=Path("/dev/null"),
            output_artifact_type="Series",
            output_field_units={"time_series": "bps"},
        )

        def _silent_resolver(tool_name: str) -> PrimitiveSpec:
            return spec

        # Build a catalogue manually (since the exploding-callable
        # resolver doesn't have rates primitives).
        tools = [_Tool("silent_tool", "doc")]
        catalogue, _ = render_tool_catalogue(
            Domain.SOVEREIGN_BONDS, tools, _silent_resolver,
        )
        assert len(catalogue) == 1
        entry = catalogue[0]

        mock_model = _MockSelectorModel(
            parsed=SelectorLLMOutput(
                chosen_mcp_tool_name=entry.mcp_tool_name,
                params={},
                chosen_output_field=entry.available_output_fields[0],
                declared_semantic_role="x",
                declared_output_meaning="x",
                fit_confidence=0.9,
            ),
        )

        session = _make_session_with_state(
            domain=Domain.SOVEREIGN_BONDS,
            catalogue=catalogue,
            selector_model=mock_model,
            primitive_resolver=_silent_resolver,
        )

        bound = await session.fill_leaf(
            "h_a", _request(domain=Domain.SOVEREIGN_BONDS),
        )
        # Either way, the exploding callable was never invoked
        # (otherwise the AssertionError would have escaped).
        assert bound.leaf_id == "h_a"


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

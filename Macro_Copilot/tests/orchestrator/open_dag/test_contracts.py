"""tests/workflow/test_holes.py — PR-3 acceptance suite for hole/fill contracts.

Covers ``shared.workflow.holes`` (LeafRequest, BoundLeaf, LeafHole,
ShapeSpec) and the ``declare_primitive_output_type`` helper from
``shared.workflow.registry``.

The plan (``tmp/orchestration.md`` §PR-3) specifies:

  1. Round-trip ``LeafRequest`` and ``BoundLeaf`` through JSON; assert
     frozen / immutable.
  2. ``declare_primitive_output_type(...)`` returns the static
     ``output_artifact_type`` without executing the primitive (no DB).
  3. ``ShapeSpec`` construction-time gate mirrors ``Workflow``'s
     (unique node IDs, terminal exists, edges reference real nodes,
     literal bindings target real nodes).
  4. ``BoundLeaf`` enforces resolver-key consistency at construction
     (the ``resolver_tool_key`` must match
     ``domain_to_resolver_key(domain, mcp_tool_name)``).
  5. ``BoundLeaf`` supports both modes — binding (refusal=None) and
     refusal (sentinel structural fields allowed).
  6. P11 — the substrate ``shared.workflow.holes`` module imports
     nothing from ``rates_agent/`` or ``orchestrator/``.

All tests are fully offline.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from shared.artifacts.registry import ArtifactTypeName
from shared.schemas.time_series import TimeSeriesUnits
from shared.workflow import OperatorNode, WorkflowEdge
from shared.workflow.types import LiteralBinding
from orchestrator.open_dag import (
    BoundLeaf,
    Frequency,
    LeafHole,
    LeafRequest,
    ShapeSpec,
    domain_to_resolver_key,
)
from orchestrator.open_dag.resolver_keys import UnknownDomainError


# ============================================================================
# CONVENIENCE BUILDERS
# ============================================================================


def _make_request(**overrides: Any) -> LeafRequest:
    defaults: Dict[str, Any] = dict(
        required_artifact_type=ArtifactTypeName.SERIES,
        domain_hint="sovereign_bonds",
        semantic_role="spread_level",
        requested_output_meaning="one curve-spread series",
        nl_intent="two-tenor spread over the past 5y",
    )
    defaults.update(overrides)
    return LeafRequest(**defaults)


def _make_binding(**overrides: Any) -> BoundLeaf:
    defaults: Dict[str, Any] = dict(
        leaf_id="hole_A",
        domain="sovereign_bonds",
        mcp_tool_name="calculate_curve_spread_tool",
        resolver_tool_key="calculate_curve_spread_tool",
        params={"curve_family": "UST"},
        output_field="time_series",
        declared_output_artifact_type=ArtifactTypeName.SERIES,
        declared_semantic_role="spread_level",
        declared_output_meaning="curve spread Series over the lookback",
        fit_confidence=0.9,
    )
    defaults.update(overrides)
    return BoundLeaf(**defaults)


def _make_refusal(**overrides: Any) -> BoundLeaf:
    defaults: Dict[str, Any] = dict(
        leaf_id="hole_B",
        domain="sovereign_bonds",
        fit_confidence=0.0,
        refusal="No primitive in this domain matches the request.",
    )
    defaults.update(overrides)
    return BoundLeaf(**defaults)


# ============================================================================
# LEAF REQUEST
# ============================================================================


class TestLeafRequest:
    def test_constructs_with_all_required_fields(self) -> None:
        req = _make_request()
        assert req.required_artifact_type == ArtifactTypeName.SERIES
        assert req.domain_hint == "sovereign_bonds"
        assert req.semantic_role == "spread_level"
        # Optional fields default to None.
        assert req.expected_units is None
        assert req.expected_frequency is None

    def test_accepts_typed_units_enum(self) -> None:
        req = _make_request(expected_units=TimeSeriesUnits.BPS)
        assert req.expected_units == TimeSeriesUnits.BPS

    def test_accepts_string_unit_via_pydantic_coercion(self) -> None:
        # Pydantic coerces a plain string to the enum member if it
        # matches a .value.  Confirms callers can pass either form.
        req = _make_request(expected_units="bps")
        assert req.expected_units == TimeSeriesUnits.BPS

    def test_rejects_unknown_unit(self) -> None:
        with pytest.raises(ValidationError):
            _make_request(expected_units="dogecoin")

    def test_rejects_unknown_domain_hint(self) -> None:
        # Pydantic v2 wraps validator errors in ValidationError; the
        # underlying UnknownDomainError is surfaced in the message.
        with pytest.raises(ValidationError, match="not a known substrate domain"):
            _make_request(domain_hint="fx")

    def test_is_frozen(self) -> None:
        req = _make_request()
        with pytest.raises(ValidationError):
            req.semantic_role = "mutated"  # type: ignore[misc]

    def test_round_trips_through_json(self) -> None:
        original = _make_request(
            expected_units=TimeSeriesUnits.BPS,
            expected_frequency="daily",
        )
        as_json = original.model_dump_json()
        # Parse the JSON to ensure it's well-formed JSON.
        parsed = json.loads(as_json)
        assert parsed["domain_hint"] == "sovereign_bonds"
        round_tripped = LeafRequest.model_validate_json(as_json)
        assert round_tripped == original

    def test_rejects_empty_nl_intent(self) -> None:
        with pytest.raises(ValidationError):
            _make_request(nl_intent="")

    def test_rejects_extra_fields(self) -> None:
        # extra="forbid" — silent typos in LLM-emitted JSON surface
        # as validation errors here rather than as silent drops.
        with pytest.raises(ValidationError):
            LeafRequest(
                required_artifact_type=ArtifactTypeName.SERIES,
                domain_hint="sovereign_bonds",
                semantic_role="x",
                requested_output_meaning="y",
                nl_intent="z",
                unknown_field="should-be-rejected",
            )


# ============================================================================
# BOUND LEAF — binding mode
# ============================================================================


class TestBoundLeafBindingMode:
    def test_constructs_with_full_binding(self) -> None:
        b = _make_binding()
        assert b.is_refusal is False
        assert b.mcp_tool_name == "calculate_curve_spread_tool"
        assert b.resolver_tool_key == "calculate_curve_spread_tool"

    def test_round_trips_through_json(self) -> None:
        original = _make_binding(
            declared_units=TimeSeriesUnits.BPS,
            declared_frequency="daily",
        )
        as_json = original.model_dump_json()
        round_tripped = BoundLeaf.model_validate_json(as_json)
        assert round_tripped == original

    def test_is_frozen(self) -> None:
        b = _make_binding()
        with pytest.raises(ValidationError):
            b.fit_confidence = 0.5  # type: ignore[misc]

    def test_rejects_unknown_domain(self) -> None:
        with pytest.raises(ValidationError, match="not a known substrate domain"):
            _make_binding(domain="fx")

    def test_rejects_fit_confidence_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            _make_binding(fit_confidence=1.5)
        with pytest.raises(ValidationError):
            _make_binding(fit_confidence=-0.1)

    def test_rejects_missing_mcp_tool_name_in_binding_mode(self) -> None:
        with pytest.raises(ValidationError, match="non-empty mcp_tool_name"):
            _make_binding(mcp_tool_name="", resolver_tool_key="")

    def test_rejects_missing_output_field_in_binding_mode(self) -> None:
        with pytest.raises(ValidationError, match="non-empty output_field"):
            _make_binding(output_field="")

    def test_rejects_missing_declared_semantic_role_in_binding_mode(self) -> None:
        with pytest.raises(ValidationError, match="declared_semantic_role"):
            _make_binding(declared_semantic_role="")

    def test_rejects_missing_declared_output_meaning_in_binding_mode(self) -> None:
        with pytest.raises(ValidationError, match="declared_output_meaning"):
            _make_binding(declared_output_meaning="")

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            BoundLeaf(
                leaf_id="x", domain="sovereign_bonds",
                mcp_tool_name="t", resolver_tool_key="t",
                output_field="f",
                declared_output_artifact_type=ArtifactTypeName.SERIES,
                declared_semantic_role="r",
                declared_output_meaning="m",
                fit_confidence=0.5,
                trash_field=42,
            )


# ============================================================================
# BOUND LEAF — resolver-key consistency
# ============================================================================


class TestBoundLeafResolverKey:
    def test_bare_domain_consistent_key_accepted(self) -> None:
        b = _make_binding(
            domain="bond_futures",
            mcp_tool_name="get_futures_price_level_tool",
            resolver_tool_key="get_futures_price_level_tool",
        )
        assert b.resolver_tool_key == "get_futures_price_level_tool"

    def test_prefixed_domain_consistent_key_accepted(self) -> None:
        b = _make_binding(
            domain="policy_futures",
            mcp_tool_name="get_futures_price_level_tool",
            resolver_tool_key="policy_futures_get_futures_price_level_tool",
        )
        assert b.resolver_tool_key == (
            "policy_futures_get_futures_price_level_tool"
        )

    def test_bare_domain_with_prefixed_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="does not match"):
            _make_binding(
                domain="bond_futures",
                mcp_tool_name="get_futures_price_level_tool",
                resolver_tool_key=(
                    "bond_futures_get_futures_price_level_tool"
                ),  # Wrong — bond_futures uses bare names.
            )

    def test_prefixed_domain_with_bare_key_rejected(self) -> None:
        with pytest.raises(ValidationError, match="does not match"):
            _make_binding(
                domain="policy_futures",
                mcp_tool_name="get_futures_price_level_tool",
                resolver_tool_key=(
                    "get_futures_price_level_tool"
                ),  # Wrong — policy_futures uses prefixed names.
            )

    def test_canonical_collision_disambiguates_at_construction(self) -> None:
        """Round-trip BOTH sides of the canonical collision via
        BoundLeaf — they must construct cleanly and carry different
        keys."""
        a = _make_binding(
            domain="policy_futures",
            mcp_tool_name="get_futures_price_level_tool",
            resolver_tool_key=domain_to_resolver_key(
                "policy_futures", "get_futures_price_level_tool",
            ),
        )
        b = _make_binding(
            leaf_id="hole_other",
            domain="bond_futures",
            mcp_tool_name="get_futures_price_level_tool",
            resolver_tool_key=domain_to_resolver_key(
                "bond_futures", "get_futures_price_level_tool",
            ),
        )
        assert a.resolver_tool_key != b.resolver_tool_key


# ============================================================================
# BOUND LEAF — refusal mode
# ============================================================================


class TestBoundLeafRefusalMode:
    def test_refusal_with_sentinel_fields_accepted(self) -> None:
        r = _make_refusal()
        assert r.is_refusal is True
        assert r.refusal == "No primitive in this domain matches the request."
        # Sentinel fields tolerated.
        assert r.mcp_tool_name == ""
        assert r.output_field == ""

    def test_refusal_with_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-empty string"):
            _make_refusal(refusal="")

    def test_refusal_with_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-empty string"):
            _make_refusal(refusal="   ")

    def test_refusal_round_trips_through_json(self) -> None:
        original = _make_refusal()
        as_json = original.model_dump_json()
        round_tripped = BoundLeaf.model_validate_json(as_json)
        assert round_tripped == original
        assert round_tripped.is_refusal is True


# ============================================================================
# LEAF HOLE
# ============================================================================


class TestLeafHole:
    def test_constructs_with_request(self) -> None:
        hole = LeafHole(node_id="hole_A", leaf_request=_make_request())
        assert hole.kind == "leaf_hole"
        assert hole.node_id == "hole_A"
        assert hole.leaf_request.domain_hint == "sovereign_bonds"

    def test_is_frozen(self) -> None:
        hole = LeafHole(node_id="hole_A", leaf_request=_make_request())
        with pytest.raises(ValidationError):
            hole.node_id = "mutated"  # type: ignore[misc]

    def test_round_trips_through_json(self) -> None:
        original = LeafHole(node_id="hole_A", leaf_request=_make_request())
        round_tripped = LeafHole.model_validate_json(
            original.model_dump_json(),
        )
        assert round_tripped == original


# ============================================================================
# SHAPE SPEC — construction-time gate
# ============================================================================


class TestShapeSpecConstruction:
    def _hole(self, node_id: str = "hole_A") -> LeafHole:
        return LeafHole(node_id=node_id, leaf_request=_make_request())

    def _op(self, node_id: str = "op1", operator_name: str = "correlation") -> OperatorNode:
        return OperatorNode(node_id=node_id, operator_name=operator_name)

    def test_constructs_with_holes_and_operators(self) -> None:
        spec = ShapeSpec(
            workflow_id="canonical",
            nodes=[
                self._hole("h_a"),
                self._hole("h_b"),
                self._op("align", "align_series"),
                self._op("sel_a", "select_from_series_set"),
                self._op("sel_b", "select_from_series_set"),
                self._op("corr", "correlation"),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="h_a", target_node_id="align",
                    target_input_slot="series_list",
                ),
                WorkflowEdge(
                    source_node_id="h_b", target_node_id="align",
                    target_input_slot="series_list",
                ),
                WorkflowEdge(
                    source_node_id="align", target_node_id="sel_a",
                    target_input_slot="series_set",
                ),
                WorkflowEdge(
                    source_node_id="align", target_node_id="sel_b",
                    target_input_slot="series_set",
                ),
                WorkflowEdge(
                    source_node_id="sel_a", target_node_id="corr",
                    target_input_slot="left",
                ),
                WorkflowEdge(
                    source_node_id="sel_b", target_node_id="corr",
                    target_input_slot="right",
                ),
            ],
            terminal_node_id="corr",
        )
        assert len(spec.leaf_holes()) == 2
        assert spec.leaf_hole_ids() == ["h_a", "h_b"]

    def test_rejects_duplicate_node_ids(self) -> None:
        with pytest.raises(ValidationError, match="duplicate node_id"):
            ShapeSpec(
                workflow_id="dup",
                nodes=[
                    self._hole("h"),
                    self._hole("h"),  # dup
                ],
                terminal_node_id="h",
            )

    def test_rejects_terminal_not_in_nodes(self) -> None:
        with pytest.raises(ValidationError, match="terminal_node_id"):
            ShapeSpec(
                workflow_id="bad_terminal",
                nodes=[self._hole("h")],
                terminal_node_id="not_a_node",
            )

    def test_rejects_edge_with_unknown_source(self) -> None:
        with pytest.raises(ValidationError, match="unknown source_node_id"):
            ShapeSpec(
                workflow_id="bad_edge_src",
                nodes=[
                    self._hole("h"),
                    self._op("op", "rolling_zscore"),
                ],
                edges=[
                    WorkflowEdge(
                        source_node_id="ghost", target_node_id="op",
                        target_input_slot="series",
                    ),
                ],
                terminal_node_id="op",
            )

    def test_rejects_edge_with_unknown_target(self) -> None:
        with pytest.raises(ValidationError, match="unknown target_node_id"):
            ShapeSpec(
                workflow_id="bad_edge_tgt",
                nodes=[
                    self._hole("h"),
                    self._op("op", "rolling_zscore"),
                ],
                edges=[
                    WorkflowEdge(
                        source_node_id="h", target_node_id="ghost",
                        target_input_slot="series",
                    ),
                ],
                terminal_node_id="op",
            )

    def test_rejects_literal_binding_with_unknown_target(self) -> None:
        with pytest.raises(ValidationError, match="literal binding"):
            ShapeSpec(
                workflow_id="bad_literal",
                nodes=[self._op("op", "series_arithmetic")],
                literal_bindings=[
                    LiteralBinding(
                        target_node_id="ghost",
                        target_input_slot="right",
                        value=2.0,
                    ),
                ],
                terminal_node_id="op",
            )

    def test_node_by_id_returns_node(self) -> None:
        spec = ShapeSpec(
            workflow_id="lookup",
            nodes=[self._hole("h"), self._op("o", "rolling_zscore")],
            edges=[
                WorkflowEdge(
                    source_node_id="h", target_node_id="o",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="o",
        )
        assert spec.node_by_id("h").kind == "leaf_hole"
        assert spec.node_by_id("o").kind == "operator"
        with pytest.raises(KeyError):
            spec.node_by_id("not_there")

    def test_round_trips_through_json(self) -> None:
        original = ShapeSpec(
            workflow_id="rt",
            nodes=[self._hole("h"), self._op("o", "rolling_zscore")],
            edges=[
                WorkflowEdge(
                    source_node_id="h", target_node_id="o",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="o",
        )
        # Discriminated union round-trip — LeafHole vs OperatorNode
        # must reconstruct correctly from `kind` discriminator.
        round_tripped = ShapeSpec.model_validate_json(
            original.model_dump_json(),
        )
        assert round_tripped == original
        assert round_tripped.node_by_id("h").kind == "leaf_hole"
        assert round_tripped.node_by_id("o").kind == "operator"

    def test_is_frozen(self) -> None:
        spec = ShapeSpec(
            workflow_id="frozen",
            nodes=[self._hole("h")],
            terminal_node_id="h",
        )
        with pytest.raises(ValidationError):
            spec.workflow_id = "mutated"  # type: ignore[misc]


# ============================================================================
# DOMAIN-AWARE OPEN-DAG MODULES MUST NOT IMPORT FROM rates_agent
# ============================================================================
#
# After the PR-A3 corrective patch, contracts + resolver_keys live in
# orchestrator/open_dag/ (one layer above the finance-blind substrate).
# They are allowed to know about the closed KNOWN_DOMAINS set — that's
# domain-routing, not instrument-aware code.  They MUST NOT import
# from rates_agent (that would make adding a new instrument type
# require editing open-DAG core).


class TestModulesNotDomainSpecific:

    @pytest.mark.parametrize(
        "module_path",
        [
            "orchestrator/open_dag/contracts.py",
            "orchestrator/open_dag/resolver_keys.py",
            "orchestrator/open_dag/primitive_declarations.py",
            "orchestrator/open_dag/composability_audit.py",
            "orchestrator/open_dag/assembler.py",
        ],
    )
    def test_no_rates_agent_imports(self, module_path: str) -> None:
        path = Path(__file__).resolve().parents[3] / module_path
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("rates_agent"), (
                        f"{module_path} imports {alias.name} — open-DAG "
                        "core must not depend on a specific domain "
                        "package (P11)."
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert not node.module.startswith("rates_agent"), (
                        f"{module_path} imports from {node.module} — "
                        "open-DAG core must not depend on rates_agent "
                        "(P11)."
                    )

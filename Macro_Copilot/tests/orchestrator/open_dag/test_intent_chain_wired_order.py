"""tests/orchestrator/open_dag/test_intent_chain_wired_order.py —
G-1.5 lock test (FRONTEND_SCORECARD T3 nit).

The L6 "Wired:" line reads ``ComposerIntentRecord.operator_names``
verbatim.  Pre-fix those were sorted ALPHABETICALLY BY NODE_ID — a DAG
whose terminal happened to sort first read backwards ("correlation ->
align_series").  The record must list operators in TOPOLOGICAL
EXECUTION ORDER (deterministic Kahn walk; ties broken by node_id so
identical graphs stay byte-stable).

The fixture deliberately names the TERMINAL node so it sorts FIRST
alphabetically — the one case the old node-id sort gets wrong.
"""

from __future__ import annotations

from orchestrator.contracts import (
    Domain,
    EconomicQuantity,
    IntentTag,
    RouteAction,
    RouteDecision,
)
from orchestrator.open_dag import GateVerdict, IntentChain
from orchestrator.open_dag.contracts import BoundLeaf, Frequency
from shared.artifacts.registry import ArtifactTypeName
from shared.workflow.types import (
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
)


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


def _adversarial_workflow() -> Workflow:
    """Two leaves → align_series → correlation, with node ids chosen so
    the ALPHABETICAL order is the REVERSE of the execution order:

        execution : z_leaf_a, z_leaf_b → b_align → a_correlation
        node-id   : a_correlation, b_align, z_leaf_a, z_leaf_b
    """
    return Workflow(
        workflow_id="wired_order_fixture",
        nodes=[
            OperatorNode(node_id="a_correlation", operator_name="correlation"),
            OperatorNode(node_id="b_align", operator_name="align_series"),
            PrimitiveNode(
                node_id="z_leaf_a",
                tool_name="calculate_curve_spread_tool",
                output_field="time_series",
                params={"curve_family": "UST"},
            ),
            PrimitiveNode(
                node_id="z_leaf_b",
                tool_name="calculate_curve_spread_tool",
                output_field="time_series",
                params={"curve_family": "UK_GILT"},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="z_leaf_a",
                target_node_id="b_align",
                target_input_slot="series_list",
            ),
            WorkflowEdge(
                source_node_id="z_leaf_b",
                target_node_id="b_align",
                target_input_slot="series_list",
            ),
            WorkflowEdge(
                source_node_id="b_align",
                target_node_id="a_correlation",
                target_input_slot="series_set",
            ),
        ],
        terminal_node_id="a_correlation",
    )


class TestWiredExecutionOrder:
    def test_operator_names_follow_execution_order_not_node_id(self):
        chain = IntentChain.from_inputs(
            user_prompt="correlate the two spreads",
            route_decision=_mk_route_decision(),
            bound_leaves=[_mk_bound_leaf("z_leaf_a"), _mk_bound_leaf("z_leaf_b")],
            shape_or_workflow=_adversarial_workflow(),
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
        )
        assert chain.composer.operator_names == (
            "align_series",
            "correlation",
        ), (
            "operator_names must read in topological execution order "
            "(align before correlation), not alphabetical node-id "
            f"order; got {chain.composer.operator_names!r}"
        )

    def test_order_is_deterministic_across_constructions(self):
        a = IntentChain.from_inputs(
            user_prompt="p",
            route_decision=_mk_route_decision(),
            bound_leaves=[_mk_bound_leaf("z_leaf_a")],
            shape_or_workflow=_adversarial_workflow(),
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
        )
        b = IntentChain.from_inputs(
            user_prompt="p",
            route_decision=_mk_route_decision(),
            bound_leaves=[_mk_bound_leaf("z_leaf_a")],
            shape_or_workflow=_adversarial_workflow(),
            gate_verdict=GateVerdict(status="PASS", reason="ok"),
        )
        assert a.composer.operator_names == b.composer.operator_names

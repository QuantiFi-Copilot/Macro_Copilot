"""orchestrator.open_dag.composer_golden_shapes — PR-7 few-shot library.

The six reference ``ShapeSpec`` instances the Composer's system prompt
embeds verbatim as few-shots.  Each one demonstrates the canonical
**hole-first** shape for one ``IntentTag`` value.

Why a constant module
=====================

Per ``tmp/orchestration.md`` §PR-7:

  > Golden few-shots (the minimum set, baked into the prompt) ... The
  > Composer ALWAYS sees the full 16-operator catalogue.  No
  > shortlister.

A separate module keeps the few-shots:

  - **Round-trippable** through the typed contracts — every constant is
    a real ``ShapeSpec`` instance, so the construction-time validator
    in ``ShapeSpec`` already catches typos / wrong slot names / missing
    terminals at import time.  If a shape is unreachable / malformed
    the import fails loudly rather than the prompt silently embedding a
    junk example.
  - **Renderable** as plain text — ``render_shape_for_prompt`` produces
    deterministic JSON-shaped output the Composer's system prompt
    embeds (cache-friendly).
  - **Substitutable** in tests — tests can import the constants
    directly and assert canonical structure (golden #1 == 2-leaf
    correlation, etc.).

The pair-stats discipline (CRITICAL)
====================================

Four of the six golden few-shots are **pair-stats** shapes
(correlation, rolling_correlation, cointegration, rolling_regression).
The canonical pair-stats shape is::

    [LeafHole A, LeafHole B] (both required_artifact_type=Series)
        |
        |    -- both feed ``series_list`` (list-shaped slot, fan-in) --
        v
    align_series (output: SeriesSet)
        |
        |    -- one edge to each of two select_from_series_set nodes --
        v
    [select_from_series_set A, select_from_series_set B]
        |                |    (each picks its own key out of the SeriesSet)
        |                |
        v                v
              <pair-stats operator>
                (left, right Series → ScalarMetric or Series)

The two ``select_from_series_set`` nodes are NOT optional — the
upstream ``align_series`` returns a SeriesSet, and the downstream
pair-stats operators (correlation / rolling_correlation /
cointegration / rolling_regression) declare ``left`` + ``right`` slots
of artifact type ``Series``.  Without the two ``select`` extractors
the validator would flag ``E_TYPE_MISMATCH`` on both edges.

Per the handoff document (``tmp/handoff_pr7.md`` §13): "the previous
agent's first plan got the shape wrong" — without the explicit select
nodes between ``align_series`` and the pair-stats operator, the
substrate validator rejects the shape.  The Composer's prompt MUST
teach the LLM this canonical chain so it doesn't recreate the bug.

Finance-blindness
=================

The few-shots use abstract ``domain_hint`` values (sovereign_bonds /
ois) and abstract ``nl_intent`` strings ("first input quantity",
"event-trigger series").  They do not bake in specific instrument
vocabulary (no UST/SOFR/BTP).  R11 / R12 are respected — the few-shots
encode SHAPE, not finance.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from orchestrator.contracts import IntentTag
from orchestrator.open_dag.contracts import (
    Frequency,
    LeafHole,
    LeafRequest,
    ShapeSpec,
)
from shared.artifacts.registry import ArtifactTypeName
from shared.workflow.types import LiteralBinding, OperatorNode, WorkflowEdge


# ============================================================================
# HELPERS — build canonical pair-stats upstream subgraph
# ============================================================================
#
# The repeat-yourself surface in golden #1..#4 is the upstream chain:
# two LeafHoles -> align_series -> two select_from_series_set extractors.
# A helper isolates that pattern so each golden differs only in its
# pair-stats operator (and any operator params).


def _leaf_hole(
    *,
    node_id: str,
    domain_hint: str,
    semantic_role: str,
    requested_output_meaning: str,
    nl_intent: str,
    expected_frequency: Frequency = Frequency.DAILY,
) -> LeafHole:
    """Build a LeafHole carrying a fully-specified LeafRequest for a
    Series input quantity.  Used by every golden shape; deliberately
    NOT pinned to a specific unit so the few-shot stays domain-agnostic
    (R11)."""
    return LeafHole(
        node_id=node_id,
        leaf_request=LeafRequest(
            required_artifact_type=ArtifactTypeName.SERIES,
            expected_units=None,
            expected_frequency=expected_frequency,
            domain_hint=domain_hint,
            semantic_role=semantic_role,
            requested_output_meaning=requested_output_meaning,
            nl_intent=nl_intent,
        ),
    )


def _pair_stats_upstream(
    *,
    leaf_a_id: str = "leaf_a",
    leaf_b_id: str = "leaf_b",
    leaf_a_key: str = "leaf_a",
    leaf_b_key: str = "leaf_b",
    domain_a: str = "sovereign_bonds",
    domain_b: str = "sovereign_bonds",
    semantic_role_a: str = "input_series_a",
    semantic_role_b: str = "input_series_b",
    align_node_id: str = "align",
    select_a_id: str = "select_a",
    select_b_id: str = "select_b",
) -> Sequence[Any]:
    """Build the canonical pair-stats upstream: two leaf-holes,
    align_series, two select_from_series_set extractors, and the
    edges that wire them together.

    Returns
    -------
    (nodes, edges, literal_bindings) :
        nodes  : two LeafHole + one OperatorNode (align_series) + two
                 OperatorNodes (select_from_series_set).  Order: leaves
                 first, then align, then the two selects.
        edges  : leaf_a -> align(series_list), leaf_b -> align(series_list),
                 align -> select_a(series_set), align -> select_b(series_set).
        literal_bindings : the two ``key`` literals for the select nodes
                 (each pinning its own key into the SeriesSet bridge).
    """
    leaf_a = _leaf_hole(
        node_id=leaf_a_id,
        domain_hint=domain_a,
        semantic_role=semantic_role_a,
        requested_output_meaning=(
            "first input series (one leg of the pair); the Selector "
            "binds the concrete primitive"
        ),
        nl_intent=(
            "fetch the first input Series for the pair, aligned on the "
            "same DatetimeIndex as the other leg"
        ),
    )
    leaf_b = _leaf_hole(
        node_id=leaf_b_id,
        domain_hint=domain_b,
        semantic_role=semantic_role_b,
        requested_output_meaning=(
            "second input series (other leg of the pair); the Selector "
            "binds the concrete primitive"
        ),
        nl_intent=(
            "fetch the second input Series for the pair, aligned on "
            "the same DatetimeIndex as the first leg"
        ),
    )
    # PR-7A Codex F1: align_series MUST set ``output_keys`` explicitly.
    # Without it, the operator's runtime keys the output SeriesSet by
    # each input Series's own ``series_key`` field — values L3 doesn't
    # know (they live behind the primitive selector boundary).  Pinning
    # output_keys to the LeafHole node_ids lets the downstream
    # ``select_from_series_set`` nodes reference the keys by name.
    align = OperatorNode(
        node_id=align_node_id,
        operator_name="align_series",
        params={"output_keys": [leaf_a_key, leaf_b_key]},
    )
    # PR-7A Codex F1: the real ``SelectFromSeriesSetParams`` field is
    # ``series_key`` (not ``key``).  Using the wrong name was a silent
    # runtime failure in PR-7 — the few-shots taught the LLM an
    # invalid wiring.
    select_a = OperatorNode(
        node_id=select_a_id,
        operator_name="select_from_series_set",
        params={"series_key": leaf_a_key},
    )
    select_b = OperatorNode(
        node_id=select_b_id,
        operator_name="select_from_series_set",
        params={"series_key": leaf_b_key},
    )

    edges = [
        # both leaves fan into align_series's list-shaped series_list slot
        WorkflowEdge(
            source_node_id=leaf_a_id,
            target_node_id=align_node_id,
            target_input_slot="series_list",
        ),
        WorkflowEdge(
            source_node_id=leaf_b_id,
            target_node_id=align_node_id,
            target_input_slot="series_list",
        ),
        # align fans out to two select extractors
        WorkflowEdge(
            source_node_id=align_node_id,
            target_node_id=select_a_id,
            target_input_slot="series_set",
        ),
        WorkflowEdge(
            source_node_id=align_node_id,
            target_node_id=select_b_id,
            target_input_slot="series_set",
        ),
    ]
    # No literal bindings — the ``key`` lives in each select node's params.
    return [leaf_a, leaf_b, align, select_a, select_b], edges, []


# ============================================================================
# GOLDEN #1 — RELATIONSHIP (full-sample correlation)
# ============================================================================
#
# Shape: 2 leaves -> align_series -> select x2 -> correlation -> ScalarMetric.
# The canonical IntentTag.RELATIONSHIP shape that closes the
# 2-leaf pair-stats discipline.


def _build_correlation_shape() -> ShapeSpec:
    nodes, edges, literals = _pair_stats_upstream()
    correlation_node = OperatorNode(
        node_id="correlation",
        operator_name="correlation",
        params={},
    )
    nodes = list(nodes) + [correlation_node]
    edges = list(edges) + [
        WorkflowEdge(
            source_node_id="select_a",
            target_node_id="correlation",
            target_input_slot="left",
        ),
        WorkflowEdge(
            source_node_id="select_b",
            target_node_id="correlation",
            target_input_slot="right",
        ),
    ]
    return ShapeSpec(
        workflow_id="golden_relationship_correlation",
        nodes=nodes,
        edges=edges,
        literal_bindings=list(literals),
        terminal_node_id="correlation",
    )


# ============================================================================
# GOLDEN #2 — RELATIONSHIP (rolling correlation)
# ============================================================================
#
# Same upstream; rolling_correlation -> Series instead of
# correlation -> ScalarMetric.  Demonstrates the "windowed variant"
# pattern so the Composer doesn't conflate the rolling/full-sample axis
# with the relationship intent itself.


def _build_rolling_correlation_shape() -> ShapeSpec:
    nodes, edges, literals = _pair_stats_upstream()
    roll_node = OperatorNode(
        node_id="rolling_corr",
        operator_name="rolling_correlation",
        params={"window": 60},
    )
    nodes = list(nodes) + [roll_node]
    edges = list(edges) + [
        WorkflowEdge(
            source_node_id="select_a",
            target_node_id="rolling_corr",
            target_input_slot="left",
        ),
        WorkflowEdge(
            source_node_id="select_b",
            target_node_id="rolling_corr",
            target_input_slot="right",
        ),
    ]
    return ShapeSpec(
        workflow_id="golden_relationship_rolling_correlation",
        nodes=nodes,
        edges=edges,
        literal_bindings=list(literals),
        terminal_node_id="rolling_corr",
    )


# ============================================================================
# GOLDEN #3 — COINTEGRATION
# ============================================================================
#
# Same upstream; cointegration -> ScalarMetric.  The
# "is-this-pair-stationary" research workhorse.


def _build_cointegration_shape() -> ShapeSpec:
    nodes, edges, literals = _pair_stats_upstream()
    coint = OperatorNode(
        node_id="cointegration",
        operator_name="cointegration",
        params={},
    )
    nodes = list(nodes) + [coint]
    edges = list(edges) + [
        WorkflowEdge(
            source_node_id="select_a",
            target_node_id="cointegration",
            target_input_slot="left",
        ),
        WorkflowEdge(
            source_node_id="select_b",
            target_node_id="cointegration",
            target_input_slot="right",
        ),
    ]
    return ShapeSpec(
        workflow_id="golden_cointegration",
        nodes=nodes,
        edges=edges,
        literal_bindings=list(literals),
        terminal_node_id="cointegration",
    )


# ============================================================================
# GOLDEN #4 — REGRESSION (rolling beta)
# ============================================================================
#
# Same upstream; rolling_regression -> SeriesSet (keyed by {beta, alpha,
# r_squared}).  The lhs / rhs split keeps the pair-stats discipline:
# lhs = dependent, rhs = single regressor.


def _build_rolling_regression_shape() -> ShapeSpec:
    nodes, edges, literals = _pair_stats_upstream(
        semantic_role_a="dependent_series",
        semantic_role_b="regressor_series",
    )
    regress = OperatorNode(
        node_id="rolling_regression",
        operator_name="rolling_regression",
        params={"window": 60},
    )
    nodes = list(nodes) + [regress]
    edges = list(edges) + [
        WorkflowEdge(
            source_node_id="select_a",
            target_node_id="rolling_regression",
            target_input_slot="lhs",
        ),
        WorkflowEdge(
            source_node_id="select_b",
            target_node_id="rolling_regression",
            target_input_slot="rhs",
        ),
    ]
    return ShapeSpec(
        workflow_id="golden_regression_rolling_beta",
        nodes=nodes,
        edges=edges,
        literal_bindings=list(literals),
        terminal_node_id="rolling_regression",
    )


# ============================================================================
# GOLDEN #5 — EVENT_REGIME (event-conditional aggregate)
# ============================================================================
#
# Shape: 1 trigger-leaf -> threshold_events -> event_windows(target=2nd leaf)
#        -> conditional_aggregate -> Series.
#
# Two leaves: one drives the EventSet, the other is the target series
# sampled around each event.  The Composer must wire the second leaf as
# the ``target`` slot of event_windows (NOT through align_series).


def _build_event_regime_shape() -> ShapeSpec:
    trigger_leaf = _leaf_hole(
        node_id="leaf_trigger",
        domain_hint="sovereign_bonds",
        semantic_role="event_trigger_series",
        requested_output_meaning=(
            "trigger series whose threshold crossings define the "
            "event dates"
        ),
        nl_intent=(
            "fetch the trigger Series whose values will be compared "
            "against a threshold to find event dates"
        ),
    )
    target_leaf = _leaf_hole(
        node_id="leaf_target",
        domain_hint="sovereign_bonds",
        semantic_role="target_series",
        requested_output_meaning=(
            "target series to sample around each event date"
        ),
        nl_intent=(
            "fetch the target Series to sample around the trigger "
            "event dates"
        ),
    )
    # PR-7A Codex F1: real ``ThresholdEventsParams`` uses ``rule`` (a
    # ThresholdRule literal of {"abs_above", "above", "below"}) and
    # ``rolling_window`` — NOT the prior PR-7 names ``comparison`` and
    # ``window`` which both fail Pydantic validation.
    threshold = OperatorNode(
        node_id="threshold",
        operator_name="threshold_events",
        params={
            "rule": "above",
            "threshold": 1.5,
            "threshold_basis": "rolling_zscore",
            "rolling_window": 252,
        },
    )
    # PR-7A Codex F1: real ``EventWindowsParams`` uses ``pre_window`` +
    # ``post_window`` (non-negative ints, days before / after the event
    # date) — NOT an ``offsets`` list.
    windows = OperatorNode(
        node_id="windows",
        operator_name="event_windows",
        params={"pre_window": 1, "post_window": 5},
    )
    aggregate = OperatorNode(
        node_id="aggregate",
        operator_name="conditional_aggregate",
        params={"aggregator": "mean"},
    )
    nodes = [trigger_leaf, target_leaf, threshold, windows, aggregate]
    edges = [
        WorkflowEdge(
            source_node_id="leaf_trigger",
            target_node_id="threshold",
            target_input_slot="series",
        ),
        WorkflowEdge(
            source_node_id="threshold",
            target_node_id="windows",
            target_input_slot="events",
        ),
        WorkflowEdge(
            source_node_id="leaf_target",
            target_node_id="windows",
            target_input_slot="target",
        ),
        WorkflowEdge(
            source_node_id="windows",
            target_node_id="aggregate",
            target_input_slot="panel",
        ),
    ]
    return ShapeSpec(
        workflow_id="golden_event_regime_conditional_aggregate",
        nodes=nodes,
        edges=edges,
        literal_bindings=[],
        terminal_node_id="aggregate",
    )


# ============================================================================
# GOLDEN #6 — TRANSFORM (rolling z-score)
# ============================================================================
#
# Shape: 1 leaf -> rolling_zscore -> Series.  The canonical
# single-series transform shape.  Demonstrates that NOT every shape
# needs align_series / select_from_series_set — a 1-leaf transform is
# the "where am I vs my own history" pattern.


def _build_rolling_zscore_shape() -> ShapeSpec:
    leaf = _leaf_hole(
        node_id="leaf_input",
        domain_hint="sovereign_bonds",
        semantic_role="input_series",
        requested_output_meaning=(
            "input series to standardise against its own trailing "
            "history"
        ),
        nl_intent=(
            "fetch the input Series whose rolling z-score the user "
            "wants"
        ),
    )
    zscore = OperatorNode(
        node_id="rolling_zscore",
        operator_name="rolling_zscore",
        params={"window": 252},
    )
    edges = [
        WorkflowEdge(
            source_node_id="leaf_input",
            target_node_id="rolling_zscore",
            target_input_slot="series",
        ),
    ]
    return ShapeSpec(
        workflow_id="golden_transform_rolling_zscore",
        nodes=[leaf, zscore],
        edges=edges,
        literal_bindings=[],
        terminal_node_id="rolling_zscore",
    )


# ============================================================================
# PUBLIC SURFACE
# ============================================================================


GOLDEN_RELATIONSHIP_CORRELATION: ShapeSpec = _build_correlation_shape()
GOLDEN_RELATIONSHIP_ROLLING_CORRELATION: ShapeSpec = _build_rolling_correlation_shape()
GOLDEN_COINTEGRATION: ShapeSpec = _build_cointegration_shape()
GOLDEN_REGRESSION_ROLLING_BETA: ShapeSpec = _build_rolling_regression_shape()
GOLDEN_EVENT_REGIME: ShapeSpec = _build_event_regime_shape()
GOLDEN_TRANSFORM_ROLLING_ZSCORE: ShapeSpec = _build_rolling_zscore_shape()


# Map IntentTag → preferred golden shape.  When two IntentTags share a
# shape family (RELATIONSHIP has both correlation and rolling_correlation
# variants), the prompt enumerates both; this map gives the Composer's
# tests a canonical "what shape best matches this intent" expectation.
GOLDEN_SHAPES_BY_INTENT: Dict[IntentTag, ShapeSpec] = {
    IntentTag.RELATIONSHIP: GOLDEN_RELATIONSHIP_CORRELATION,
    IntentTag.COINTEGRATION: GOLDEN_COINTEGRATION,
    IntentTag.REGRESSION: GOLDEN_REGRESSION_ROLLING_BETA,
    IntentTag.EVENT_REGIME: GOLDEN_EVENT_REGIME,
    IntentTag.TRANSFORM: GOLDEN_TRANSFORM_ROLLING_ZSCORE,
}


# Sequence the prompt renderer iterates over (in this order).  The
# variant pairs (correlation + rolling_correlation) are listed
# together so the Composer sees the contrast in one cache block.
GOLDEN_SHAPES: List[ShapeSpec] = [
    GOLDEN_RELATIONSHIP_CORRELATION,
    GOLDEN_RELATIONSHIP_ROLLING_CORRELATION,
    GOLDEN_COINTEGRATION,
    GOLDEN_REGRESSION_ROLLING_BETA,
    GOLDEN_EVENT_REGIME,
    GOLDEN_TRANSFORM_ROLLING_ZSCORE,
]


# ============================================================================
# PROMPT RENDERER — ShapeSpec → deterministic text block
# ============================================================================


def _node_dict(node) -> Dict[str, Any]:
    """Render one ShapeNode as a small, prompt-friendly dict.  Discards
    Pydantic-internal extras; emits in declaration order so the JSON is
    byte-stable."""
    if isinstance(node, LeafHole):
        req = node.leaf_request
        return {
            "kind": "leaf_hole",
            "node_id": node.node_id,
            "leaf_request": {
                "required_artifact_type": req.required_artifact_type.value,
                "expected_units": (
                    req.expected_units.value if req.expected_units else None
                ),
                "expected_frequency": (
                    req.expected_frequency.value
                    if req.expected_frequency else None
                ),
                "domain_hint": req.domain_hint,
                "semantic_role": req.semantic_role,
                "requested_output_meaning": req.requested_output_meaning,
                "nl_intent": req.nl_intent,
            },
        }
    # OperatorNode
    return {
        "kind": "operator",
        "node_id": node.node_id,
        "operator_name": node.operator_name,
        "params": dict(node.params),
    }


def _edge_dict(edge: WorkflowEdge) -> Dict[str, str]:
    return {
        "source_node_id": edge.source_node_id,
        "target_node_id": edge.target_node_id,
        "target_input_slot": edge.target_input_slot,
    }


def _literal_dict(binding: LiteralBinding) -> Dict[str, Any]:
    return {
        "target_node_id": binding.target_node_id,
        "target_input_slot": binding.target_input_slot,
        "value": binding.value,
    }


def render_shape_for_prompt(shape: ShapeSpec) -> str:
    """Render one ShapeSpec as a deterministic JSON block (ShapeSpec
    shape — single discriminated ``nodes`` list).

    Kept for backward-compat with PR-7's tests; the **Composer
    few-shots** use ``render_shape_as_composer_output`` (see below)
    which mirrors the FLAT ``ComposerLLMOutput`` schema the LLM must
    emit.
    """
    payload: Dict[str, Any] = {
        "workflow_id": shape.workflow_id,
        "nodes": [_node_dict(n) for n in shape.nodes],
        "edges": [_edge_dict(e) for e in shape.edges],
        "literal_bindings": [
            _literal_dict(b) for b in shape.literal_bindings
        ],
        "terminal_node_id": shape.terminal_node_id,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _leaf_hole_decl_dict(node: LeafHole) -> Dict[str, Any]:
    """Render one LeafHole as a ``_LeafHoleDecl``-shaped dict (no
    ``kind`` discriminator; flattened LeafRequest fields)."""
    req = node.leaf_request
    return {
        "node_id": node.node_id,
        "required_artifact_type": req.required_artifact_type.value,
        "expected_units": (
            req.expected_units.value if req.expected_units else None
        ),
        "expected_frequency": (
            req.expected_frequency.value if req.expected_frequency else None
        ),
        "domain_hint": req.domain_hint,
        "semantic_role": req.semantic_role,
        "requested_output_meaning": req.requested_output_meaning,
        "nl_intent": req.nl_intent,
    }


def _operator_node_decl_dict(node: OperatorNode) -> Dict[str, Any]:
    """Render one OperatorNode as an ``_OperatorNodeDecl``-shaped dict
    (no ``kind`` discriminator)."""
    return {
        "node_id": node.node_id,
        "operator_name": node.operator_name,
        "params": dict(node.params),
    }


def render_shape_as_composer_output(shape: ShapeSpec) -> str:
    """Render one ShapeSpec in **flat ComposerLLMOutput JSON shape** —
    the exact schema the Composer LLM is expected to emit.

    Per PR-7A Codex F3: the prior PR-7 few-shots showed the LLM
    ShapeSpec-shaped JSON (single discriminated ``nodes`` list with
    ``kind`` discriminators), but the LLM's structured-output schema
    (``ComposerLLMOutput``) is FLAT — ``leaf_holes`` and
    ``operator_nodes`` as separate top-level lists.  That mismatch
    would teach the model to emit an inconsistent payload.  This
    renderer mirrors the LLM-output schema exactly so the few-shots
    serve as the model's wire-format ground truth.

    Deterministic (sorted keys) — cache-friendly.
    """
    leaf_holes: List[Dict[str, Any]] = []
    operator_nodes: List[Dict[str, Any]] = []
    for n in shape.nodes:
        if isinstance(n, LeafHole):
            leaf_holes.append(_leaf_hole_decl_dict(n))
        else:
            operator_nodes.append(_operator_node_decl_dict(n))
    payload: Dict[str, Any] = {
        "workflow_id": shape.workflow_id,
        "leaf_holes": leaf_holes,
        "operator_nodes": operator_nodes,
        "edges": [_edge_dict(e) for e in shape.edges],
        "literal_bindings": [
            _literal_dict(b) for b in shape.literal_bindings
        ],
        "terminal_node_id": shape.terminal_node_id,
        "refusal": None,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def render_golden_few_shots() -> str:
    """Render every golden shape as a deterministic concatenated text
    block the Composer's system prompt embeds verbatim.  Wraps each
    shape in a labelled banner so the LLM can see where each example
    begins / ends.

    Per PR-7A Codex F3: emits the FLAT ``ComposerLLMOutput`` shape
    (matches the LLM's structured-output schema) — NOT the older
    ShapeSpec discriminated-union shape.
    """
    blocks: List[str] = []
    for i, shape in enumerate(GOLDEN_SHAPES, start=1):
        blocks.append(f"--- GOLDEN SHAPE #{i}: {shape.workflow_id} ---")
        blocks.append(render_shape_as_composer_output(shape))
    return "\n".join(blocks)


__all__ = [
    "GOLDEN_RELATIONSHIP_CORRELATION",
    "GOLDEN_RELATIONSHIP_ROLLING_CORRELATION",
    "GOLDEN_COINTEGRATION",
    "GOLDEN_REGRESSION_ROLLING_BETA",
    "GOLDEN_EVENT_REGIME",
    "GOLDEN_TRANSFORM_ROLLING_ZSCORE",
    "GOLDEN_SHAPES_BY_INTENT",
    "GOLDEN_SHAPES",
    "render_shape_for_prompt",
    "render_shape_as_composer_output",
    "render_golden_few_shots",
]

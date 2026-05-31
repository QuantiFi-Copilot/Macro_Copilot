"""orchestrator.open_dag.dag_echo — PR-8 of the open-DAG PoC.

Pure deterministic English description of an assembled ``Workflow``
+ its ``BoundLeaf`` bindings.  No LLM.  No randomness.  No
network calls.  Used by the Boundary B Coverage Gate (PR-8) as
deterministic input alongside the original user prompt.

What the echo carries
=====================

For each leaf-substituted PrimitiveNode the gate sees:
  - the leaf's BoundLeaf-declared ``semantic_role``,
  - the artifact type the bind produced,
  - the leaf's domain (NOT the primitive's tool_name — see below),
  - the leaf's BoundLeaf-declared ``output_meaning`` (free-form
    English).

For each operator node the gate sees:
  - the operator's name + its key params (closed-substrate vocabulary
    L3 already authored — operators are L3's vocabulary).

The terminal node + its output artifact type close the description so
the gate can compare "what the DAG produces" against "what the prompt
asked for".

Primitive-blindness in the echo
===============================

The echo INTENTIONALLY surfaces ``semantic_role`` + ``output_meaning``
+ ``declared_units`` + ``declared_frequency`` instead of the primitive's
``tool_name`` / ``output_field``.  The gate is a coverage check, not a
selector audit — it needs to know WHAT the DAG produces (in finance-
blind English authored by the selector), not WHICH primitive produced
it.  Keeps the L3 primitive-blindness discipline (P11) intact even at
the post-assembly inspection surface.

The ``domain`` IS surfaced (it's the closed-family routing key from
the L1 router; the gate uses it to detect under-scoped routing when
the prompt mentions a domain the assembly didn't cover).

Determinism + cache-friendliness
================================

The echo is byte-stable for a given (Workflow, leaves) input:
  - leaves iterated by leaf_id sort order (NOT insertion order — the
    Assembler may have re-substituted during repair).
  - operators iterated by node_id sort order.
  - params dict iterated by sorted keys.
  - terminal description always last.

This lets the Anthropic prompt cache pin the gate's system prompt
without churning when the same DAG is re-evaluated.

Finance-blindness
=================

This module sits in ``orchestrator/open_dag/``.  It does NOT import
from ``rates_agent/`` and it does NOT name any specific instrument.
The echo's vocabulary is L3's (operator names + artifact types +
semantic_role English) and L1's (domain enum values).  Both are
closed-family substrates.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.open_dag.contracts import BoundLeaf
from shared.workflow.types import (
    OperatorNode,
    PrimitiveNode,
    Workflow,
)


# ============================================================================
# ECHO PAYLOAD — the structured intermediate used by render_dag_echo
# ============================================================================


class _LeafEcho(BaseModel):
    """Per-leaf echo entry.  Surfaces the BoundLeaf-declared free-form
    English the gate uses to reason about coverage, plus the closed-
    substrate fields (artifact type / units / frequency / domain) the
    gate compares against the prompt's stated intent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    leaf_id: str = Field(..., min_length=1)
    domain: str = Field(
        ...,
        min_length=1,
        description=(
            "Closed-family domain (KNOWN_DOMAINS).  The gate compares "
            "this against the prompt's domain vocabulary to detect "
            "under-scoped routing."
        ),
    )
    artifact_type: str = Field(..., min_length=1)
    units: Optional[str] = Field(
        default=None,
        description=(
            "Closed-family TimeSeriesUnits value, or None when the "
            "primitive's declared units were not pinned."
        ),
    )
    frequency: Optional[str] = Field(
        default=None,
        description=(
            "Closed-family Frequency value (daily / weekly / monthly), "
            "or None when not declared."
        ),
    )
    semantic_role: str = Field(
        ...,
        description=(
            "BoundLeaf.declared_semantic_role — selector's free-form "
            "tag.  Used by the gate as the leaf's English identity."
        ),
    )
    output_meaning: str = Field(
        ...,
        description=(
            "BoundLeaf.declared_output_meaning — one-sentence English "
            "describing what the bound primitive produces.  This is "
            "the gate's primary signal for 'what does this leaf "
            "represent in the user's prompt?'."
        ),
    )


class _OperatorEcho(BaseModel):
    """Per-operator echo entry.  Operators are L3's vocabulary — the
    gate sees their names + params verbatim."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    node_id: str = Field(..., min_length=1)
    operator_name: str = Field(..., min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)


class _EdgeEcho(BaseModel):
    """Per-edge echo entry."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_node_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    target_input_slot: str = Field(..., min_length=1)


class DagEcho(BaseModel):
    """Structured payload the renderer builds before producing the
    final English string.  Public so callers (the gate's tests, the
    lineage layer in PR-9) can introspect the echo programmatically
    without re-parsing the string form.

    Frozen.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = Field(..., min_length=1)
    leaves: List[_LeafEcho] = Field(default_factory=list)
    operators: List[_OperatorEcho] = Field(default_factory=list)
    edges: List[_EdgeEcho] = Field(default_factory=list)
    terminal_node_id: str = Field(..., min_length=1)
    terminal_artifact_type: str = Field(
        ...,
        min_length=1,
        description=(
            "Closed-family artifact type the terminal node emits.  For "
            "an operator terminal, that's the operator's OutputDescriptor "
            "artifact_type.  For a (degenerate) primitive terminal, the "
            "leaf's declared artifact type.  Lets the gate verify the "
            "DAG's output shape matches the prompt's stated intent."
        ),
    )
    terminal_kind: str = Field(
        ...,
        min_length=1,
        description="One of 'operator' or 'primitive'.",
    )
    terminal_label: str = Field(
        ...,
        min_length=1,
        description=(
            "Human-readable label.  For operator terminals: the "
            "operator_name.  For primitive terminals: the leaf's "
            "semantic_role + ' (leaf)' to avoid leaking tool_name."
        ),
    )


# ============================================================================
# PUBLIC ENTRY POINT — build the typed echo
# ============================================================================


def build_dag_echo(
    workflow: Workflow,
    leaves: Sequence[BoundLeaf],
) -> DagEcho:
    """Build a typed ``DagEcho`` from an assembled Workflow + its
    BoundLeaf list.

    Inputs
    ------
    workflow :
        The CLEAN Workflow from ``AssemblyResult.workflow`` (PR-4).
        Must have all leaves substituted (LeafHoles replaced by
        PrimitiveNode instances).
    leaves :
        The BoundLeaf list the Assembler used for substitution.  The
        echo indexes them by ``leaf_id`` to surface BoundLeaf-declared
        free-form English and closed-substrate declarations on each
        leaf.  Refusals are skipped (the gate's input is the
        ``CLEAN`` outcome — refusals never reach here per
        AssemblyResult's invariant).

    Returns
    -------
    DagEcho — frozen structured payload.  Determinism is in the
    iteration order: leaves sorted by leaf_id, operators by node_id,
    edges by (source, target, slot).
    """
    # Index leaves so we can look up declared fields per primitive node.
    leaves_by_id: Dict[str, BoundLeaf] = {
        l.leaf_id: l for l in leaves if not l.is_refusal
    }

    # Build leaf echoes from the assembled Workflow's PrimitiveNodes
    # (one per substituted hole).  Iterate by node_id sort order so the
    # output is byte-stable.
    leaf_echoes: List[_LeafEcho] = []
    for node in sorted(workflow.nodes, key=lambda n: n.node_id):
        if not isinstance(node, PrimitiveNode):
            continue
        bound = leaves_by_id.get(node.node_id)
        if bound is None:
            # Defensive — should not happen.  AssemblyResult.CLEAN
            # invariant guarantees every PrimitiveNode came from a
            # successfully-bound leaf.  Skip silently rather than
            # crashing the echo (the gate's downstream callers can
            # detect missing leaves via a separate assert if needed).
            continue
        leaf_echoes.append(_LeafEcho(
            leaf_id=node.node_id,
            domain=bound.domain,
            artifact_type=bound.declared_output_artifact_type.value,
            units=(
                bound.declared_units.value
                if bound.declared_units is not None else None
            ),
            frequency=(
                bound.declared_frequency.value
                if bound.declared_frequency is not None else None
            ),
            semantic_role=bound.declared_semantic_role,
            output_meaning=bound.declared_output_meaning,
        ))

    # Operator echoes — by node_id sort order.
    operator_echoes: List[_OperatorEcho] = []
    for node in sorted(workflow.nodes, key=lambda n: n.node_id):
        if not isinstance(node, OperatorNode):
            continue
        operator_echoes.append(_OperatorEcho(
            node_id=node.node_id,
            operator_name=node.operator_name,
            params={k: node.params[k] for k in sorted(node.params)},
        ))

    # Edges — by (source, target, slot) sort order.
    edge_echoes: List[_EdgeEcho] = []
    for edge in sorted(
        workflow.edges,
        key=lambda e: (e.source_node_id, e.target_node_id, e.target_input_slot),
    ):
        edge_echoes.append(_EdgeEcho(
            source_node_id=edge.source_node_id,
            target_node_id=edge.target_node_id,
            target_input_slot=edge.target_input_slot,
        ))

    # Terminal: name its kind + artifact type.  For operator terminals,
    # resolve the artifact type from the substrate's OPERATOR_REGISTRY
    # (the canonical source of truth — not duplicated here).  For
    # primitive terminals (degenerate 1-leaf lookup), use the leaf's
    # declared artifact type.
    from shared.workflow.registry import OPERATOR_REGISTRY

    terminal_node = workflow.node_by_id(workflow.terminal_node_id)
    if isinstance(terminal_node, OperatorNode):
        spec = OPERATOR_REGISTRY.get(terminal_node.operator_name)
        if spec is None:
            # Defensive — operator validator would have already caught
            # an unknown operator.  Fall back to a sentinel label so
            # the echo never crashes.
            terminal_artifact_type = "Unknown"
        else:
            terminal_artifact_type = spec.output.artifact_type.value
        terminal_label = terminal_node.operator_name
        terminal_kind = "operator"
    elif isinstance(terminal_node, PrimitiveNode):
        bound = leaves_by_id.get(terminal_node.node_id)
        terminal_artifact_type = (
            bound.declared_output_artifact_type.value
            if bound is not None else "Unknown"
        )
        # Primitive-blindness — don't leak tool_name.  Surface the
        # leaf's semantic_role as the human-readable label.
        terminal_label = (
            f"{bound.declared_semantic_role} (leaf)"
            if bound is not None
            else f"{terminal_node.node_id} (leaf)"
        )
        terminal_kind = "primitive"
    else:  # defensive
        terminal_artifact_type = "Unknown"
        terminal_label = terminal_node.node_id
        terminal_kind = "unknown"

    return DagEcho(
        workflow_id=workflow.workflow_id,
        leaves=leaf_echoes,
        operators=operator_echoes,
        edges=edge_echoes,
        terminal_node_id=workflow.terminal_node_id,
        terminal_artifact_type=terminal_artifact_type,
        terminal_kind=terminal_kind,
        terminal_label=terminal_label,
    )


# ============================================================================
# RENDERER — DagEcho → deterministic English string
# ============================================================================


def render_dag_echo(echo: DagEcho) -> str:
    """Render a ``DagEcho`` as a deterministic English description the
    Coverage Gate's LLM consumes verbatim.

    Format (stable; cache-friendly):

        DAG ECHO (workflow_id=<wid>):

        INPUT LEAVES (<N>):
          - leaf_id=<id>: <semantic_role> — <output_meaning>
            domain=<d>; artifact_type=<a>; units=<u>; frequency=<f>

        OPERATORS (<M>):
          - <node_id>: <operator_name>(<sorted_params>)

        EDGES (<E>):
          - <source> -> <target> on slot '<slot>'

        TERMINAL:
          <terminal_label> (kind=<operator|primitive>, output=<artifact_type>)

    The gate's English-reasoning step takes this verbatim and compares
    it against the original prompt.
    """
    lines: List[str] = []
    lines.append(f"DAG ECHO (workflow_id={echo.workflow_id}):")
    lines.append("")
    lines.append(f"INPUT LEAVES ({len(echo.leaves)}):")
    for leaf in echo.leaves:
        lines.append(
            f"  - leaf_id={leaf.leaf_id}: {leaf.semantic_role} — "
            f"{leaf.output_meaning}"
        )
        lines.append(
            f"    domain={leaf.domain}; "
            f"artifact_type={leaf.artifact_type}; "
            f"units={leaf.units if leaf.units else 'unspecified'}; "
            f"frequency={leaf.frequency if leaf.frequency else 'unspecified'}"
        )
    lines.append("")
    lines.append(f"OPERATORS ({len(echo.operators)}):")
    for op in echo.operators:
        params_str = ", ".join(
            f"{k}={op.params[k]!r}" for k in op.params
        ) if op.params else ""
        lines.append(f"  - {op.node_id}: {op.operator_name}({params_str})")
    lines.append("")
    lines.append(f"EDGES ({len(echo.edges)}):")
    for edge in echo.edges:
        lines.append(
            f"  - {edge.source_node_id} -> {edge.target_node_id} "
            f"on slot {edge.target_input_slot!r}"
        )
    lines.append("")
    lines.append("TERMINAL:")
    lines.append(
        f"  {echo.terminal_label} (kind={echo.terminal_kind}, "
        f"output={echo.terminal_artifact_type})"
    )
    return "\n".join(lines)


def build_and_render_dag_echo(
    workflow: Workflow,
    leaves: Sequence[BoundLeaf],
) -> str:
    """Convenience: build the typed echo then render it.  Equivalent to
    ``render_dag_echo(build_dag_echo(workflow, leaves))``."""
    return render_dag_echo(build_dag_echo(workflow, leaves))


__all__ = [
    "DagEcho",
    "build_dag_echo",
    "render_dag_echo",
    "build_and_render_dag_echo",
]

"""shared.workflow.types — DAG schema for workflow templates.

The substrate's typed shape for workflow templates.  Per
``docs/architecture/workflow_architecture.md``, this layer is
finance-blind: it knows about typed artifacts, edge contracts, and
node kinds, but NOT about specific primitives, instruments, or
analysis archetypes.  Templates (``rates_agent/workflows/``) and
agent-side primitive resolvers carry the finance-aware mapping.

Closed-family discipline
------------------------
Two node kinds — ``PrimitiveNode`` and ``OperatorNode`` — composed
into a discriminated union (``WorkflowNode``).  Adding a new node
kind requires editing this file AND the discriminator union AND
the corresponding executor dispatch path.  Same closed-family
discipline as ``LineageStep`` and ``MissingnessPolicy``.

Edges are explicit and typed
----------------------------
``WorkflowEdge`` declares ``source_node_id`` → ``target_node_id``
binding, with the ``target_input_slot`` naming which input on the
target operator/primitive receives the artifact.  Multiple edges
with the same ``target_input_slot`` aggregate into a list at
execution time — this is how ``align_series([a, b, c])`` works
without special-casing the substrate for list-shaped inputs.

Slot binding model
------------------
Workflow templates (PR 4 — separate layer) own slot schemas.  At
this substrate layer, node ``params`` are concrete dicts; the
template layer is responsible for substituting any
``{"$slot": "name"}`` placeholders BEFORE handing the workflow to
the executor.  Keeps the substrate's responsibilities narrow.
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ============================================================================
# NODE KINDS (closed family)
# ============================================================================


class PrimitiveNode(BaseModel):
    """A workflow node that invokes a primitive (a per-tool-folder
    tool under ``rates_agent/<domain>/tools/<tool>/``).

    The substrate does NOT know which primitives exist — the
    executor uses a caller-supplied ``PrimitiveResolver`` protocol
    (see ``shared.workflow.registry``) to look up the tool by name.
    This is what keeps ``shared/workflow/`` finance-blind.

    Fields
    ------
    node_id :
        Workflow-local identifier; must be unique within the
        Workflow.  Used by ``WorkflowEdge.source_node_id`` /
        ``target_node_id``.
    tool_name :
        The MCP tool name (e.g. ``"calculate_swap_spread_tool"``).
        Resolves via the executor's ``PrimitiveResolver``.
    output_field :
        Which ``time_series*`` field of the primitive's output to
        lift via the bridge into a typed ``Series`` artifact.
        E.g. ``"time_series_spread"``, ``"time_series_zscore"``,
        ``"time_series"``.
    params :
        Concrete parameters for the primitive's ``*Input`` schema.
        Substrate does NOT validate these — the primitive's own
        Pydantic ``*Input`` validator catches schema errors at
        execution time.  Templates substitute slot values into this
        dict before passing the workflow to the executor.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["primitive"] = "primitive"
    node_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    output_field: str = Field(..., min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)


class OperatorNode(BaseModel):
    """A workflow node that invokes a central operator
    (``shared/operators/<operator>/``).

    The substrate maintains a closed registry of operators
    (see ``shared.workflow.registry.OPERATOR_REGISTRY``); the
    executor dispatches by ``operator_name``.  Adding a new
    operator requires editing the registry AND passing the
    operator's admission checklist
    (``operator_architecture.md``).

    Fields
    ------
    node_id :
        Workflow-local unique identifier.
    operator_name :
        Closed-enum operator name (must be a key in
        ``OPERATOR_REGISTRY``).  Validated at workflow-construction
        time so a typo surfaces before execution.
    params :
        Operator-specific parameters (passed to the operator's
        ``*Params`` schema).  Substrate does NOT validate — the
        operator's own ``*Params`` validator catches errors at
        execution time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["operator"] = "operator"
    node_id: str = Field(..., min_length=1)
    operator_name: str = Field(..., min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)


# Discriminated union over the closed family.  Pydantic picks the
# right concrete class by ``kind`` on deserialization, so a
# Workflow round-trips through JSON without losing type information.
WorkflowNode = Annotated[
    Union[PrimitiveNode, OperatorNode],
    Field(discriminator="kind"),
]


# ============================================================================
# EDGE
# ============================================================================


class WorkflowEdge(BaseModel):
    """A directed edge in the workflow DAG: an artifact handoff
    from one node's output to another node's named input slot.

    Multiple edges with the same ``target_node_id`` +
    ``target_input_slot`` aggregate into a LIST at execution time,
    in declaration order — this is how list-shaped operator inputs
    (e.g. ``align_series(series_list=[a, b, c])``) are expressed
    without special-casing the substrate.

    Fields
    ------
    source_node_id :
        Which node produces the artifact.
    target_node_id :
        Which node consumes it.
    target_input_slot :
        Which named input slot on the target operator/primitive
        receives the artifact.  Operator slots are defined by the
        operator's signature (e.g. ``align_series`` has
        ``series_list``; ``series_arithmetic`` has ``left`` +
        ``right``; ``threshold_events`` has ``series``;
        ``event_windows`` has ``events`` + ``target``;
        ``conditional_aggregate`` has ``panel``).  Primitive nodes
        do not consume node-output edges in v1 (primitives fetch
        from DB themselves); the slot remains an open string for
        future composite primitives.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_node_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    target_input_slot: str = Field(..., min_length=1)


# ============================================================================
# WORKFLOW
# ============================================================================


class Workflow(BaseModel):
    """A typed DAG of primitive + operator nodes producing a
    typed terminal artifact.

    The substrate's executable representation.  Templates
    (PR 4) build Workflow instances by substituting slot values
    into a template-locked DAG shape; the substrate does not know
    about templates or slots.

    Fields
    ------
    workflow_id :
        Stable identifier for the workflow (e.g.
        ``"event_study"``, or a template-instance-specific
        identifier set by the template layer).
    nodes :
        All nodes in the DAG, in any order.  Node IDs must be
        unique.
    edges :
        All directed artifact-handoff edges.  Cycle-free
        (validator catches cycles).  Each edge's source and
        target must reference real nodes.
    terminal_node_id :
        Which node's output is the workflow's terminal artifact.
        Validator confirms this references a real node.

    Validators
    ----------
    Frozen + ``extra="forbid"``.  Per-field structural checks
    (uniqueness, references) live in
    ``shared.workflow.validate.validate_workflow`` so they can be
    invoked separately from construction (e.g. for static analysis
    of templates).  The model-level validator below catches the
    most basic shape errors at construction time so a malformed
    Workflow cannot be built at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = Field(..., min_length=1)
    nodes: List[WorkflowNode] = Field(..., min_length=1)
    edges: List[WorkflowEdge] = Field(default_factory=list)
    terminal_node_id: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_basic_shape(self) -> "Workflow":
        """Construction-time gate.  Catches the most basic shape
        errors immediately so a malformed Workflow cannot be built
        at all.  Deeper structural validation (cycles, slot
        existence, type compatibility) runs in
        ``shared.workflow.validate.validate_workflow``."""
        # 1. Node IDs unique.
        node_ids = [n.node_id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            duplicates = sorted(
                {nid for nid in node_ids if node_ids.count(nid) > 1}
            )
            raise ValueError(
                f"Workflow {self.workflow_id!r}: duplicate node_id(s) "
                f"{duplicates}; every node must have a unique node_id."
            )

        # 2. terminal_node_id references a real node.
        if self.terminal_node_id not in node_ids:
            raise ValueError(
                f"Workflow {self.workflow_id!r}: terminal_node_id="
                f"{self.terminal_node_id!r} does not match any node "
                f"in this workflow.  Known node IDs: {sorted(node_ids)}."
            )

        # 3. Every edge references real nodes.
        node_id_set = set(node_ids)
        for edge in self.edges:
            if edge.source_node_id not in node_id_set:
                raise ValueError(
                    f"Workflow {self.workflow_id!r}: edge references "
                    f"unknown source_node_id={edge.source_node_id!r}.  "
                    f"Known node IDs: {sorted(node_id_set)}."
                )
            if edge.target_node_id not in node_id_set:
                raise ValueError(
                    f"Workflow {self.workflow_id!r}: edge references "
                    f"unknown target_node_id={edge.target_node_id!r}.  "
                    f"Known node IDs: {sorted(node_id_set)}."
                )

        return self

    def node_by_id(self, node_id: str) -> WorkflowNode:
        """Return the node with the given ``node_id``, or raise
        KeyError if no such node exists.  Convenience accessor for
        the validator + executor."""
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(
            f"Workflow {self.workflow_id!r} has no node with "
            f"node_id={node_id!r}."
        )


__all__ = [
    "PrimitiveNode",
    "OperatorNode",
    "WorkflowNode",
    "WorkflowEdge",
    "Workflow",
]

"""shared.workflow.template_card — LLM-readable template descriptors.

Per ``docs/architecture/workflow_architecture.md``, each template
exposes a "template card" derived from its declared shape.  The
card is the LLM-facing surface for template selection — analog of
a primitive's MCP tool description, but for workflow templates.

Card contents
-------------
- ``template_id`` (echo)
- ``archetype`` (closed-enum routing key)
- ``description`` (one-line human-readable)
- ``slot_schema`` (typed slot declarations the LLM fills)
- ``terminal_artifact_type`` (closed-enum artifact-type name —
  what the workflow ultimately produces, derived from the
  registry's known operator output_type for the terminal node OR
  hardcoded ``"Series"`` for primitive-terminal templates since
  the bridge always produces Series)
- ``primitives_used`` (sorted list of MCP tool names the template
  invokes — useful for the LLM's "which template can answer this
  prompt" classifier)
- ``operators_used`` (sorted list of operator names the template
  invokes)
- ``node_count`` (substrate-shape diagnostic)
- ``edge_count`` (substrate-shape diagnostic)
- ``archetype_signature`` (the structural cues the
  ``route_to_template`` LLM step matches against prompts; per
  the workflow-architecture spec's "Template-selection
  contract" section)

The card is INTENTIONALLY the same shape across all templates so
the LLM template-selection layer can iterate over the catalogue
uniformly (no per-template branching).  Same discipline as
primitives' MCP tool descriptions — uniform schema, varying
content.

What the card does NOT include
------------------------------
- The full DAG topology — too verbose for prompt-context budgets.
  Available via ``WorkflowTemplate.nodes`` / ``edges`` for tools
  that need it.
- Operator parameters — template-locked, not LLM-controlled.
  Available via the structured template object.
- Lineage chain hash — N/A at template-shape level (only concrete
  ``Workflow.bind()`` outputs have lineage).
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, ConfigDict, Field

from shared.workflow.registry import OPERATOR_REGISTRY
from shared.workflow.template import (
    OperatorNodeTemplate,
    PrimitiveNodeTemplate,
    SlotDeclaration,
    WorkflowArchetype,
    WorkflowTemplate,
)


class TemplateCard(BaseModel):
    """LLM-readable descriptor of a workflow template.

    Frozen + ``extra="forbid"`` — same discipline as the bridge's
    primitive cards.  Card contents are derived from the
    template's declared shape; cards do not carry runtime state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str = Field(..., min_length=1)
    archetype: WorkflowArchetype
    description: str = Field(..., min_length=1)
    slot_schema: List[SlotDeclaration]
    terminal_artifact_type: str = Field(..., min_length=1)
    primitives_used: List[str] = Field(default_factory=list)
    operators_used: List[str] = Field(default_factory=list)
    node_count: int = Field(..., ge=1)
    edge_count: int = Field(..., ge=0)
    # Structural cues the future ``route_to_template`` LLM step
    # matches against prompts.  Echoed verbatim from the
    # template's declaration; cards do not synthesise cues.
    archetype_signature: List[str] = Field(default_factory=list)


def card_for_template(template: WorkflowTemplate) -> TemplateCard:
    """Derive a ``TemplateCard`` from a registered
    ``WorkflowTemplate``.

    Used by:
    - the LLM template-selection layer to render the catalogue
    - diagnostic tooling that wants a uniform template descriptor
    - any future REST/MCP surface exposing the template catalogue
    """
    # Inspect nodes for the primitives + operators the template
    # touches.  Sorted for stable card output across runs.
    #
    # ``PrimitiveNodeTemplate.tool_name`` is slot-substitutable
    # (per the instrument-agnostic discipline).  When tool_name
    # is a $slot reference, the actual primitive is decided at
    # bind time, NOT at template-author time — so the card
    # records ``"<via $slot:NAME>"`` rather than a concrete tool
    # name.  Operator names are NOT slot-substitutable
    # (topology-locked), so operators_used always contains
    # concrete names.
    primitives_used: set[str] = set()
    operators_used: set[str] = set()
    for node in template.nodes:
        if isinstance(node, PrimitiveNodeTemplate):
            tool_name = node.tool_name
            if isinstance(tool_name, str):
                primitives_used.add(tool_name)
            elif isinstance(tool_name, dict) and "$slot" in tool_name:
                primitives_used.add(f"<via $slot:{tool_name['$slot']}>")
        elif isinstance(node, OperatorNodeTemplate):
            operators_used.add(node.operator_name)

    # Resolve terminal artifact type.  PrimitiveNode terminal →
    # always "Series" (the bridge's invariant).  OperatorNode
    # terminal → look up in OPERATOR_REGISTRY.
    terminal_node = next(
        n for n in template.nodes if n.node_id == template.terminal_node_id
    )
    if isinstance(terminal_node, PrimitiveNodeTemplate):
        terminal_artifact_type = "Series"
    elif isinstance(terminal_node, OperatorNodeTemplate):
        spec = OPERATOR_REGISTRY.get(terminal_node.operator_name)
        if spec is None:
            # Unknown operator — defensive fallback.  The
            # template's construction validators should have
            # caught this already, but if it slips through we
            # surface the issue explicitly in the card.
            terminal_artifact_type = f"Unknown[{terminal_node.operator_name}]"
        else:
            # PART D migration: structured ``OutputDescriptor``
            # replaces the bare ``output_type: str`` field; the
            # closed-enum value is read off ``output.artifact_type``.
            terminal_artifact_type = spec.output.artifact_type.value
    else:
        terminal_artifact_type = "Unknown"

    return TemplateCard(
        template_id=template.template_id,
        archetype=template.archetype,
        description=template.description,
        slot_schema=list(template.slot_schema),
        terminal_artifact_type=terminal_artifact_type,
        primitives_used=sorted(primitives_used),
        operators_used=sorted(operators_used),
        node_count=len(template.nodes),
        edge_count=len(template.edges),
        archetype_signature=list(template.archetype_signature),
    )


__all__ = [
    "TemplateCard",
    "card_for_template",
]

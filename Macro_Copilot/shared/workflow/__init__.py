"""shared.workflow — typed DAG substrate for workflow templates.

Per ``docs/architecture/workflow_architecture.md``.  The substrate
sits below the workflow-template layer and above the operator +
primitive + bridge layers it composes.

Layering
--------
- primitives  (rates_agent/<domain>/tools/)  — finance-aware
- operators   (shared/operators/)             — finance-blind, structural
- bridge      (shared/artifacts/adapters/)    — primitives ↔ operators
- substrate   (shared/workflow/)              — typed DAG over the above
- templates   (rates_agent/workflows/)        — analysis archetypes (PR 4)

The substrate knows about typed artifacts, edge contracts, node
kinds, and operator dispatch.  It does NOT know about specific
primitives, instruments, or analysis archetypes — those live in
the agent + template layers and reach the substrate via a caller-
supplied ``PrimitiveResolver`` protocol.

Public API
----------
- ``Workflow``, ``PrimitiveNode``, ``OperatorNode``, ``WorkflowEdge``
  — typed DAG schema (closed-family discriminated union over node
  kinds).
- ``WorkflowResult`` — typed terminal artifact + lineage summary
  + intermediate node-artifact map.
- ``execute_workflow`` — the executor.  Validates the DAG, runs
  it in topological order, dispatches primitives via the
  resolver, lifts primitive outputs through the bridge,
  dispatches operators via the closed registry.
- ``validate_workflow`` — pre-execution structural validation
  (cycles, slot/type/unit compat).  Runs implicitly inside
  ``execute_workflow`` but exposed for static analysis of
  templates.
- ``WorkflowValidationError`` / ``WorkflowExecutionError`` —
  typed error families.
- ``OPERATOR_REGISTRY``, ``known_operators``, ``OperatorSpec`` —
  closed-family operator dispatch (substrate-internal but
  exposed for diagnostic / catalogue purposes).
- ``SlotDescriptor``, ``OutputDescriptor`` — structured per-slot
  and per-output metadata on ``OperatorSpec`` (replaces the prior
  string-encoded ``Dict[str, str]`` slots + bare ``output_type``
  + sibling ``accepts_scalar_input`` tuple).
- ``PrimitiveResolver``, ``PrimitiveSpec`` — caller-supplied
  primitive dispatch protocol.
- ``ARTIFACT_TYPE_NAMES``, ``artifact_type_name`` — closed enum
  for the artifact type names the substrate validates against.
"""

from shared.workflow.executor import (
    WorkflowExecutionError,
    execute_workflow,
)
from shared.workflow.registry import (
    ARTIFACT_TYPE_NAMES,
    OPERATOR_REGISTRY,
    OperatorSpec,
    PrimitiveResolver,
    PrimitiveSpec,
    artifact_type_name,
    known_operators,
)
from shared.workflow.slots import (
    OutputDescriptor,
    SlotDescriptor,
)
from shared.workflow.result import (
    TerminalArtifact,
    WorkflowResult,
)
from shared.workflow.types import (
    LiteralBinding,
    LiteralScalar,
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
)
from shared.workflow.template import (
    LiteralBindingTemplate,
    OperatorNodeTemplate,
    PrimitiveNodeTemplate,
    RelativeOrderConstraint,
    SlotBindingError,
    SlotConstraint,
    SlotDeclaration,
    WORKFLOW_ARCHETYPES,
    WorkflowArchetype,
    WorkflowNodeTemplate,
    WorkflowTemplate,
)
from shared.workflow.template_card import (
    TemplateCard,
    card_for_template,
)
from shared.workflow.template_loader import (
    WorkflowTemplateError,
    clear_workflow_template_cache,
    load_workflow_template,
)
from shared.workflow.template_registry import (
    TemplateRegistryError,
    clear_template_registry,
    get_template,
    known_archetypes,
    known_template_ids,
    list_templates,
    register_template,
    unregister_template,
)
from shared.workflow.validate import (
    WorkflowValidationError,
    topological_order,
    validate_workflow,
    validate_workflow_collect,
)
from shared.workflow.validation_result import (
    ErrorCode,
    OwnerLayer,
    ValidationError,
    ValidationResult,
)


__all__ = [
    # Schema
    "Workflow",
    "WorkflowNode",
    "PrimitiveNode",
    "OperatorNode",
    "WorkflowEdge",
    "LiteralBinding",
    "LiteralScalar",
    # Result
    "WorkflowResult",
    "TerminalArtifact",
    # Execution
    "execute_workflow",
    "WorkflowExecutionError",
    # Validation
    "validate_workflow",
    "validate_workflow_collect",
    "topological_order",
    "WorkflowValidationError",
    "ValidationResult",
    "ValidationError",
    "ErrorCode",
    "OwnerLayer",
    # Registry / dispatch
    "OPERATOR_REGISTRY",
    "OperatorSpec",
    "known_operators",
    "PrimitiveResolver",
    "PrimitiveSpec",
    "ARTIFACT_TYPE_NAMES",
    "artifact_type_name",
    "SlotDescriptor",
    "OutputDescriptor",
    # Template layer
    "WorkflowTemplate",
    "WorkflowArchetype",
    "WORKFLOW_ARCHETYPES",
    "SlotDeclaration",
    "SlotConstraint",
    "RelativeOrderConstraint",
    "PrimitiveNodeTemplate",
    "OperatorNodeTemplate",
    "WorkflowNodeTemplate",
    "LiteralBindingTemplate",
    "SlotBindingError",
    # Template loader
    "load_workflow_template",
    "clear_workflow_template_cache",
    "WorkflowTemplateError",
    # Template registry
    "register_template",
    "unregister_template",
    "get_template",
    "list_templates",
    "known_template_ids",
    "known_archetypes",
    "clear_template_registry",
    "TemplateRegistryError",
    # Template card
    "TemplateCard",
    "card_for_template",
]

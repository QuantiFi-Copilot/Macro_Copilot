"""orchestrator.open_dag — the open-DAG composition layer (PR-3 / PR-4 / PR-7+).

Lives ABOVE the finance-blind substrate (``shared/workflow/``) per the
plan's domain layering: this package carries domain-aware contracts,
the resolver-key collision adapter, the primitive-output composability
audit, and the bounded-repair Assembler.  None of it knows about
specific instruments (P11), but all of it can route on the closed
``KNOWN_DOMAINS`` set.

Per ``tmp/orchestration.md`` line 410: "These contracts live in
``orchestrator/open_dag/``, not ``shared/workflow/``, because they
contain domain routing hints and LLM-facing intent fields.  The
existing ``shared/workflow`` substrate remains finance-blind and only
sees the assembled executable ``Workflow``."

Public surface
==============

Contracts (PR-3):
  - ``LeafRequest`` / ``BoundLeaf`` / ``LeafHole`` / ``ShapeSpec`` /
    ``ShapeNode`` — typed contracts the Composer (L3) emits and the
    Selector (L2) fills.
  - ``Frequency`` — closed-substrate vocabulary for the leaf-contract
    frequency check.

Resolver-key adapter (PR-3):
  - ``KNOWN_DOMAINS`` / ``UnknownDomainError`` / ``domain_to_resolver_key``.

Primitive declarations (PR-3):
  - ``PrimitiveDeclaration`` / ``declare_primitive_output``.

Composability audit (PR-3 §2.5):
  - ``Composability`` / ``CompositionAuditEntry`` / ``classify_primitive``
    / ``audit_resolver`` / ``by_classification``.

Assembler + bounded repair (PR-4):
  - ``Assembler`` / ``AssemblyResult`` / ``AssemblyStatus`` /
    ``RepairKind`` / ``RepairStep`` / ``InsertAdapterNode`` /
    ``RewireEdge`` / ``ShapePatch`` / ``ShapePatchProvider`` /
    ``LeafRebinder``.
"""

from orchestrator.open_dag.assembler import (
    Assembler,
    AssemblyResult,
    AssemblyStatus,
    InsertAdapterNode,
    LeafRebinder,
    RepairKind,
    RepairStep,
    RewireEdge,
    ShapePatch,
    ShapePatchProvider,
)
from orchestrator.open_dag.composability_audit import (
    Composability,
    CompositionAuditEntry,
    audit_resolver,
    by_classification,
    classify_primitive,
)
from orchestrator.open_dag.composer import (
    Composer,
    ComposerLLMOutput,
    ComposerOutputError,
    ComposerRefusal,
    ComposerRepairLLMOutput,
    build_compose_system_prompt_text,
    build_repair_system_prompt_text,
    llm_output_to_shape_spec,
    llm_repair_output_to_patches,
    render_composer_repair_user_message,
    render_composer_user_message,
    render_operator_catalogue_block,
)
from orchestrator.open_dag.answer import (
    AnswerRenderer,
    assemble_final_answer,
    render_answer_user_message,
    render_clarification,
    render_intent_echo,
    render_provenance_footer,
    render_refusal,
)
from orchestrator.open_dag.coverage_gate import (
    CoverageGate,
    GateStatus,
    GateVerdict,
    render_gate_user_message,
    warnings_to_string_list,
)
from orchestrator.open_dag.intent_chain import (
    ComposerIntentRecord,
    GateIntentRecord,
    IntentChain,
    RouterIntentRecord,
    SelectorIntentRecord,
)
from orchestrator.open_dag.pipeline import (
    ExecutorCallback,
    OpenDagPipeline,
    PipelineOutcome,
    PipelineStatus,
    SelectorCallback,
)
from orchestrator.open_dag.run_record import (
    RunLineage,
    build_run_lineage,
)
from orchestrator.open_dag.dag_echo import (
    DagEcho,
    build_and_render_dag_echo,
    build_dag_echo,
    render_dag_echo,
)
from orchestrator.open_dag.composer_golden_shapes import (
    GOLDEN_COINTEGRATION,
    GOLDEN_EVENT_REGIME,
    GOLDEN_REGRESSION_ROLLING_BETA,
    GOLDEN_RELATIONSHIP_CORRELATION,
    GOLDEN_RELATIONSHIP_ROLLING_CORRELATION,
    GOLDEN_SHAPES,
    GOLDEN_SHAPES_BY_INTENT,
    GOLDEN_TRANSFORM_ROLLING_ZSCORE,
    render_golden_few_shots,
    render_shape_as_composer_output,
    render_shape_for_prompt,
)
from orchestrator.open_dag.contracts import (
    BoundLeaf,
    Frequency,
    LeafHole,
    LeafRequest,
    ShapeNode,
    ShapeSpec,
)
from orchestrator.open_dag.primitive_declarations import (
    PrimitiveDeclaration,
    declare_primitive_output,
)
from orchestrator.open_dag.resolver_keys import (
    KNOWN_DOMAINS,
    UnknownDomainError,
    domain_to_resolver_key,
)


__all__ = [
    # Contracts (PR-3)
    "Frequency",
    "LeafRequest",
    "BoundLeaf",
    "LeafHole",
    "ShapeNode",
    "ShapeSpec",
    # Resolver-key adapter (PR-3)
    "KNOWN_DOMAINS",
    "UnknownDomainError",
    "domain_to_resolver_key",
    # Primitive declarations (PR-3)
    "PrimitiveDeclaration",
    "declare_primitive_output",
    # Composability audit (PR-3 §2.5)
    "Composability",
    "CompositionAuditEntry",
    "classify_primitive",
    "audit_resolver",
    "by_classification",
    # Assembler (PR-4)
    "Assembler",
    "AssemblyResult",
    "AssemblyStatus",
    "RepairKind",
    "RepairStep",
    "InsertAdapterNode",
    "RewireEdge",
    "ShapePatch",
    "ShapePatchProvider",
    "LeafRebinder",
    # Composer (PR-7)
    "Composer",
    "ComposerLLMOutput",
    "ComposerOutputError",
    "ComposerRefusal",
    "ComposerRepairLLMOutput",
    "build_compose_system_prompt_text",
    "build_repair_system_prompt_text",
    "llm_output_to_shape_spec",
    "llm_repair_output_to_patches",
    "render_composer_repair_user_message",
    "render_composer_user_message",
    "render_operator_catalogue_block",
    # Golden shapes (PR-7 few-shots)
    "GOLDEN_COINTEGRATION",
    "GOLDEN_EVENT_REGIME",
    "GOLDEN_REGRESSION_ROLLING_BETA",
    "GOLDEN_RELATIONSHIP_CORRELATION",
    "GOLDEN_RELATIONSHIP_ROLLING_CORRELATION",
    "GOLDEN_SHAPES",
    "GOLDEN_SHAPES_BY_INTENT",
    "GOLDEN_TRANSFORM_ROLLING_ZSCORE",
    "render_golden_few_shots",
    "render_shape_as_composer_output",
    "render_shape_for_prompt",
    # Coverage gate (PR-8)
    "CoverageGate",
    "GateStatus",
    "GateVerdict",
    "render_gate_user_message",
    "warnings_to_string_list",
    # DAG echo (PR-8)
    "DagEcho",
    "build_dag_echo",
    "build_and_render_dag_echo",
    "render_dag_echo",
    # Intent chain (PR-9)
    "IntentChain",
    "RouterIntentRecord",
    "SelectorIntentRecord",
    "ComposerIntentRecord",
    "GateIntentRecord",
    # Answer renderer (PR-9)
    "AnswerRenderer",
    "assemble_final_answer",
    "render_answer_user_message",
    "render_clarification",
    "render_intent_echo",
    "render_provenance_footer",
    "render_refusal",
    # Run lineage (PR-9A — the IntentChain + Lineage join)
    "RunLineage",
    "build_run_lineage",
    # Pipeline (PR-10)
    "OpenDagPipeline",
    "PipelineOutcome",
    "PipelineStatus",
    "ExecutorCallback",
    "SelectorCallback",
]

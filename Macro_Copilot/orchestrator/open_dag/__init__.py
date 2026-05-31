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
]

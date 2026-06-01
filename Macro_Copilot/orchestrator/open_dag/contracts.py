"""shared.workflow.holes — hole/fill contracts for the open-DAG pipeline.

The substrate's typed contracts that let the L3 Composer emit a DAG
*shape with leaf-holes* before any L2 Selector binds a concrete
primitive.  This is the core mechanism that keeps R10/P11 isolation
intact: L3 sees only operators + artifact types (no primitives), L2
Selectors see only their own domain's primitives (no operators).

Four types
----------
- ``LeafRequest`` — what the L3 Composer declares per leaf-hole.
  Carries closed-substrate fields (artifact type, units, frequency,
  domain hint) and free-form fields (semantic_role,
  requested_output_meaning, nl_intent) that PR-4's Boundary A will
  cross-check against the matching ``BoundLeaf``.
- ``BoundLeaf`` — what an L2 Selector returns when binding a hole, or
  when refusing to bind it.  Carries the chosen primitive's identity
  in TWO forms (visible ``mcp_tool_name`` for audit + resolver-safe
  ``resolver_tool_key`` for executor dispatch) plus the Selector's
  declared output meta (re-asserted from the primitive's own
  PrimitiveSpec, so the Assembler can validate consistency without
  re-fetching).
- ``LeafHole`` — a placeholder node in a ``ShapeSpec``, in place of a
  ``PrimitiveNode``.  Carries the ``LeafRequest`` and the node_id the
  Composer assigned (which the BoundLeaf will reference via leaf_id).
- ``ShapeSpec`` — the Composer's emitted DAG-with-holes.  Same shape
  as ``Workflow`` but with ``LeafHole`` nodes in place of
  ``PrimitiveNode`` nodes.  Construction-time gate mirrors
  ``Workflow``'s (unique node_ids, terminal exists, edges reference
  real nodes, literal bindings target real nodes).

Field-family split (the role-discriminant nuance)
-------------------------------------------------
Two families of fields are split deliberately:

  - **Closed-substrate fields** (``required_artifact_type``,
    ``expected_units``, ``expected_frequency``, ``domain_hint``) have
    closed enum / known-set domains.  Boundary A (PR-4) will reject a
    BoundLeaf that contradicts these with HARD errors
    (E_TYPE_MISMATCH / E_UNIT_MISMATCH / E_FREQUENCY_MISMATCH —
    declared in the substrate in PR-1, raised by PR-4).
  - **Free-form fields** (``semantic_role``,
    ``requested_output_meaning``, ``nl_intent``) are
    natural-language strings the LLM authors at L3 and the L2
    Selector echoes back on BoundLeaf.  PR-4's Boundary A will do
    normalised-string equality on these and surface mismatches as
    SOFT warnings (not hard rejects) — Boundary B (the coverage
    gate) consumes those warnings as supplementary evidence.

This split is the explicit answer to R5 (no curated role enum): the
substrate gets hard structured checks only on things that ARE
genuinely closed; everything else stays free-form English, validated
by contradiction-detection rather than vocabulary-matching.

Finance-blindness (P11)
-----------------------
This module sits in ``shared/workflow/`` and is part of the substrate.
It does NOT import from ``rates_agent/`` or from ``orchestrator/``.
The known-domain set lives in ``shared.workflow.resolver_keys`` (also
substrate-internal).  Upstream callers (orchestrator) can use their
own typed ``Domain`` enum and pass its ``.value`` — Pydantic accepts
the string, the substrate validates it against ``KNOWN_DOMAINS``.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts.registry import ArtifactTypeName
from shared.schemas.time_series import TimeSeriesUnits
from orchestrator.open_dag.resolver_keys import (
    KNOWN_DOMAINS,
    UnknownDomainError,
    domain_to_resolver_key,
)
from shared.workflow.types import LiteralBinding, OperatorNode, WorkflowEdge


# ============================================================================
# CLOSED-FAMILY FREQUENCY VOCABULARY
# ============================================================================


class Frequency(str, Enum):
    """Closed family of leaf-contract frequencies.

    Per ``tmp/orchestration.md`` §PR-3 (line 425 in the build): "the
    substrate gets hard structured checks only on the things that are
    GENUINELY closed (artifact_type is already an enum; units is
    TimeSeriesUnits; frequency is a small closed set)."

    The audit-pass (PR-3 corrective) made the frequency field a free
    string — this enum restores the plan's contract: every
    ``LeafRequest.expected_frequency`` and ``BoundLeaf.declared_frequency``
    is one of these values (or None).  Extension is an ADR change.
    """

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# ============================================================================
# LEAF REQUEST (L3 Composer → L2 Selector)
# ============================================================================


class LeafRequest(BaseModel):
    """What the L3 Composer declares per leaf-hole.

    Two field families.  Closed-substrate fields are hard-validated by
    Boundary A (PR-4) against the matching declared_* fields on the
    BoundLeaf.  Free-form fields are normalised-string compared and
    escalate to SOFT warnings, not hard rejects.

    Frozen so the Assembler / Composer cannot mutate after emission —
    the Selector receives an immutable contract.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ---- CLOSED-SUBSTRATE FIELDS (HARD-validated by Boundary A) ----

    required_artifact_type: ArtifactTypeName = Field(
        ...,
        description=(
            "The closed-family artifact type this leaf must produce, so "
            "the operator slot it feeds is type-compatible.  One of "
            "ArtifactTypeName (Series, SeriesSet, EventSet, Panel, "
            "WindowedPanel, ScalarMetric)."
        ),
    )
    expected_units: Optional[TimeSeriesUnits] = Field(
        default=None,
        description=(
            "Optional unit expectation.  When set, the BoundLeaf's "
            "declared_units MUST equal it (E_UNIT_MISMATCH otherwise).  "
            "When None, any unit is accepted (the leaf is unit-agnostic "
            "from the Composer's perspective)."
        ),
    )
    expected_frequency: Optional[Frequency] = Field(
        default=None,
        description=(
            "Optional frequency expectation.  Closed family — one of "
            "Frequency (DAILY, WEEKLY, MONTHLY) or None.  When set, the "
            "BoundLeaf's declared_frequency MUST equal it "
            "(E_FREQUENCY_MISMATCH otherwise).  When None, any "
            "frequency is accepted."
        ),
    )
    domain_hint: str = Field(
        ...,
        min_length=1,
        description=(
            "The L1-router's domain assignment for this leaf.  Selects "
            "which L2 Selector receives this LeafRequest.  Must be one "
            "of KNOWN_DOMAINS (see shared.workflow.resolver_keys)."
        ),
    )

    # ---- FREE-FORM FIELDS (SOFT signals — normalised-equality check) ----

    semantic_role: str = Field(
        ...,
        min_length=1,
        description=(
            "Composer-authored semantic tag describing what kind of "
            "quantity this leaf should produce (e.g. 'spread_level', "
            "'breakeven_level', 'regime_event').  Free string by "
            "design (R5: no curated role enum); the BoundLeaf echoes a "
            "declared_semantic_role and Boundary A flags contradictions "
            "as SOFT warnings."
        ),
    )
    requested_output_meaning: str = Field(
        ...,
        min_length=1,
        description=(
            "One-sentence English description of the answer this leaf "
            "should provide.  Used by Boundary B as supplementary "
            "evidence; the BoundLeaf echoes a declared_output_meaning."
        ),
    )
    nl_intent: str = Field(
        ...,
        min_length=1,
        description=(
            "Plain-English description of WHAT to fetch (e.g. \"two "
            "Series A and B aligned on the same DatetimeIndex over the "
            "last five years\").  Read by the L2 Selector as the "
            "natural-language prompt for primitive selection."
        ),
    )

    @model_validator(mode="after")
    def _validate_domain_hint(self) -> "LeafRequest":
        if self.domain_hint not in KNOWN_DOMAINS:
            raise UnknownDomainError(
                f"LeafRequest.domain_hint={self.domain_hint!r} is not a "
                f"known substrate domain.  Known: {sorted(KNOWN_DOMAINS)}.  "
                "If a new domain has been added, update "
                "shared.workflow.resolver_keys.KNOWN_DOMAINS in the same "
                "PR (P10)."
            )
        return self


# ============================================================================
# BOUND LEAF (L2 Selector → L4 Assembler)
# ============================================================================


class BoundLeaf(BaseModel):
    """What an L2 Selector returns for one LeafRequest.

    Two modes:
      1. **Binding** — ``refusal is None``.  The Selector picked a
         primitive; every field below must be a meaningful value.
      2. **Refusal** — ``refusal`` is a non-empty string explaining
         why no primitive in the Selector's domain fits.  Structural
         fields (``mcp_tool_name``, ``output_field``, declared_*) may
         carry sentinel values; the Assembler MUST NOT read them.

    Frozen.  Carries the chosen primitive's identity in TWO forms:

      - ``mcp_tool_name`` — what the Selector saw via its MCP
        subprocess.  Stored verbatim for audit / lineage / debugging.
      - ``resolver_tool_key`` — what the global PrimitiveResolver is
        registered under.  Derived deterministically via
        ``shared.workflow.resolver_keys.domain_to_resolver_key``;
        Pydantic validation ensures they agree.

    The Assembler (PR-4) substitutes the BoundLeaf into the LeafHole
    by creating a ``PrimitiveNode`` whose ``tool_name`` is the
    ``resolver_tool_key`` (not the MCP name).  That's why the two
    must agree at construction time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # ---- IDENTITY ----

    leaf_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Matches the LeafHole.node_id this BoundLeaf was produced "
            "for.  The Assembler routes BoundLeafs to holes by this id."
        ),
    )
    domain: str = Field(
        ...,
        min_length=1,
        description=(
            "The domain whose Selector produced this BoundLeaf — must "
            "equal the LeafRequest.domain_hint that was sent.  "
            "Validated against KNOWN_DOMAINS at construction time."
        ),
    )

    # ---- BINDING (meaningful when refusal is None) ----

    mcp_tool_name: str = Field(
        default="",
        description=(
            "The visible MCP tool name the Selector bound (e.g. "
            "'calculate_curve_spread_tool').  Stored verbatim for "
            "audit; the Assembler uses ``resolver_tool_key`` for "
            "dispatch.  Empty allowed only when refusal is set."
        ),
    )
    resolver_tool_key: str = Field(
        default="",
        description=(
            "The key the global PrimitiveResolver is registered under.  "
            "Derived deterministically via "
            "``domain_to_resolver_key(domain, mcp_tool_name)``; the "
            "model validator enforces agreement.  Empty allowed only "
            "when refusal is set."
        ),
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "The primitive's input params (passed to its *Input "
            "schema).  Substrate does NOT validate against the schema; "
            "the primitive's own validator catches schema errors at "
            "execution time."
        ),
    )
    output_field: str = Field(
        default="",
        description=(
            "Which ``time_series*`` field of the primitive's output to "
            "lift via the bridge.  Empty allowed only when refusal is "
            "set."
        ),
    )

    # ---- CLOSED-SUBSTRATE DECLARATIONS (HARD-validated vs LeafRequest) ----

    declared_output_artifact_type: ArtifactTypeName = Field(
        default=ArtifactTypeName.SERIES,
        description=(
            "The artifact type the Selector asserts this primitive will "
            "produce.  PR-4 Boundary A asserts this equals the "
            "LeafRequest.required_artifact_type (E_TYPE_MISMATCH else)."
        ),
    )
    declared_units: Optional[TimeSeriesUnits] = Field(
        default=None,
        description=(
            "The unit the Selector asserts this primitive's output has.  "
            "PR-4 Boundary A asserts this equals the "
            "LeafRequest.expected_units when the latter is set "
            "(E_UNIT_MISMATCH else)."
        ),
    )
    declared_frequency: Optional[Frequency] = Field(
        default=None,
        description=(
            "The cadence the Selector asserts this primitive emits.  "
            "Closed family — one of Frequency or None.  PR-4 Boundary "
            "A asserts equality with LeafRequest.expected_frequency "
            "when the latter is set (E_FREQUENCY_MISMATCH else)."
        ),
    )

    # ---- FREE-FORM DECLARATIONS (SOFT signals — Boundary A warns only) ----

    declared_semantic_role: str = Field(
        default="",
        description=(
            "The Selector's own tag for the bound primitive's role.  "
            "PR-4 Boundary A normalised-string compares against the "
            "LeafRequest.semantic_role; mismatch surfaces as a SOFT "
            "warning that Boundary B (PR-8) consumes."
        ),
    )
    declared_output_meaning: str = Field(
        default="",
        description=(
            "One-sentence English describing what this bound primitive "
            "produces.  Compared against LeafRequest.requested_output_meaning."
        ),
    )

    # ---- META ----

    fit_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Selector's self-assessment of how well this primitive fits "
            "the LeafRequest.  Range [0, 1]; low values combined with "
            "successful binding give Boundary B a soft signal to lean "
            "toward CLARIFY."
        ),
    )
    refusal: Optional[str] = Field(
        default=None,
        description=(
            "If set, the Selector refused to bind any primitive to this "
            "leaf and the string explains why.  The Assembler treats "
            "the leaf as unfilled and escalates per the PR-4 / PR-8 "
            "refusal protocol.  When non-None, MUST be a non-empty "
            "string."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "PR-10B Codex F14: optional free-form English the "
            "Selector LLM authored explaining why this primitive was "
            "chosen for this LeafRequest.  When populated, the "
            "IntentChain captures it verbatim — preserving the "
            "actual LLM-authored lingo-resolution rationale per the "
            "plan §PR-9.  When empty (the default), the IntentChain "
            "derives a deterministic rationale from the BoundLeaf's "
            "structured fields.  P11-safe: this is free-form English "
            "about WHY, not what tool — it's surfaced to the user "
            "via the L6 provenance footer where primitive names ARE "
            "the documented exception."
        ),
    )

    @model_validator(mode="after")
    def _validate_consistency(self) -> "BoundLeaf":
        # Domain validity.
        if self.domain not in KNOWN_DOMAINS:
            raise UnknownDomainError(
                f"BoundLeaf.domain={self.domain!r} is not a known "
                f"substrate domain.  Known: {sorted(KNOWN_DOMAINS)}."
            )

        # Refusal-mode rules.
        if self.refusal is not None:
            if not self.refusal.strip():
                raise ValueError(
                    "BoundLeaf.refusal, when set, MUST be a non-empty "
                    "string explaining why the Selector declined to "
                    "bind.  Pass None to indicate a successful binding."
                )
            # In refusal mode, the structural fields are not required
            # to be meaningful — the Assembler will skip the leaf.
            return self

        # Binding-mode rules: structural fields must be present.
        if not self.mcp_tool_name.strip():
            raise ValueError(
                "BoundLeaf in binding mode (refusal=None) requires a "
                "non-empty mcp_tool_name."
            )
        if not self.output_field.strip():
            raise ValueError(
                "BoundLeaf in binding mode (refusal=None) requires a "
                "non-empty output_field."
            )
        if not self.declared_semantic_role.strip():
            raise ValueError(
                "BoundLeaf in binding mode requires a non-empty "
                "declared_semantic_role (free-form English — used by "
                "PR-4 Boundary A's role-discriminant check)."
            )
        if not self.declared_output_meaning.strip():
            raise ValueError(
                "BoundLeaf in binding mode requires a non-empty "
                "declared_output_meaning."
            )

        # Resolver-key consistency.  The single canonical seam: the
        # Selector returns the visible mcp_tool_name AND the derived
        # resolver_tool_key.  We enforce agreement here so a Selector
        # that hand-rolls the key (or skips the helper) trips
        # immediately rather than miscompose at execute-time.
        expected_key = domain_to_resolver_key(
            self.domain, self.mcp_tool_name,
        )
        if self.resolver_tool_key != expected_key:
            raise ValueError(
                f"BoundLeaf.resolver_tool_key={self.resolver_tool_key!r} "
                f"does not match the substrate's convention for "
                f"(domain={self.domain!r}, "
                f"mcp_tool_name={self.mcp_tool_name!r}).  Expected: "
                f"{expected_key!r}.  Always derive the key via "
                "shared.workflow.resolver_keys.domain_to_resolver_key."
            )

        return self

    @property
    def is_refusal(self) -> bool:
        """True iff this BoundLeaf is a refusal (no primitive bound)."""
        return self.refusal is not None


# ============================================================================
# LEAF HOLE (placeholder node in a ShapeSpec)
# ============================================================================


class LeafHole(BaseModel):
    """A placeholder node in a ``ShapeSpec``, in place of a
    ``PrimitiveNode``.  Carries the LeafRequest the Composer authored
    for this hole.

    ``kind`` is the discriminator for the ``ShapeNode`` union — the
    Composer emits a ShapeSpec whose nodes are EITHER LeafHole or
    OperatorNode.  When the L2 Selector returns a BoundLeaf for this
    hole's ``node_id``, the PR-4 Assembler substitutes a real
    ``PrimitiveNode`` (with the BoundLeaf's resolver_tool_key as its
    tool_name) in its place to produce a runnable ``Workflow``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["leaf_hole"] = "leaf_hole"
    node_id: str = Field(..., min_length=1)
    leaf_request: LeafRequest


# Discriminated union over the closed family of ShapeSpec nodes.  Pydantic
# picks the right concrete class by ``kind`` on deserialization so a
# ShapeSpec round-trips through JSON without losing type information.
ShapeNode = Annotated[
    Union[LeafHole, OperatorNode],
    Field(discriminator="kind"),
]


# ============================================================================
# SHAPE SPEC (L3 Composer output)
# ============================================================================


class ShapeSpec(BaseModel):
    """A typed DAG with leaf-holes — the L3 Composer's emitted artifact.

    Shape parallels ``Workflow`` (nodes + edges + literal_bindings +
    terminal_node_id + workflow_id) but uses ``LeafHole`` in place of
    ``PrimitiveNode``.  The Assembler (PR-4) substitutes each LeafHole
    with a PrimitiveNode (using the matching BoundLeaf's
    resolver_tool_key as tool_name) and produces a runnable Workflow.

    Construction-time gate mirrors ``Workflow._validate_basic_shape``:
    unique node IDs, terminal references a real node, edges reference
    real nodes, literal_bindings target real nodes.  Deeper checks
    (slot existence, type compat, cycles, role-discriminant) belong to
    the post-substitution validator (PR-4 calls validate_workflow_result
    on the assembled Workflow).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_id: str = Field(..., min_length=1)
    nodes: List[ShapeNode] = Field(..., min_length=1)
    edges: List[WorkflowEdge] = Field(default_factory=list)
    literal_bindings: List[LiteralBinding] = Field(default_factory=list)
    terminal_node_id: str = Field(..., min_length=1)

    @model_validator(mode="after")
    def _validate_basic_shape(self) -> "ShapeSpec":
        """Construction-time gate mirroring ``Workflow``'s.  Catches the
        most basic shape errors immediately so a malformed ShapeSpec
        cannot be built at all.  Deeper checks happen post-substitution
        via ``validate_workflow_result`` in PR-4."""
        # 1. Node IDs unique.
        node_ids = [n.node_id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            duplicates = sorted(
                {nid for nid in node_ids if node_ids.count(nid) > 1}
            )
            raise ValueError(
                f"ShapeSpec {self.workflow_id!r}: duplicate node_id(s) "
                f"{duplicates}; every node must have a unique node_id."
            )

        # 2. terminal_node_id references a real node.
        if self.terminal_node_id not in node_ids:
            raise ValueError(
                f"ShapeSpec {self.workflow_id!r}: terminal_node_id="
                f"{self.terminal_node_id!r} does not match any node in "
                f"this shape.  Known node IDs: {sorted(node_ids)}."
            )

        # 3. Every edge references real nodes.
        node_id_set = set(node_ids)
        for edge in self.edges:
            if edge.source_node_id not in node_id_set:
                raise ValueError(
                    f"ShapeSpec {self.workflow_id!r}: edge references "
                    f"unknown source_node_id={edge.source_node_id!r}.  "
                    f"Known node IDs: {sorted(node_id_set)}."
                )
            if edge.target_node_id not in node_id_set:
                raise ValueError(
                    f"ShapeSpec {self.workflow_id!r}: edge references "
                    f"unknown target_node_id={edge.target_node_id!r}.  "
                    f"Known node IDs: {sorted(node_id_set)}."
                )

        # 4. Every literal binding references a real node.
        for binding in self.literal_bindings:
            if binding.target_node_id not in node_id_set:
                raise ValueError(
                    f"ShapeSpec {self.workflow_id!r}: literal binding "
                    f"references unknown target_node_id="
                    f"{binding.target_node_id!r}.  Known node IDs: "
                    f"{sorted(node_id_set)}."
                )

        return self

    def node_by_id(self, node_id: str) -> ShapeNode:
        """Return the node with the given ``node_id``, or raise
        KeyError if no such node exists."""
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(
            f"ShapeSpec {self.workflow_id!r} has no node with "
            f"node_id={node_id!r}."
        )

    def leaf_holes(self) -> List[LeafHole]:
        """Return every LeafHole node in declaration order.  The
        Assembler iterates this to dispatch LeafRequests to the
        per-domain Selectors."""
        return [n for n in self.nodes if isinstance(n, LeafHole)]

    def leaf_hole_ids(self) -> List[str]:
        """Return the node_ids of every LeafHole.  Convenience for
        the Assembler's substitution sweep."""
        return [n.node_id for n in self.nodes if isinstance(n, LeafHole)]


__all__ = [
    "Frequency",
    "LeafRequest",
    "BoundLeaf",
    "LeafHole",
    "ShapeNode",
    "ShapeSpec",
]

"""orchestrator.open_dag.composer — PR-7 of the open-DAG PoC.

The L3 Composer.  Takes a user prompt + the L1 router's
``IntentTag`` + ``decomposition`` and emits a ``ShapeSpec`` — a typed
DAG with one ``LeafHole`` per input quantity and one or more
``OperatorNode``s wiring them into the operator graph that answers the
prompt.

Per the plan (``tmp/orchestration.md`` §PR-7) this module's discipline:

  - **The Composer NEVER picks a primitive.**  Every leaf is a
    ``LeafHole`` carrying a fully-specified ``LeafRequest``; the L2
    Selectors fill them.  The Composer's prompt does not contain any
    primitive names by design (the grep-test in ``test_composer.py``
    asserts this).
  - **The Composer NEVER mutates a shape in repair.**  ``Composer.repair``
    implements the PR-4 ``ShapePatchProvider`` Protocol: it returns a
    sequence of additive ``ShapePatch`` instances (``InsertAdapterNode``
    / ``RewireEdge``) — never a new ``ShapeSpec``.
  - **The Composer ALWAYS sees the full 16-operator catalogue.**  No
    shortlister.  Per R3 / R4 of the rulings list: rich operator cards
    ARE the priors the LLM reasons over.

Two-layer split (mirrors PR-6's selectors / domain_agent split)
--------------------------------------------------------------

This module exposes a pure layer (prompt rendering, structured-output
schema, LLM-output transformer) and the orchestrating ``Composer``
class.  Tests target the pure layer first; ``Composer`` itself is
tested with a mock LLM that conforms to LangChain's structured-output
``ainvoke -> {"raw": ..., "parsed": ..., "parsing_error": ...}``
contract.

Finance-blindness
-----------------

This module sits in ``orchestrator/open_dag/`` (the domain-aware
agent layer).  It imports from ``orchestrator.contracts`` (IntentTag /
EconomicQuantity / Domain), ``orchestrator.open_dag.contracts``
(LeafHole / LeafRequest / ShapeSpec / Frequency), and ``shared.workflow``
(operator catalogue + Workflow types).  It does NOT import from
``rates_agent/`` — primitives never appear here.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

from orchestrator.config import COMPOSER_MODEL
from orchestrator.contracts import EconomicQuantity, IntentTag
from orchestrator.open_dag.composer_golden_shapes import (
    GOLDEN_SHAPES,
    render_golden_few_shots,
)
from orchestrator.open_dag.contracts import (
    Frequency,
    LeafHole,
    LeafRequest,
    ShapeSpec,
)
from shared.artifacts.registry import ArtifactTypeName
from shared.schemas.time_series import TimeSeriesUnits
from shared.workflow.operator_catalogue import (
    OperatorCard,
    card_to_prompt_block,
    render_operator_catalogue,
)
from shared.workflow.registry import OPERATOR_REGISTRY
from shared.workflow.types import LiteralBinding, OperatorNode, WorkflowEdge

if TYPE_CHECKING:  # pragma: no cover — type-checking-only imports
    from orchestrator.open_dag.assembler import (
        InsertAdapterNode,
        RewireEdge,
        ShapePatch,
    )
    from shared.workflow.types import Workflow
    from shared.workflow.validation_result import ValidationError


logger = logging.getLogger(__name__)


# ============================================================================
# COMPOSER LLM OUTPUT — STRUCTURED-OUTPUT SCHEMA
# ============================================================================
#
# The Composer's structured-output schema mirrors ``ShapeSpec`` but in a
# form Pydantic can parse from a JSON tool-call payload without a
# discriminated union at the LLM seam.  Each leaf-hole and each operator
# node is its own typed declaration; the post-LLM transformer
# (``llm_output_to_shape_spec``) assembles them into a ``ShapeSpec``.
#
# The split is deliberate: ``ShapeSpec.nodes`` is an ``Annotated[Union,
# Field(discriminator=…)]`` — Pydantic's discriminated unions parse
# fine from JSON, but LangChain's structured-output schema generation
# is most reliable on a flat shape with separate ``leaf_holes`` /
# ``operator_nodes`` lists.  The transformer reassembles them.


class _LeafHoleDecl(BaseModel):
    """One leaf-hole declaration in the LLM's structured output.

    Mirrors the fields the LLM has to fill on a ``LeafHole`` (node_id +
    LeafRequest fields, no discriminator literal).
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Workflow-local identifier for this leaf-hole.  Must be "
            "unique across all leaf_holes AND operator_nodes in the "
            "same shape.  Composer-chosen; suggested format "
            "'leaf_<short_name>' (e.g. 'leaf_a', 'leaf_trigger')."
        ),
    )

    required_artifact_type: ArtifactTypeName = Field(
        ...,
        description=(
            "Closed-family artifact type this leaf must produce.  One "
            "of Series, SeriesSet, EventSet, Panel, WindowedPanel, "
            "ScalarMetric.  In V1, leaf primitives are almost always "
            "Series-producing — leaves are how a domain Selector "
            "turns a primitive call into a typed Series input for "
            "operators downstream."
        ),
    )
    expected_units: Optional[TimeSeriesUnits] = Field(
        default=None,
        description=(
            "Optional unit expectation.  Set when the prompt or the "
            "operator-chain downstream requires a specific unit "
            "(e.g. bps for a yield-spread leg).  Leave null when any "
            "unit is acceptable — the Composer should err toward "
            "leaving this null and letting the operator chain handle "
            "unit-conversion via the convert_units adapter."
        ),
    )
    expected_frequency: Optional[Frequency] = Field(
        default=None,
        description=(
            "Closed-family frequency (daily / weekly / monthly) or "
            "null.  Pin this when the operator downstream requires a "
            "specific cadence; leave null when any cadence is "
            "acceptable."
        ),
    )
    domain_hint: str = Field(
        ...,
        min_length=1,
        description=(
            "Which L1-routed domain owns this quantity.  Must match a "
            "Domain enum value (sovereign_bonds / ois / "
            "inflation_indexed_bonds / inflation_swaps / "
            "policy_futures / bond_futures).  The Selector for this "
            "domain receives the LeafRequest."
        ),
    )
    semantic_role: str = Field(
        ...,
        min_length=1,
        description=(
            "Composer's free-form tag describing the role this leaf "
            "plays in the shape (e.g. 'first_input_series', "
            "'event_trigger_series', 'dependent_variable').  Used by "
            "PR-4 Boundary A's role-discriminant check as a SOFT "
            "warning."
        ),
    )
    requested_output_meaning: str = Field(
        ...,
        min_length=1,
        description=(
            "One-sentence English description of what this leaf "
            "produces, in the Composer's words.  Used by Boundary B "
            "as supplementary evidence."
        ),
    )
    nl_intent: str = Field(
        ...,
        min_length=1,
        description=(
            "Plain-English description of WHAT the Selector should "
            "fetch — used as the Selector's per-call prompt.  Keep "
            "this concrete enough that the Selector can pick the "
            "right primitive without re-reading the user's prompt."
        ),
    )


class _OperatorNodeDecl(BaseModel):
    """One operator-node declaration in the LLM's structured output.

    Mirrors ``OperatorNode`` (node_id + operator_name + params), with no
    discriminator literal.  The post-LLM transformer adds the
    discriminator on the way to the typed ShapeSpec.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Workflow-local identifier for this operator node.  Must "
            "be unique across all leaf_holes AND operator_nodes."
        ),
    )
    operator_name: str = Field(
        ...,
        min_length=1,
        description=(
            "One of the 16 closed-family operator names from the "
            "catalogue.  The Composer's prompt embeds every name; "
            "picking one outside that set will fail post-LLM "
            "validation (the OPERATOR_REGISTRY lookup raises)."
        ),
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Operator-specific params.  The substrate does NOT "
            "validate these; the operator's own *Params schema "
            "catches errors at execution time.  Required params (e.g. "
            "'window', 'aggregator', 'op') belong here."
        ),
    )


class _EdgeDecl(BaseModel):
    """One DAG edge in the LLM's structured output."""

    model_config = ConfigDict(extra="forbid")

    source_node_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    target_input_slot: str = Field(
        ...,
        min_length=1,
        description=(
            "Which named slot on the target operator receives this "
            "artifact.  Slot names match the catalogue's INPUT SLOTS "
            "section per operator (e.g. align_series has "
            "'series_list'; correlation has 'left' + 'right')."
        ),
    )


class _LiteralBindingDecl(BaseModel):
    """One literal scalar binding in the LLM's structured output."""

    model_config = ConfigDict(extra="forbid")

    target_node_id: str = Field(..., min_length=1)
    target_input_slot: str = Field(..., min_length=1)
    value: Any = Field(
        ...,
        description=(
            "Scalar literal (int / float / str / bool).  Only valid "
            "for operator slots whose SlotDescriptor.accepts_scalar "
            "is True."
        ),
    )


class ComposerLLMOutput(BaseModel):
    """The L3 Composer LLM's structured output.

    Mirrors the structural shape of ``ShapeSpec`` but with the
    discriminated union flattened into two parallel lists — the
    transformer ``llm_output_to_shape_spec`` reassembles them.

    Two modes:
      (A) Compose mode (``refusal`` is null): the LLM emits the lists.
      (B) Refusal mode (``refusal`` is a non-empty string): the LLM
          declined to compose a shape because no operator family in the
          catalogue fits the prompt's intent.
    """

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(
        default="open_dag_composed",
        min_length=1,
        description=(
            "Stable identifier for this composed workflow.  Used for "
            "logging / lineage.  Composer chooses; defaults to "
            "'open_dag_composed' when the prompt doesn't suggest a "
            "more specific name."
        ),
    )
    leaf_holes: List[_LeafHoleDecl] = Field(
        default_factory=list,
        description=(
            "Every input-quantity LeafHole in the shape.  Empty in "
            "refusal mode.  Each entry's node_id must be unique "
            "across leaf_holes AND operator_nodes."
        ),
    )
    operator_nodes: List[_OperatorNodeDecl] = Field(
        default_factory=list,
        description=(
            "Every operator node in the shape.  Empty in refusal "
            "mode.  Each entry's node_id must be unique across "
            "leaf_holes AND operator_nodes."
        ),
    )
    edges: List[_EdgeDecl] = Field(
        default_factory=list,
        description=(
            "Directed edges wiring leaves and operators into a DAG.  "
            "Each edge's target_input_slot must match a slot the "
            "target operator's catalogue card declares."
        ),
    )
    literal_bindings: List[_LiteralBindingDecl] = Field(
        default_factory=list,
        description=(
            "Scalar literal bindings.  Only for slots whose "
            "SlotDescriptor.accepts_scalar is True (e.g. "
            "series_arithmetic.right when used with a scalar)."
        ),
    )
    terminal_node_id: str = Field(
        default="",
        description=(
            "Which node's output is the terminal artifact of the "
            "shape.  Must be the node_id of one of the operator_nodes "
            "(or, in degenerate 1-leaf shapes, a leaf_hole).  Empty "
            "string in refusal mode."
        ),
    )
    refusal: Optional[str] = Field(
        default=None,
        description=(
            "When set, no shape was emitted.  Non-empty string "
            "explaining why no operator family fits the prompt's "
            "intent.  The Assembler / Boundary B routes a refusal to "
            "the clarification path; the Composer does NOT emit a "
            "fake LeafHole to force the pipeline onward."
        ),
    )
    rationale: str = Field(
        default="",
        description=(
            "PR-10B Codex F14: optional free-form English explaining "
            "the WIRING choice — why this operator chain, why this "
            "terminal type.  When the Composer LLM provides this, "
            "the IntentChain captures it verbatim, preserving the "
            "LLM-authored wiring rationale the plan asks for.  "
            "When empty (default), the IntentChain derives a "
            "deterministic rationale from operator_names + "
            "terminal_operator_name."
        ),
    )

    @field_validator("refusal")
    @classmethod
    def _refusal_non_empty_or_none(cls, v: Optional[str]) -> Optional[str]:
        # Treat empty / whitespace-only refusal strings as None so the
        # post-LLM transformer doesn't see an ambiguous "empty refusal".
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


# ============================================================================
# REPAIR — STRUCTURED-OUTPUT SCHEMA
# ============================================================================


class _InsertAdapterPatchDecl(BaseModel):
    """One InsertAdapterNode patch in the Composer's repair output."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        default="insert_adapter_node",
        description=(
            "Discriminator; always the string 'insert_adapter_node'."
        ),
    )
    on_edge_source: str = Field(..., min_length=1)
    on_edge_target: str = Field(..., min_length=1)
    on_edge_slot: str = Field(..., min_length=1)
    adapter_node_id: str = Field(..., min_length=1)
    adapter_operator_name: str = Field(
        ...,
        min_length=1,
        description=(
            "One of the closed adapter whitelist: 'convert_units' or "
            "'align_series'.  Any other name fails the patch's typed "
            "construction at the Assembler boundary."
        ),
    )
    adapter_input_slot: str = Field(..., min_length=1)
    adapter_params: Dict[str, Any] = Field(default_factory=dict)
    adapter_literal_bindings: List[_LiteralBindingDecl] = Field(
        default_factory=list,
    )


class _RewireEdgePatchDecl(BaseModel):
    """One RewireEdge patch in the Composer's repair output."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(
        default="rewire_edge",
        description=(
            "Discriminator; always the string 'rewire_edge'."
        ),
    )
    source_node_id: str = Field(..., min_length=1)
    target_node_id: str = Field(..., min_length=1)
    current_target_input_slot: str = Field(..., min_length=1)
    new_target_input_slot: str = Field(..., min_length=1)


class ComposerRepairLLMOutput(BaseModel):
    """The Composer LLM's structured output for a repair round.

    Per the plan's discipline (``tmp/orchestration.md`` §PR-7): the
    Composer NEVER mutates the shape in repair.  It returns ONLY
    additive patches; the Assembler applies them deterministically.

    Two lists (one per patch kind) mirror the closed family of
    ``ShapePatch`` types.  Both lists empty → refusal (no
    repair possible).
    """

    model_config = ConfigDict(extra="forbid")

    insert_adapter_patches: List[_InsertAdapterPatchDecl] = Field(
        default_factory=list,
        description=(
            "Adapter insertions on existing edges.  Used to fix unit "
            "mismatches (convert_units) and series-alignment / "
            "frequency mismatches (align_series).  No other operator "
            "is a legal adapter."
        ),
    )
    rewire_patches: List[_RewireEdgePatchDecl] = Field(
        default_factory=list,
        description=(
            "Edge-slot rewires on existing edges.  Used when the "
            "Composer wired the right pair of nodes but to the wrong "
            "input slot on the target operator.  Source AND target "
            "nodes do NOT change."
        ),
    )
    refusal: Optional[str] = Field(
        default=None,
        description=(
            "When set, the Composer cannot patch this set of errors "
            "with additive mutations alone.  Boundary B sees the "
            "refusal and routes to clarification."
        ),
    )

    @field_validator("refusal")
    @classmethod
    def _refusal_non_empty_or_none(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        stripped = v.strip()
        return stripped or None


# ============================================================================
# REFUSAL ENVELOPE (Composer.compose return type)
# ============================================================================


class ComposerRefusal(BaseModel):
    """What ``Composer.compose`` returns when the Composer declines.

    Carried as a typed envelope (NOT a raised exception) so callers
    can treat refusal as first-class — Boundary B receives a structured
    refusal for clarification, the way ``BoundLeaf`` refusals flow
    through the Selector path.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str = Field(..., min_length=1)


# ``compose`` returns either a ShapeSpec (when the Composer emitted a
# shape) or a ComposerRefusal (when it declined).  Two concrete types,
# not a union — callers pattern-match by isinstance().
ComposeResult = Any  # Union[ShapeSpec, ComposerRefusal] — annotated this way
# because pydantic discriminated unions can't combine across modules
# cleanly without a wrapper.


# ============================================================================
# PURE LOGIC — PROMPT RENDERING
# ============================================================================


def _intent_section_for_prompt(catalogue: Dict[str, OperatorCard]) -> str:
    """Render the IntentTag → canonical-operator-family map the
    Composer prompt embeds.  Per R3 / R4: the LLM gets the priors, then
    reasons.

    PR-10B Codex F15: hints are derived from each operator card's
    ``intent_hints`` YAML field (registration-only growth — a new
    operator with ``intent_hints: [RELATIONSHIP]`` in its YAML
    automatically appears in the RELATIONSHIP row without any
    composer.py edit).  Cards that don't declare intent_hints fall
    through to the V1 hardcoded baseline below so existing operators
    keep their established positions until each YAML is updated.
    """
    # V1 hardcoded baseline — preserved for operators whose YAML
    # doesn't declare intent_hints yet.  New operator families
    # SHOULD ship with their own intent_hints in YAML.
    _BASELINE: Dict[IntentTag, List[str]] = {
        IntentTag.LOOKUP: ["(direct primitive answer — no operators required)"],
        IntentTag.RELATIONSHIP: ["correlation", "rolling_correlation"],
        IntentTag.REGRESSION: ["rolling_regression"],
        IntentTag.COINTEGRATION: ["cointegration"],
        IntentTag.TRANSFORM: [
            "rolling_zscore",
            "rolling_statistic",
            "percentile_rank",
            "convert_units",
            "series_arithmetic",
        ],
        IntentTag.EVENT_REGIME: [
            "threshold_events",
            "event_windows",
            "conditional_aggregate",
            "apply_mask",
        ],
        IntentTag.SCAN: ["(scanner primitives — no operator chain)"],
        IntentTag.PANEL: ["select_from_series_set"],
        IntentTag.BASIS: ["align_series", "select_from_series_set", "series_arithmetic"],
    }

    # PR-10H gap #5: per-tag precedence — if ANY operator declares
    # ``intent_hints`` for a given IntentTag, the rendered row for
    # that tag is the (deterministically sorted) union of those
    # operators and the baseline row is DROPPED.  When NO operator
    # declares hints for a tag, the baseline row is preserved
    # verbatim.  This is what lets the SCAN '(scanner primitives)'
    # textual hint and the LOOKUP '(direct primitive answer)'
    # textual hint survive (no operator can claim them), while
    # still letting a new operator REPLACE the RELATIONSHIP row by
    # declaring intent_hints: [relationship] in its YAML — true
    # registration-only growth on the intent table.  Empty
    # intent_hints lists on utility operators (align_series,
    # select_from_series_set, convert_units) signal "this operator
    # is intent-agnostic, invoked structurally" and contribute
    # nothing to the rendered table.
    card_hints: Dict[IntentTag, List[str]] = {}
    for op_name, card in catalogue.items():
        hints = getattr(card, "intent_hints", ()) or ()
        for hint in hints:
            try:
                tag = IntentTag(hint.lower().strip())
            except ValueError:
                continue
            card_hints.setdefault(tag, []).append(op_name)
    intent_to_operators_dict: Dict[IntentTag, List[str]] = {}
    for tag in IntentTag:
        if tag in card_hints:
            intent_to_operators_dict[tag] = sorted(set(card_hints[tag]))
        elif tag in _BASELINE:
            intent_to_operators_dict[tag] = list(_BASELINE[tag])

    intent_to_operators: List[Tuple[IntentTag, List[str]]] = [
        (tag, intent_to_operators_dict[tag]) for tag in IntentTag
        if tag in intent_to_operators_dict
    ]
    lines: List[str] = ["INTENT → CANONICAL OPERATOR FAMILY:"]
    for tag, ops in intent_to_operators:
        # Filter the list to ones actually in the catalogue so a
        # registry deletion doesn't quietly leave the prompt stale.
        known = [o for o in ops if o in catalogue or "(" in o]
        if not known:
            known = ops  # keep the textual hint even when no match
        lines.append(f"  - {tag.value}: {', '.join(known)}")
    return "\n".join(lines)


def _artifact_types_section() -> str:
    """Render the 6 closed-family artifact-type names + a one-line gloss."""
    descriptions = {
        ArtifactTypeName.SERIES: "single time-indexed Series.",
        ArtifactTypeName.SERIES_SET: "bundle of N Series aligned on the same DatetimeIndex.",
        ArtifactTypeName.EVENT_SET: "boolean event-flag Series marking trigger dates.",
        ArtifactTypeName.PANEL: "Cross-sectional × time-indexed Panel.",
        ArtifactTypeName.WINDOWED_PANEL: "Per-event panel with one row per event and one column per offset.",
        ArtifactTypeName.SCALAR_METRIC: "single scalar metric (e.g. correlation coefficient, ADF stat).",
    }
    lines: List[str] = ["ARTIFACT TYPES (closed family — every leaf and edge typed):"]
    for name in ArtifactTypeName:
        lines.append(f"  - {name.value}: {descriptions[name]}")
    return "\n".join(lines)


def render_operator_catalogue_block(
    catalogue: Optional[Dict[str, OperatorCard]] = None,
) -> str:
    """Render the 16-operator catalogue as a deterministic concatenated
    text block.  Each card uses ``card_to_prompt_block`` (PR-2's
    canonical renderer) so the substrate is the single source of truth.
    """
    cat = catalogue if catalogue is not None else render_operator_catalogue()
    blocks: List[str] = []
    blocks.append(f"OPERATOR CATALOGUE ({len(cat)} operators):")
    for name in sorted(cat.keys()):
        blocks.append("")
        blocks.append(card_to_prompt_block(cat[name]))
    return "\n".join(blocks)


def render_composer_user_message(
    *,
    prompt: str,
    intent_tag: IntentTag,
    decomposition: Sequence[EconomicQuantity],
    correction: Optional[str] = None,
) -> str:
    """Render the per-call user message the Composer LLM sees.

    The system prompt
    (``orchestrator.prompts.COMPOSER_SYSTEM_PROMPT``) carries the
    static instructions + the 16-operator catalogue + the golden
    few-shots.  The user message carries the per-call payload: the
    user's prompt + the L1 router's intent + decomposition.

    Deterministic (sorted keys / stable ordering / no randomness) so
    the Anthropic prompt cache hits the system block cleanly.
    """
    lines: List[str] = []
    lines.append("USER PROMPT:")
    lines.append(prompt)
    lines.append("")
    lines.append(f"L1 INTENT: {intent_tag.value}")
    lines.append("")
    lines.append(f"L1 DECOMPOSITION ({len(decomposition)} quantities):")
    for i, q in enumerate(decomposition, start=1):
        lines.append(f"  {i}. name={q.name!r}")
        lines.append(f"     domain_hint={q.domain_hint.value}")
        lines.append(f"     nl_description={q.nl_description!r}")
    lines.append("")
    # Plan D1/D3: reason-seeded RE-COMPOSE.  When the previous attempt's
    # DAG failed a deterministic check or the coverage gate, the SPECIFIC
    # reason is surfaced here so this fresh compose corrects it.
    if correction:
        lines.append("CORRECTION — your PREVIOUS attempt was rejected:")
        lines.append(correction.strip())
        lines.append(
            "Build a DIFFERENT ShapeSpec that fixes the issue above.  Do "
            "NOT repeat the same mistake; in particular, make the TERMINAL "
            "node produce the artifact shape the question asks for."
        )
        lines.append("")
    lines.append(
        "Emit a ShapeSpec.  One LeafHole per decomposition entry whose "
        "input quantity must be fetched from a domain Selector; one "
        "OperatorNode per analytical step; edges wiring them into a "
        "DAG; terminal_node_id naming the final node.  Set refusal="
        "<reason> when no operator chain from the catalogue produces "
        "the user's requested quantity."
    )
    return "\n".join(lines)


def render_composer_repair_user_message(
    *,
    workflow: "Workflow",
    errors: Sequence["ValidationError"],
) -> str:
    """Render the per-call user message the Composer LLM sees in repair
    mode.

    The repair system prompt
    (``orchestrator.prompts.COMPOSER_REPAIR_PROMPT``) carries the
    static instructions + the additive-only patch discipline + the
    adapter whitelist.  The user message carries the **redacted**
    post-substitution workflow + the structured validation errors the
    Assembler routed to L3_WIRING.

    Per PR-7A Codex F2: the Workflow's PrimitiveNode entries carry
    ``tool_name`` / ``output_field`` / primitive-side ``params`` —
    surfacing those in the L3 LLM prompt violates the Composer's
    primitive-blindness discipline (P11).  The repair prompt uses
    ``_workflow_to_redacted_dict`` which keeps the SHAPE topology
    (node_ids, operator nodes, edges, slots) and elides primitive
    identities.  L3 can still pinpoint where to wire an adapter or
    rewire an edge because the validation errors carry the relevant
    ``node_id`` + ``detail`` payload.

    Similarly, ``ValidationError.tool_name`` is omitted from the
    rendered diagnostics — when the substrate flags a unit / type
    mismatch, the *kind* of mismatch (unit X expected, unit Y
    declared) is what the Composer needs to pick an adapter; the
    primitive's tool_name is not.
    """
    lines: List[str] = []
    lines.append("ASSEMBLED SHAPE (post-substitution, primitive identities redacted):")
    lines.append(json.dumps(
        _workflow_to_redacted_dict(workflow), indent=2, sort_keys=True,
    ))
    lines.append("")
    lines.append(f"L3_WIRING HARD ERRORS ({len(errors)}):")
    for i, e in enumerate(errors, start=1):
        # ValidationError surface in the repair prompt: code +
        # node_id + detail (closed-substrate fields the validator
        # populated for the diagnostic).  tool_name is REDACTED so
        # the Composer cannot infer the primitive's identity from
        # the error stream — primitive-blindness on the diagnostic
        # surface too.
        lines.append(
            f"  {i}. code={e.code.value} | node_id={e.node_id} | "
            f"detail={dict(e.detail)}"
        )
        lines.append(f"      message: {e.message}")
    lines.append("")
    lines.append(
        "Emit ONLY additive patches: insert_adapter_node entries or "
        "rewire_edge entries.  NEVER a new shape; NEVER a primitive "
        "swap; NEVER a node removal.  Set refusal=<reason> when no "
        "additive patch resolves these errors."
    )
    return "\n".join(lines)


def _workflow_to_redacted_dict(workflow: "Workflow") -> Dict[str, Any]:
    """Render a post-substitution Workflow as a SHAPE-LEVEL view —
    structural information only, primitive identities ELIDED.

    Per PR-7A Codex F2: the Composer must remain primitive-blind
    even in repair.  This function surfaces what the Composer
    legitimately needs to author additive patches:

      - every node's ``node_id`` so the patch can reference it
      - every operator node's ``operator_name`` + operator-side
        ``params`` (so the Composer can read the existing knob set
        when deciding whether to rewire vs adapt)
      - every edge's (source, target, target_input_slot) so the
        Composer knows where to splice an InsertAdapterNode
      - literal bindings + terminal — same structural reasoning

    Primitive nodes are surfaced as ``{kind: "primitive",
    node_id: ...}`` only.  Their ``tool_name`` (which would tell L3
    which domain's MCP tool the Selector picked), ``output_field``
    (which would reveal the primitive's output-schema vocabulary),
    and primitive-side ``params`` (which would expose domain-specific
    instrument identifiers) are stripped.

    This is the symmetric counterpart of L2 Selectors not seeing
    operators: L3 doesn't see primitives, even at the post-substitution
    boundary.
    """
    nodes: List[Dict[str, Any]] = []
    for n in workflow.nodes:
        kind = getattr(n, "kind", None)
        if kind == "primitive":
            # PR-7A Codex F2: REDACTED.  Only the structural identity
            # (node_id + kind label) crosses the L3 boundary.
            nodes.append({
                "kind": "primitive",
                "node_id": n.node_id,
            })
        elif kind == "operator":
            # Operators are L3's own vocabulary — full surface.
            nodes.append({
                "kind": "operator",
                "node_id": n.node_id,
                "operator_name": n.operator_name,
                "params": dict(n.params),
            })
        else:  # defensive — unknown future node kind
            nodes.append({"kind": kind, "node_id": n.node_id})
    return {
        "workflow_id": workflow.workflow_id,
        "nodes": nodes,
        "edges": [
            {
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "target_input_slot": e.target_input_slot,
            }
            for e in workflow.edges
        ],
        "literal_bindings": [
            {
                "target_node_id": b.target_node_id,
                "target_input_slot": b.target_input_slot,
                "value": b.value,
            }
            for b in workflow.literal_bindings
        ],
        "terminal_node_id": workflow.terminal_node_id,
    }


# Backwards-compatible alias for the prior name.  Older callers (and
# this module's own __all__ pre-PR-7A) referenced ``_workflow_to_dict``.
# The alias preserves the import surface but the implementation is the
# redacted variant — primitive identities are NEVER serialised again
# from this module's surface, even by accident.
_workflow_to_dict = _workflow_to_redacted_dict


# ============================================================================
# PURE LOGIC — LLM OUTPUT → ShapeSpec
# ============================================================================


class ComposerOutputError(ValueError):
    """Raised when the Composer LLM's structured output is structurally
    impossible to convert into a valid ``ShapeSpec`` (e.g. references
    an unknown operator, the terminal_node_id doesn't match any node,
    duplicate node_ids).  Caller converts to a ``ComposerRefusal`` so
    Boundary B sees a structured outcome."""


def llm_output_to_shape_spec(output: ComposerLLMOutput) -> ShapeSpec:
    """Transform the Composer LLM's flat structured output into a typed
    ``ShapeSpec``.

    Raises
    ------
    ComposerOutputError
        When the output is structurally impossible: an operator_name
        not in OPERATOR_REGISTRY; a duplicate node_id between
        leaf_holes and operator_nodes; a terminal_node_id that doesn't
        match any node; an edge / literal binding that references an
        unknown node.  ``ShapeSpec``'s own construction-time
        validator catches the second / third / fourth cases too; the
        ComposerOutputError check here gives a tighter error message
        (operator-name lookup is the only check that ShapeSpec
        doesn't already perform).

    Notes
    -----
    This function is REFUSAL-AGNOSTIC: callers must check
    ``output.refusal is None`` BEFORE calling this transformer.
    Passing a refusal output here is a contract violation (it would
    try to build a ShapeSpec with empty nodes and ``ShapeSpec``'s own
    ``min_length=1`` validator would raise).
    """
    if output.refusal is not None:
        raise ComposerOutputError(
            f"llm_output_to_shape_spec called on a refusal output "
            f"(reason: {output.refusal!r}); callers must branch on "
            "output.refusal before transforming."
        )

    # Operator-name closed-family check (the only check ShapeSpec's
    # own validator does not perform — ShapeSpec checks structural
    # references, not closed-family membership).
    for op_decl in output.operator_nodes:
        if op_decl.operator_name not in OPERATOR_REGISTRY:
            raise ComposerOutputError(
                f"Composer LLM declared operator_name="
                f"{op_decl.operator_name!r} which is not in the closed "
                f"operator registry.  Known: "
                f"{sorted(OPERATOR_REGISTRY.keys())}."
            )

    # Build leaf-hole nodes.  ``kind`` is the discriminator literal.
    leaf_nodes: List[LeafHole] = []
    for lh in output.leaf_holes:
        leaf_nodes.append(LeafHole(
            node_id=lh.node_id,
            leaf_request=LeafRequest(
                required_artifact_type=lh.required_artifact_type,
                expected_units=lh.expected_units,
                expected_frequency=lh.expected_frequency,
                domain_hint=lh.domain_hint,
                semantic_role=lh.semantic_role,
                requested_output_meaning=lh.requested_output_meaning,
                nl_intent=lh.nl_intent,
            ),
        ))

    # Build operator nodes.
    op_nodes: List[OperatorNode] = []
    for op_decl in output.operator_nodes:
        op_nodes.append(OperatorNode(
            node_id=op_decl.node_id,
            operator_name=op_decl.operator_name,
            params=dict(op_decl.params),
        ))

    edges = [
        WorkflowEdge(
            source_node_id=e.source_node_id,
            target_node_id=e.target_node_id,
            target_input_slot=e.target_input_slot,
        )
        for e in output.edges
    ]
    literals = [
        LiteralBinding(
            target_node_id=b.target_node_id,
            target_input_slot=b.target_input_slot,
            value=b.value,
        )
        for b in output.literal_bindings
    ]

    nodes = leaf_nodes + op_nodes

    # ShapeSpec's own construction-time validator (unique node_ids,
    # terminal exists, edges/literals reference real nodes) gates the
    # rest.
    try:
        return ShapeSpec(
            workflow_id=output.workflow_id,
            nodes=nodes,
            edges=edges,
            literal_bindings=literals,
            terminal_node_id=output.terminal_node_id,
        )
    except Exception as exc:
        raise ComposerOutputError(
            f"Composer LLM output failed ShapeSpec construction: {exc}"
        ) from exc


# ============================================================================
# PURE LOGIC — LLM REPAIR OUTPUT → ShapePatch list
# ============================================================================


def llm_repair_output_to_patches(
    output: ComposerRepairLLMOutput,
) -> Sequence["ShapePatch"]:
    """Transform the Composer's repair structured output into a sequence
    of typed ``ShapePatch`` instances.

    Returns an empty sequence when the Composer refused (no additive
    patch was possible).  The Assembler treats an empty return as
    "no repair possible" and routes to REFUSED.

    The adapter whitelist (``InsertAdapterNode.adapter_operator_name``
    Literal["convert_units", "align_series"]) is enforced by Pydantic
    at construction time — any other name fails here with a clean
    error message that the caller converts into an empty patch list.
    """
    # Import inside the function to keep the module-level import graph
    # acyclic — assembler depends on contracts which depends on this
    # module's siblings, and we only need these types at call time.
    from orchestrator.open_dag.assembler import (
        InsertAdapterNode,
        RewireEdge,
    )

    if output.refusal is not None:
        return ()

    patches: List["ShapePatch"] = []
    for p in output.insert_adapter_patches:
        try:
            patches.append(InsertAdapterNode(
                on_edge_source=p.on_edge_source,
                on_edge_target=p.on_edge_target,
                on_edge_slot=p.on_edge_slot,
                adapter_node_id=p.adapter_node_id,
                adapter_operator_name=p.adapter_operator_name,  # type: ignore[arg-type]
                adapter_input_slot=p.adapter_input_slot,
                adapter_params=dict(p.adapter_params),
                adapter_literal_bindings=[
                    LiteralBinding(
                        target_node_id=lb.target_node_id,
                        target_input_slot=lb.target_input_slot,
                        value=lb.value,
                    )
                    for lb in p.adapter_literal_bindings
                ],
            ))
        except Exception as exc:
            # Adapter whitelist violation or other typed-construction
            # failure — log and skip; an empty-patch outcome is
            # equivalent to a refusal at the Assembler boundary.
            logger.warning(
                "Composer repair patch (insert_adapter) failed to "
                "construct: %s.  Patch dropped.",
                exc,
            )
            continue
    for r in output.rewire_patches:
        try:
            patches.append(RewireEdge(
                source_node_id=r.source_node_id,
                target_node_id=r.target_node_id,
                current_target_input_slot=r.current_target_input_slot,
                new_target_input_slot=r.new_target_input_slot,
            ))
        except Exception as exc:
            logger.warning(
                "Composer repair patch (rewire_edge) failed to "
                "construct: %s.  Patch dropped.",
                exc,
            )
            continue
    return tuple(patches)


# ============================================================================
# COMPOSER — orchestrating class (LLM-backed)
# ============================================================================


class Composer:
    """The PR-7 L3 Composer.

    Constructed once per session with an LLM model name + sampling
    params.  Exposes two async methods:

      - ``compose(prompt, intent_tag, decomposition)`` — returns a
        ``ShapeSpec`` or a ``ComposerRefusal``.
      - ``repair(workflow, errors)`` — async ``ShapePatchProvider``-shape
        callback; returns a sequence of additive ``ShapePatch`` instances
        (never a new ShapeSpec, never a primitive swap).

    Both methods bound their LLM call by ``timeout_s``.  Both convert
    LLM exceptions / timeouts into a refusal flavour (a
    ``ComposerRefusal`` for ``compose``; an empty patch sequence for
    ``repair``) so the Assembler / Boundary B path always sees a
    structured outcome.

    The system prompt is constructed once at ``open()`` and pinned with
    an Anthropic ephemeral cache_control breakpoint for cost efficiency.
    """

    def __init__(
        self,
        *,
        model_name: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> None:
        # Plan D7: the model is a config knob (COMPOSER_MODEL, default
        # claude-opus-4-6).  An explicit model_name still overrides (tests
        # pin a model); ``Composer()`` picks up the configured default.
        self._model_name = model_name or COMPOSER_MODEL
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Open-on-first-use scaffolding; populated by open().
        self._is_open: bool = False
        self._catalogue: Dict[str, OperatorCard] = {}
        self._cached_system_message: Optional[Any] = None
        self._cached_repair_system_message: Optional[Any] = None
        self._compose_model: Optional[Any] = None
        self._repair_model: Optional[Any] = None
        # PR-10C Codex F7: capture the most recent compose call's
        # LLM-authored wiring rationale.  Read by the pipeline after
        # Composer.compose() returns and threaded into
        # IntentChain.from_inputs(composer_llm_rationale=...).  Reset
        # on every compose call so a stale rationale never bleeds
        # across queries.
        self._last_compose_rationale: str = ""

    @property
    def last_compose_rationale(self) -> str:
        """The LLM-authored wiring rationale from the most recent
        successful ``compose()`` call.  Empty string when (a) compose
        hasn't run yet, (b) the LLM didn't supply a rationale, or
        (c) compose returned a ComposerRefusal."""
        return self._last_compose_rationale

    # ------------------------------------------------------------------
    # LIFECYCLE
    # ------------------------------------------------------------------

    def open(self) -> None:
        """Build the cached system message + structured-output model.

        Idempotent — calling ``open()`` twice is a no-op.  The
        operator catalogue is loaded eagerly (16 cards, ~12K chars
        total) and cached on the instance; the system prompt and the
        ChatAnthropic model are also cached.
        """
        if self._is_open:
            return

        # Late import — keep the module loadable even when the optional
        # LangChain Anthropic stack isn't installed (tests can use the
        # session-level `_compose_model = mock` pattern).
        from langchain_anthropic import ChatAnthropic
        from langchain_core.messages import SystemMessage

        from orchestrator.prompts import (
            COMPOSER_REPAIR_PROMPT,
            COMPOSER_SYSTEM_PROMPT,
        )

        self._catalogue = render_operator_catalogue()

        compose_system_text = build_compose_system_prompt_text(
            self._catalogue, COMPOSER_SYSTEM_PROMPT,
        )
        repair_system_text = build_repair_system_prompt_text(
            self._catalogue, COMPOSER_REPAIR_PROMPT,
        )

        self._cached_system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": compose_system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )
        self._cached_repair_system_message = SystemMessage(
            content=[
                {
                    "type": "text",
                    "text": repair_system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )

        self._compose_model = ChatAnthropic(
            model=self._model_name,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ).with_structured_output(ComposerLLMOutput, include_raw=True)
        self._repair_model = ChatAnthropic(
            model=self._model_name,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ).with_structured_output(ComposerRepairLLMOutput, include_raw=True)

        self._is_open = True
        logger.info(
            "Composer ready (model=%s, %d operators in catalogue, "
            "compose_system_text=%d chars, repair_system_text=%d chars)",
            self._model_name,
            len(self._catalogue),
            len(compose_system_text),
            len(repair_system_text),
        )

    def close(self) -> None:
        """Drop the cached model + system message references.

        Idempotent — calling ``close()`` twice is a no-op.  After
        ``close``, the next ``compose`` / ``repair`` call will fail
        with a ``RuntimeError`` until ``open`` is called again.
        """
        self._is_open = False
        self._catalogue = {}
        self._cached_system_message = None
        self._cached_repair_system_message = None
        self._compose_model = None
        self._repair_model = None

    # ------------------------------------------------------------------
    # COMPOSE
    # ------------------------------------------------------------------

    async def compose(
        self,
        *,
        prompt: str,
        intent_tag: IntentTag,
        decomposition: Sequence[EconomicQuantity],
        timeout_s: float = 15.0,
        correction: Optional[str] = None,
    ) -> ComposeResult:
        """Emit a ``ShapeSpec`` (or a ``ComposerRefusal``) for the
        user's prompt.

        Parameters
        ----------
        correction :
            Orchestration-upgrade plan D1/D3: when set, this is the
            SPECIFIC reason the PREVIOUS attempt failed a deterministic
            check or the coverage gate (e.g. "terminal produced a Series
            but the question needs a ScalarMetric").  It is appended to
            the user message so the LLM RE-COMPOSES a corrected DAG.
            The pipeline calls compose() with a correction at most once
            per turn (bounded re-compose, then hard-block) — this is a
            FRESH compose, distinct from the adapter-only ``repair()``.

        Returns
        -------
        Union[ShapeSpec, ComposerRefusal]
            A typed shape on success; a refusal envelope when the LLM
            declined or the output couldn't be structurally
            transformed.  Never raises (refusal is first-class even on
            timeout / LLM error).
        """
        from langchain_core.messages import HumanMessage

        # PR-10C Codex F7: reset rationale on every compose call so
        # a stale value never bleeds across queries.  Populated only
        # when the LLM returns a non-empty rationale AND the post-
        # LLM transform succeeds (see end of this method).
        self._last_compose_rationale = ""

        if not self._is_open:
            raise RuntimeError(
                "Composer is not open.  Call `composer.open()` before "
                "compose()."
            )
        if (
            self._compose_model is None
            or self._cached_system_message is None
        ):
            raise RuntimeError(
                "Composer is open but compose_model / system message "
                "are missing — internal state corrupted."
            )

        user_text = render_composer_user_message(
            prompt=prompt,
            intent_tag=intent_tag,
            decomposition=decomposition,
            correction=correction,
        )
        messages = [
            self._cached_system_message,
            HumanMessage(content=user_text),
        ]
        try:
            result = await asyncio.wait_for(
                self._compose_model.ainvoke(messages),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Composer LLM timed out after %.1fs in compose", timeout_s,
            )
            return ComposerRefusal(
                reason=(
                    f"Composer LLM call timed out after {timeout_s}s "
                    "without producing a structured output."
                ),
            )
        except Exception as exc:
            logger.exception("Composer LLM call failed in compose")
            return ComposerRefusal(
                reason=(
                    f"Composer LLM call failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else None
        parsing_error = (
            result.get("parsing_error") if isinstance(result, dict) else None
        )
        if raw is not None:
            _log_usage("composer.compose", raw)

        if parsed is None:
            return ComposerRefusal(
                reason=(
                    f"Composer LLM did not produce a valid "
                    f"ComposerLLMOutput; parsing_error="
                    f"{parsing_error!r}."
                ),
            )

        if parsed.refusal is not None:
            return ComposerRefusal(reason=parsed.refusal)

        try:
            shape = llm_output_to_shape_spec(parsed)
        except ComposerOutputError as exc:
            return ComposerRefusal(reason=str(exc))
        # PR-10C Codex F7: capture the LLM-authored wiring rationale
        # so the pipeline can thread it through to IntentChain.from_inputs
        # (via the composer_llm_rationale kwarg added in PR-10B F14).
        # Stored on the instance because Composer.compose's return
        # type is the existing ShapeSpec | ComposerRefusal union;
        # changing it to a tuple would break every caller.  The
        # pipeline reads ``composer.last_compose_rationale`` AFTER
        # the compose call returns.
        self._last_compose_rationale = (parsed.rationale or "").strip()
        return shape

    # ------------------------------------------------------------------
    # REPAIR (ShapePatchProvider Protocol implementation)
    # ------------------------------------------------------------------

    async def repair(
        self,
        *,
        workflow: "Workflow",
        errors: Sequence["ValidationError"],
        timeout_s: float = 10.0,
    ) -> Sequence["ShapePatch"]:
        """Emit a sequence of additive patches for an Assembler repair
        round.

        Per the plan's discipline: only ``InsertAdapterNode`` and
        ``RewireEdge`` patches; never a new shape; never a primitive
        swap.  Returns an empty sequence when the Composer cannot
        repair (refusal); the Assembler treats that as "no repair
        possible" and refuses the assembly.

        Bridges the Assembler's sync Protocol (``ShapePatchProvider``)
        to this async method via ``Assembler.shape_patch_provider``-
        side wrapping in PR-10's wiring layer.  Tests call this method
        directly with mocked LLM output.
        """
        from langchain_core.messages import HumanMessage

        if not self._is_open:
            raise RuntimeError(
                "Composer is not open.  Call `composer.open()` before "
                "repair()."
            )
        if (
            self._repair_model is None
            or self._cached_repair_system_message is None
        ):
            raise RuntimeError(
                "Composer is open but repair_model / system message "
                "are missing — internal state corrupted."
            )

        user_text = render_composer_repair_user_message(
            workflow=workflow, errors=errors,
        )
        messages = [
            self._cached_repair_system_message,
            HumanMessage(content=user_text),
        ]
        try:
            result = await asyncio.wait_for(
                self._repair_model.ainvoke(messages),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Composer LLM timed out after %.1fs in repair", timeout_s,
            )
            return ()
        except Exception:
            logger.exception("Composer LLM call failed in repair")
            return ()

        raw = result.get("raw") if isinstance(result, dict) else None
        parsed = result.get("parsed") if isinstance(result, dict) else None
        if raw is not None:
            _log_usage("composer.repair", raw)
        if parsed is None:
            return ()
        return llm_repair_output_to_patches(parsed)

    # ------------------------------------------------------------------
    # SYNC WRAPPER — satisfies the Assembler's ShapePatchProvider Protocol
    # ------------------------------------------------------------------

    def repair_sync(
        self,
        workflow: "Workflow",
        errors: Sequence["ValidationError"],
    ) -> Sequence["ShapePatch"]:
        """Synchronous wrapper around ``repair`` matching the
        ``ShapePatchProvider`` Protocol declared in
        ``orchestrator.open_dag.assembler``.

        PR-10B Codex F4: the Assembler's repair callback signature is
        sync ``(workflow, errors) -> Sequence[ShapePatch]``.  This
        wrapper bridges the gap by running the async ``repair`` to
        completion in the current event loop (when there is none) or
        in a fresh loop on a worker thread (when called from inside
        an existing async context — e.g. the OpenDagPipeline).

        The Assembler invokes this synchronously inside ``assemble``;
        it MUST NOT block the caller's async loop.  Implementation
        picks the right path based on whether a loop is currently
        running.

        Returns an empty sequence on any failure mode — the Assembler
        treats an empty patch list as "no repair possible" and
        gracefully refuses the assembly.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop running — safe to use asyncio.run.
            try:
                return asyncio.run(
                    self.repair(workflow=workflow, errors=errors),
                )
            except Exception:
                logger.exception(
                    "Composer.repair_sync (no-loop path) raised",
                )
                return ()

        # A loop IS running.  asyncio.run() would error; instead,
        # offload to a fresh loop on a worker thread and block this
        # thread until it returns.  The Assembler runs in the same
        # thread that called assemble(); the pipeline awaits
        # assemble() so this thread is FREE to block briefly.
        import concurrent.futures

        def _runner():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(
                    self.repair(workflow=workflow, errors=errors),
                )
            finally:
                new_loop.close()

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(_runner).result()
        except Exception:
            logger.exception(
                "Composer.repair_sync (worker-thread path) raised",
            )
            return ()


# ============================================================================
# SYSTEM PROMPT TEXT BUILDERS
# ============================================================================
#
# The actual prompt prose lives in ``orchestrator.prompts`` (per the
# convention used by SUPERVISOR_SYSTEM_PROMPT and
# SELECTOR_FILL_LEAF_SYSTEM_PROMPT).  Composer adds the dynamic blocks
# (catalogue, intent table, artifact types, golden few-shots) at
# open() time using these helpers, so the prose body stays a static
# constant in ``orchestrator.prompts`` (cache-friendly, easy to audit).


def build_compose_system_prompt_text(
    catalogue: Dict[str, OperatorCard],
    static_prompt_body: str,
) -> str:
    """Concatenate the static composer prompt body with the dynamic
    sections (intent table, artifact types, operator catalogue, golden
    few-shots).  Used by ``Composer.open()``.
    """
    sections: List[str] = [
        static_prompt_body.rstrip(),
        "",
        _artifact_types_section(),
        "",
        _intent_section_for_prompt(catalogue),
        "",
        render_operator_catalogue_block(catalogue),
        "",
        "GOLDEN FEW-SHOTS (canonical shapes — one per intent family):",
        render_golden_few_shots(),
    ]
    return "\n".join(sections)


def build_repair_system_prompt_text(
    catalogue: Dict[str, OperatorCard],
    static_repair_prompt_body: str,
) -> str:
    """Concatenate the static repair prompt body with the operator
    catalogue (so the Composer knows the adapter operators' slot
    signatures).  Used by ``Composer.open()``."""
    sections: List[str] = [
        static_repair_prompt_body.rstrip(),
        "",
        _artifact_types_section(),
        "",
        render_operator_catalogue_block(catalogue),
    ]
    return "\n".join(sections)


# ============================================================================
# USAGE LOGGING
# ============================================================================


def _log_usage(label: str, response) -> None:
    """Mirror ``orchestrator.domain_agent._log_usage`` so Composer LLM
    calls show up in the same observability format as Supervisor and
    Selector calls."""
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return
    if not isinstance(meta, dict):
        try:
            meta = dict(meta)
        except (TypeError, ValueError):
            return
    details = meta.get("input_token_details", {}) or {}
    logger.info(
        "[%s] tokens input=%s output=%s cache_create=%s cache_read=%s",
        label,
        meta.get("input_tokens"),
        meta.get("output_tokens"),
        details.get("cache_creation"),
        details.get("cache_read"),
    )


__all__ = [
    # Schemas
    "ComposerLLMOutput",
    "ComposerRepairLLMOutput",
    "ComposerRefusal",
    "ComposerOutputError",
    # Class
    "Composer",
    # Pure helpers
    "render_composer_user_message",
    "render_composer_repair_user_message",
    "render_operator_catalogue_block",
    "build_compose_system_prompt_text",
    "build_repair_system_prompt_text",
    "llm_output_to_shape_spec",
    "llm_repair_output_to_patches",
]

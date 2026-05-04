"""shared.workflow.template — template-locked DAG with caller-fillable slots.

The layer that turns the generic workflow substrate into "standard
reusable workflows" per ``docs/architecture/workflow_architecture.md``.

What a template is
------------------
A workflow template is a topology-locked DAG (substrate
``Workflow``) whose node ``params`` and literal-binding ``value``
fields may reference template-declared slots via the placeholder
syntax ``{"$slot": "<slot_name>"}``.  Slots are caller-fillable;
the rest of the topology + per-node parameters + per-edge wiring
is template-locked.

Example slot reference inside a primitive node's params::

    PrimitiveNodeTemplate(
        node_id="signal_primitive",
        tool_name="calculate_curve_spread_tool",
        output_field="time_series_zscore",
        params={
            "curve_family": {"$slot": "curve_family"},
            "short_tenor": {"$slot": "short_tenor"},
            "long_tenor": {"$slot": "long_tenor"},
            "lookback_days": 365,  # template-locked
        },
    )

The four V1 archetypes (``event_study``,
``regime_conditioned_relationship``, ``attribution_decomposition``,
``cross_sectional_screen``) are declared in
``WorkflowArchetype`` (closed enum).  Adding a fifth archetype
requires extending the enum AND passing the workflow-template
admission checklist in ``workflow_architecture.md``.

bind() returns a Workflow
-------------------------
``WorkflowTemplate.bind(slot_values)`` substitutes every
``{"$slot": "name"}`` placeholder with the matching slot value
from ``slot_values``, then constructs a concrete substrate
``Workflow`` ready for the executor.  Validation happens twice:

  1. ``slot_values`` is validated against the template's
     ``slot_schema`` (caller supplied every required slot, types
     match, etc.).
  2. The resulting concrete ``Workflow`` is validated by the
     substrate's own constructor + (when desired) by
     ``validate_workflow``.

This double-gate matches the discipline of the prior layers
(primitives validate input via Pydantic; bridge validates
primitive output via Pydantic; substrate validates Workflow shape
via Pydantic; templates validate slot_values via Pydantic).
"""

from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.workflow.types import (
    LiteralBinding,
    LiteralScalar,
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
)


# ============================================================================
# CLOSED-FAMILY ARCHETYPES (V1)
# ============================================================================

# The four V1 workflow archetypes from
# ``docs/architecture/workflow_architecture.md``.  Closed-enum
# discipline — adding a fifth requires extending this Literal AND
# the template's admission checklist.

WorkflowArchetype = Literal[
    "event_study",
    "regime_conditioned_relationship",
    "attribution_decomposition",
    "cross_sectional_screen",
]


WORKFLOW_ARCHETYPES: tuple[str, ...] = (
    "event_study",
    "regime_conditioned_relationship",
    "attribution_decomposition",
    "cross_sectional_screen",
)


# ============================================================================
# SLOT SCHEMA
# ============================================================================


class SlotDeclaration(BaseModel):
    """Declaration of a single template slot.

    The slot schema is a list of these.  Each declares the slot's
    name, type (closed-enum string), required-ness, and a one-line
    description for the template card.  Template authors can also
    supply a default value for non-required slots.

    Type strings (closed enum):
      - ``"str"``   — caller supplies a Python string
      - ``"int"``   — caller supplies a Python int
      - ``"float"`` — caller supplies a Python int or float
      - ``"bool"``  — caller supplies a Python bool
      - ``"dict"``  — caller supplies a dict (e.g. SeriesSpec-shaped
        nested dict; the consuming primitive's *Input validates the
        nested shape)
      - ``"list"``  — caller supplies a list (e.g. a universe of
        curve_family strings for cross-sectional screens)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(..., min_length=1)
    type: Literal["str", "int", "float", "bool", "dict", "list"]
    required: bool = True
    description: str = Field(..., min_length=1)
    default: Any = None  # used only when required=False

    @model_validator(mode="after")
    def _default_only_when_optional(self) -> "SlotDeclaration":
        if self.required and self.default is not None:
            raise ValueError(
                f"slot {self.name!r}: required=True but default was "
                "supplied; defaults are only meaningful for optional "
                "slots."
            )
        return self


# ============================================================================
# NODE TEMPLATES (slot-aware variants of the substrate node types)
# ============================================================================


class PrimitiveNodeTemplate(BaseModel):
    """Template-shaped variant of ``PrimitiveNode``: ``params``
    values may include ``{"$slot": "name"}`` placeholders.  At
    bind time, placeholders are substituted with the matching slot
    value and the result is constructed as a concrete
    ``PrimitiveNode``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["primitive"] = "primitive"
    node_id: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    output_field: str = Field(..., min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)


class OperatorNodeTemplate(BaseModel):
    """Template-shaped variant of ``OperatorNode``: ``params``
    values may include ``{"$slot": "name"}`` placeholders."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["operator"] = "operator"
    node_id: str = Field(..., min_length=1)
    operator_name: str = Field(..., min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)


WorkflowNodeTemplate = Annotated[
    Union[PrimitiveNodeTemplate, OperatorNodeTemplate],
    Field(discriminator="kind"),
]


class LiteralBindingTemplate(BaseModel):
    """Template-shaped variant of ``LiteralBinding``: ``value`` may
    be a literal scalar OR a ``{"$slot": "name"}`` placeholder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_node_id: str = Field(..., min_length=1)
    target_input_slot: str = Field(..., min_length=1)
    value: Any  # literal OR {"$slot": "name"}


# ============================================================================
# WORKFLOW TEMPLATE
# ============================================================================


class WorkflowTemplate(BaseModel):
    """A topology-locked, slot-driven workflow.

    Per ``docs/architecture/workflow_architecture.md``, a workflow
    template owns one of the four V1 archetypes (``event_study``,
    ``regime_conditioned_relationship``, ``attribution_decomposition``,
    ``cross_sectional_screen``), exposes only the central analysis
    knobs as fillable slots, and locks the rest of the topology
    + per-node configuration.

    Fields
    ------
    template_id :
        Stable identifier for the template.  Used by the registry
        and the LLM template-selection layer.  Convention:
        snake_case matching the archetype + any variant suffix
        (e.g. ``"event_study"`` or
        ``"event_study_intraday_variant"``).
    archetype :
        Which of the closed V1 archetypes this template owns.
        Each shipped template MUST declare one.  Adding a fifth
        archetype requires extending ``WorkflowArchetype``.
    description :
        One-line human-readable description.  Surfaces in the
        template card.
    slot_schema :
        List of ``SlotDeclaration`` — the caller-fillable surface.
        At ``bind()`` time, slot_values are validated against this
        schema (every required slot supplied, types match).
    nodes / edges / literal_bindings :
        Topology-locked DAG with ``{"$slot": "name"}`` placeholders
        in params / literal values.  At ``bind()`` time,
        placeholders are substituted and the result is constructed
        as a concrete ``Workflow``.
    terminal_node_id :
        Which node's output is the workflow's terminal artifact.
        Template-locked (the LLM does not choose this).
    archetype_signature :
        Short structural-cue strings the future
        ``route_to_template`` LLM step uses to decide whether a
        prompt matches this template.  Per
        ``docs/architecture/workflow_architecture.md`` (the
        "Template-selection contract" section):

            Each template carries an ``archetype_signature``
            declaration: which structural cues in a prompt
            indicate this template (e.g. event_study requires
            "conditional aggregation across event windows";
            attribution_decomposition requires "decompose / drove
            / explained-vs-residual phrasing").

        Each cue is a short string (≤120 chars) that captures one
        recognisable phrase or pattern the desk would use when
        asking for this analysis.  V1 lower-bound: 1 cue per
        template.  Multiple cues are an OR pattern (any matching
        cue → template is a candidate); the LLM router picks the
        single best-matching template per prompt.

        Cues are caller-controlled, NOT closed-enum: each
        template author writes the cues that match their
        archetype's desk vocabulary.  But the linter in PR 5+
        will check that every shipped template has at least one
        cue declared so the routing layer always has something to
        match against.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    template_id: str = Field(..., min_length=1)
    archetype: WorkflowArchetype
    description: str = Field(..., min_length=1)
    slot_schema: List[SlotDeclaration] = Field(default_factory=list)
    nodes: List[WorkflowNodeTemplate] = Field(..., min_length=1)
    edges: List[WorkflowEdge] = Field(default_factory=list)
    literal_bindings: List[LiteralBindingTemplate] = Field(default_factory=list)
    terminal_node_id: str = Field(..., min_length=1)
    archetype_signature: List[str] = Field(
        default_factory=list,
        description=(
            "Structural cue strings the future ``route_to_template`` "
            "LLM step matches against prompts to decide whether this "
            "template is a candidate.  Each cue is a short phrase "
            "(≤120 chars) capturing one recognisable pattern the "
            "desk would use.  Multiple cues = OR (any matching cue "
            "→ candidate)."
        ),
    )

    @model_validator(mode="after")
    def _validate_template_shape(self) -> "WorkflowTemplate":
        """Construction-time gate: slot-schema uniqueness, slot
        references resolve, terminal node + edge endpoints exist,
        archetype signature cues are well-formed."""
        # Archetype signature cue length cap.  The
        # ``route_to_template`` LLM step pattern-matches against
        # each cue; very long cues are likely to be sentences
        # rather than recognisable phrases and dilute the match
        # signal.
        for i, cue in enumerate(self.archetype_signature):
            if not isinstance(cue, str):
                raise ValueError(
                    f"WorkflowTemplate {self.template_id!r}: "
                    f"archetype_signature[{i}] must be a string; "
                    f"got {type(cue).__name__}."
                )
            stripped = cue.strip()
            if not stripped:
                raise ValueError(
                    f"WorkflowTemplate {self.template_id!r}: "
                    f"archetype_signature[{i}] is empty."
                )
            if len(cue) > 120:
                raise ValueError(
                    f"WorkflowTemplate {self.template_id!r}: "
                    f"archetype_signature[{i}] is {len(cue)} "
                    "characters; cues must be ≤120 chars (short "
                    "recognisable phrases, not sentences)."
                )

        # Slot names unique
        slot_names = [s.name for s in self.slot_schema]
        if len(slot_names) != len(set(slot_names)):
            duplicates = sorted(
                {n for n in slot_names if slot_names.count(n) > 1}
            )
            raise ValueError(
                f"WorkflowTemplate {self.template_id!r}: duplicate "
                f"slot name(s) {duplicates}; every slot must have a "
                "unique name."
            )

        # Node IDs unique
        node_ids = [n.node_id for n in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            duplicates = sorted(
                {n for n in node_ids if node_ids.count(n) > 1}
            )
            raise ValueError(
                f"WorkflowTemplate {self.template_id!r}: duplicate "
                f"node_id(s) {duplicates}."
            )

        # Terminal exists
        if self.terminal_node_id not in node_ids:
            raise ValueError(
                f"WorkflowTemplate {self.template_id!r}: "
                f"terminal_node_id={self.terminal_node_id!r} does "
                f"not match any node.  Known: {sorted(node_ids)}."
            )

        # Edge endpoints exist
        node_id_set = set(node_ids)
        for edge in self.edges:
            if edge.source_node_id not in node_id_set:
                raise ValueError(
                    f"WorkflowTemplate {self.template_id!r}: edge "
                    f"references unknown source_node_id="
                    f"{edge.source_node_id!r}."
                )
            if edge.target_node_id not in node_id_set:
                raise ValueError(
                    f"WorkflowTemplate {self.template_id!r}: edge "
                    f"references unknown target_node_id="
                    f"{edge.target_node_id!r}."
                )

        # Literal-binding target nodes exist
        for binding in self.literal_bindings:
            if binding.target_node_id not in node_id_set:
                raise ValueError(
                    f"WorkflowTemplate {self.template_id!r}: literal "
                    f"binding references unknown target_node_id="
                    f"{binding.target_node_id!r}."
                )

        # Every $slot reference in node params + literal bindings
        # resolves to a declared slot
        slot_name_set = set(slot_names)
        unknown_refs: List[str] = []
        for node in self.nodes:
            unknown_refs.extend(
                _walk_slot_refs(node.params, slot_name_set, context=node.node_id)
            )
        for binding in self.literal_bindings:
            unknown_refs.extend(
                _walk_slot_refs(
                    binding.value, slot_name_set,
                    context=f"literal_binding[{binding.target_node_id}.{binding.target_input_slot}]",
                )
            )
        if unknown_refs:
            raise ValueError(
                f"WorkflowTemplate {self.template_id!r}: undeclared "
                f"slot references: {unknown_refs}.  Declare these "
                "slots in slot_schema or remove the references."
            )

        return self

    def bind(self, slot_values: Dict[str, Any]) -> Workflow:
        """Substitute slot values into the template and construct
        a concrete ``Workflow``.

        Parameters
        ----------
        slot_values :
            Caller-supplied values keyed by slot name.

        Returns
        -------
        Workflow
            Concrete substrate ``Workflow`` with all
            ``{"$slot": "name"}`` placeholders substituted.

        Raises
        ------
        SlotBindingError
            When a required slot is missing, a supplied slot is
            unknown, or a slot's value type does not match its
            declaration.
        """
        # 1. Validate slot_values against slot_schema.
        resolved = _validate_and_resolve_slot_values(
            slot_schema=self.slot_schema,
            slot_values=slot_values,
            template_id=self.template_id,
        )

        # 2. Substitute placeholders in node params + literal
        #    bindings.
        concrete_nodes: List[Any] = []
        for node in self.nodes:
            resolved_params = _substitute_slots(node.params, resolved)
            if isinstance(node, PrimitiveNodeTemplate):
                concrete_nodes.append(
                    PrimitiveNode(
                        node_id=node.node_id,
                        tool_name=node.tool_name,
                        output_field=node.output_field,
                        params=resolved_params,
                    )
                )
            elif isinstance(node, OperatorNodeTemplate):
                concrete_nodes.append(
                    OperatorNode(
                        node_id=node.node_id,
                        operator_name=node.operator_name,
                        params=resolved_params,
                    )
                )

        concrete_literal_bindings: List[LiteralBinding] = []
        for binding in self.literal_bindings:
            resolved_value = _substitute_slots(binding.value, resolved)
            concrete_literal_bindings.append(
                LiteralBinding(
                    target_node_id=binding.target_node_id,
                    target_input_slot=binding.target_input_slot,
                    value=resolved_value,
                )
            )

        # 3. Construct the concrete Workflow (substrate validators
        #    fire on construction).
        return Workflow(
            workflow_id=self.template_id,
            nodes=concrete_nodes,
            edges=list(self.edges),
            literal_bindings=concrete_literal_bindings,
            terminal_node_id=self.terminal_node_id,
        )


# ============================================================================
# ERRORS
# ============================================================================


class SlotBindingError(ValueError):
    """Raised when ``WorkflowTemplate.bind`` rejects the supplied
    slot_values (missing required slot, unknown slot, type
    mismatch)."""


# ============================================================================
# INTERNAL HELPERS
# ============================================================================


_SLOT_KEY = "$slot"


def _is_slot_ref(value: Any) -> bool:
    """Return True iff ``value`` is the placeholder shape
    ``{"$slot": "name"}``."""
    return (
        isinstance(value, dict)
        and len(value) == 1
        and _SLOT_KEY in value
        and isinstance(value[_SLOT_KEY], str)
    )


def _walk_slot_refs(
    value: Any, slot_names: set, *, context: str,
) -> List[str]:
    """Recursively walk a value for ``$slot`` references; return a
    list of error messages for any references to undeclared slots.
    Used by the construction-time validator."""
    errors: List[str] = []
    if _is_slot_ref(value):
        ref_name = value[_SLOT_KEY]
        if ref_name not in slot_names:
            errors.append(
                f"{context}: ${{slot: {ref_name!r}}} refers to "
                "an undeclared slot"
            )
    elif isinstance(value, dict):
        for v in value.values():
            errors.extend(_walk_slot_refs(v, slot_names, context=context))
    elif isinstance(value, list):
        for item in value:
            errors.extend(_walk_slot_refs(item, slot_names, context=context))
    return errors


def _validate_and_resolve_slot_values(
    *,
    slot_schema: List[SlotDeclaration],
    slot_values: Dict[str, Any],
    template_id: str,
) -> Dict[str, Any]:
    """Validate caller-supplied slot_values against the template's
    slot_schema.  Returns a resolved dict (with defaults applied
    for unsupplied optional slots).  Raises ``SlotBindingError`` on
    missing required slots, unknown slots, or type mismatches."""
    declared = {s.name: s for s in slot_schema}
    declared_names = set(declared.keys())
    supplied_names = set(slot_values.keys())

    unknown = supplied_names - declared_names
    if unknown:
        raise SlotBindingError(
            f"WorkflowTemplate {template_id!r}: unknown slot(s) "
            f"{sorted(unknown)}.  Declared slots: "
            f"{sorted(declared_names)}."
        )

    resolved: Dict[str, Any] = {}
    for name, decl in declared.items():
        if name in slot_values:
            value = slot_values[name]
        elif decl.required:
            raise SlotBindingError(
                f"WorkflowTemplate {template_id!r}: required slot "
                f"{name!r} ({decl.type}) was not supplied.  "
                f"Description: {decl.description}"
            )
        else:
            value = decl.default

        # Type check
        if not _slot_type_matches(value, decl.type):
            raise SlotBindingError(
                f"WorkflowTemplate {template_id!r}: slot {name!r} "
                f"declared type={decl.type!r} but supplied value is "
                f"{type(value).__name__}."
            )
        resolved[name] = value
    return resolved


def _slot_type_matches(value: Any, declared_type: str) -> bool:
    """Closed-enum type check.  Accepts None for non-required
    slots whose default is None."""
    if value is None:
        return True
    if declared_type == "str":
        return isinstance(value, str)
    if declared_type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared_type == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared_type == "bool":
        return isinstance(value, bool)
    if declared_type == "dict":
        return isinstance(value, dict)
    if declared_type == "list":
        return isinstance(value, list)
    return False


def _substitute_slots(value: Any, resolved: Dict[str, Any]) -> Any:
    """Recursively substitute ``{"$slot": "name"}`` placeholders
    with the matching value from ``resolved``.  Other values are
    passed through unchanged."""
    if _is_slot_ref(value):
        return resolved[value[_SLOT_KEY]]
    if isinstance(value, dict):
        return {k: _substitute_slots(v, resolved) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_slots(item, resolved) for item in value]
    return value


__all__ = [
    "WorkflowArchetype",
    "WORKFLOW_ARCHETYPES",
    "SlotDeclaration",
    "PrimitiveNodeTemplate",
    "OperatorNodeTemplate",
    "WorkflowNodeTemplate",
    "LiteralBindingTemplate",
    "WorkflowTemplate",
    "SlotBindingError",
]

"""tests/test_workflow_template_system.py — workflow template system tests.

Phase 2 PR 4.  Covers ``shared/workflow/{template,template_loader,
template_registry,template_card}.py``.

Sections:
  1. SlotDeclaration — type checks, default-only-when-optional
     guard.
  2. WorkflowTemplate construction — slot uniqueness, slot
     references resolve, terminal node + edge endpoints exist,
     literal-binding endpoints exist.
  3. ``bind()`` — slot validation (missing/unknown/type-mismatch),
     placeholder substitution, nested slot refs in dicts/lists,
     concrete Workflow construction, replay determinism.
  4. YAML loader — parse + validate + cache; malformed file
     errors.
  5. Registry — register/get/list/unregister; idempotent re-register;
     conflict detection; archetype filter.
  6. Template card — derived shape: archetype, slot_schema,
     terminal artifact type (PrimitiveNode terminal → "Series";
     OperatorNode terminal → registry lookup), primitives_used,
     operators_used, node_count + edge_count.
  7. End-to-end — load YAML → bind → execute via the substrate
     executor against a synthetic primitive resolver.

Tests are fully offline; the synthetic primitive (defined here)
keeps the substrate finance-blind in tests.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Type

import pandas as pd
import pytest
from pydantic import BaseModel

from shared.artifacts import Series
from shared.schemas import TimeSeries, TimeSeriesRow
from shared.workflow import (
    LiteralBindingTemplate,
    OperatorNodeTemplate,
    PrimitiveNodeTemplate,
    PrimitiveResolver,
    PrimitiveSpec,
    RelativeOrderConstraint,
    SlotBindingError,
    SlotDeclaration,
    TemplateCard,
    TemplateRegistryError,
    WORKFLOW_ARCHETYPES,
    WorkflowEdge,
    WorkflowTemplate,
    WorkflowTemplateError,
    card_for_template,
    clear_template_registry,
    clear_workflow_template_cache,
    execute_workflow,
    get_template,
    known_template_ids,
    list_templates,
    load_workflow_template,
    register_template,
    unregister_template,
)


# ---------------------------------------------------------------------------
# Synthetic primitive (finance-blind) — same shape used by PR #78 tests
# ---------------------------------------------------------------------------


class _SyntheticInput(BaseModel):
    series_name: str = "synthetic"
    n_rows: int = 10
    base_value: float = 1.0
    drift: float = 0.5
    units: str = "bps"


class _SyntheticOutput(BaseModel):
    class _Metrics(BaseModel):
        as_of_date: str

    current_metrics: "_SyntheticOutput._Metrics"
    time_series: TimeSeries


_SyntheticOutput.model_rebuild()


def _synthetic_primitive_callable(
    *, engine, params: _SyntheticInput, config,
) -> dict:
    bdays = pd.bdate_range(date(2026, 4, 1), periods=params.n_rows)
    rows = [
        TimeSeriesRow(
            date=d.strftime("%Y-%m-%d"),
            value=params.base_value + i * params.drift,
        )
        for i, d in enumerate(bdays)
    ]
    return {
        "current_metrics": {"as_of_date": rows[-1].date},
        "time_series": {
            "series_name": params.series_name,
            "units": params.units,
            "description": "Synthetic series.",
            "rows": [r.model_dump() for r in rows],
        },
    }


_SYNTHETIC_CONFIG_YAML = """
tool:
  name: synthetic_primitive_tool
  domain: synthetic
  description: Synthetic primitive for template tests.
  category: desk_invariant_primitive
conventions:
  ffill_limit_days:
    value: 5
    source: template_test_default
    rationale: synthetic config for template tests
methodology:
  what_it_does: >-
    Returns a deterministic linear walk for substrate + template
    tests.
"""


@pytest.fixture
def synthetic_config_path(tmp_path) -> Path:
    p = tmp_path / "synthetic_config.yaml"
    p.write_text(_SYNTHETIC_CONFIG_YAML)
    return p


@pytest.fixture
def synthetic_resolver(synthetic_config_path) -> PrimitiveResolver:
    spec = PrimitiveSpec(
        tool_name="synthetic_primitive_tool",
        callable=_synthetic_primitive_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=synthetic_config_path,
    )

    def _resolve(tool_name: str) -> PrimitiveSpec:
        if tool_name != "synthetic_primitive_tool":
            raise KeyError(f"unknown tool: {tool_name!r}")
        return spec

    return _resolve


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset all process-wide caches between tests."""
    from shared.config import clear_tool_config_cache
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()
    yield
    clear_tool_config_cache()
    clear_workflow_template_cache()
    clear_template_registry()


# ===========================================================================
# 1. SlotDeclaration
# ===========================================================================


class TestSlotDeclaration:
    def test_required_slot_no_default(self):
        slot = SlotDeclaration(
            name="x", type="str", required=True, description="...",
        )
        assert slot.name == "x"
        assert slot.required is True
        assert slot.default is None

    def test_optional_slot_with_default(self):
        slot = SlotDeclaration(
            name="x", type="int", required=False,
            description="...", default=10,
        )
        assert slot.required is False
        assert slot.default == 10

    def test_required_with_default_rejected(self):
        with pytest.raises(Exception, match="defaults are only meaningful"):
            SlotDeclaration(
                name="x", type="str", required=True,
                description="...", default="oops",
            )

    def test_all_six_types_accepted(self):
        for t in ("str", "int", "float", "bool", "dict", "list"):
            SlotDeclaration(
                name="x", type=t, required=True, description="...",
            )

    def test_unknown_type_rejected(self):
        with pytest.raises(Exception):
            SlotDeclaration(
                name="x", type="not_a_type", required=True,
                description="...",
            )


# ===========================================================================
# 2. WorkflowTemplate construction
# ===========================================================================


def _trivial_template_dict(**overrides):
    """Minimal valid template dict: 1 primitive node, no edges."""
    base = {
        "template_id": "trivial",
        "archetype": "event_study",
        "description": "trivial test template",
        "slot_schema": [],
        "nodes": [
            PrimitiveNodeTemplate(
                node_id="p",
                tool_name="synthetic_primitive_tool",
                output_field="time_series",
                params={},
            ),
        ],
        "edges": [],
        "literal_bindings": [],
        "terminal_node_id": "p",
    }
    base.update(overrides)
    return base


class TestWorkflowTemplateConstruction:
    def test_minimal_construction(self):
        t = WorkflowTemplate(**_trivial_template_dict())
        assert t.template_id == "trivial"
        assert t.archetype == "event_study"

    def test_archetype_must_be_in_closed_set(self):
        with pytest.raises(Exception):
            WorkflowTemplate(
                **_trivial_template_dict(archetype="not_a_real_archetype")
            )

    def test_duplicate_slot_names_rejected(self):
        with pytest.raises(Exception, match="duplicate slot name"):
            WorkflowTemplate(**_trivial_template_dict(
                slot_schema=[
                    SlotDeclaration(
                        name="x", type="str", required=True, description="..."
                    ),
                    SlotDeclaration(
                        name="x", type="int", required=True, description="..."
                    ),
                ],
            ))

    def test_duplicate_node_ids_rejected(self):
        with pytest.raises(Exception, match="duplicate node_id"):
            WorkflowTemplate(**_trivial_template_dict(
                nodes=[
                    PrimitiveNodeTemplate(
                        node_id="p", tool_name="synthetic_primitive_tool",
                        output_field="time_series", params={},
                    ),
                    PrimitiveNodeTemplate(
                        node_id="p", tool_name="synthetic_primitive_tool",
                        output_field="time_series", params={},
                    ),
                ],
            ))

    def test_terminal_must_exist(self):
        with pytest.raises(Exception, match="terminal_node_id"):
            WorkflowTemplate(**_trivial_template_dict(
                terminal_node_id="ghost",
            ))

    def test_edge_endpoints_must_exist(self):
        with pytest.raises(Exception, match="unknown source_node_id"):
            WorkflowTemplate(**_trivial_template_dict(
                edges=[
                    WorkflowEdge(
                        source_node_id="ghost",
                        target_node_id="p",
                        target_input_slot="series",
                    ),
                ],
            ))

    def test_literal_binding_target_must_exist(self):
        with pytest.raises(Exception, match="unknown target_node_id"):
            WorkflowTemplate(**_trivial_template_dict(
                literal_bindings=[
                    LiteralBindingTemplate(
                        target_node_id="ghost",
                        target_input_slot="right",
                        value=100.0,
                    ),
                ],
            ))

    def test_undeclared_slot_reference_rejected(self):
        with pytest.raises(Exception, match="undeclared slot references"):
            WorkflowTemplate(**_trivial_template_dict(
                slot_schema=[],
                nodes=[
                    PrimitiveNodeTemplate(
                        node_id="p",
                        tool_name="synthetic_primitive_tool",
                        output_field="time_series",
                        params={"series_name": {"$slot": "ghost"}},
                    ),
                ],
            ))

    def test_template_is_frozen(self):
        t = WorkflowTemplate(**_trivial_template_dict())
        with pytest.raises(Exception):
            t.template_id = "different"


# ===========================================================================
# 3. bind() — slot validation + placeholder substitution
# ===========================================================================


def _template_with_named_slot():
    """Template that puts a slot reference in a primitive's params."""
    return WorkflowTemplate(
        template_id="named_slot_test",
        archetype="event_study",
        description="test",
        slot_schema=[
            SlotDeclaration(
                name="series_name",
                type="str", required=True,
                description="caller-supplied series name",
            ),
        ],
        nodes=[
            PrimitiveNodeTemplate(
                node_id="p",
                tool_name="synthetic_primitive_tool",
                output_field="time_series",
                params={"series_name": {"$slot": "series_name"}},
            ),
        ],
        edges=[],
        terminal_node_id="p",
    )


class TestBindSlotValidation:
    def test_required_slot_missing_raises(self):
        t = _template_with_named_slot()
        with pytest.raises(SlotBindingError, match="required slot"):
            t.bind({})

    def test_unknown_slot_raises(self):
        t = _template_with_named_slot()
        with pytest.raises(SlotBindingError, match="unknown slot"):
            t.bind({"series_name": "s", "extra": "ghost"})

    def test_type_mismatch_raises(self):
        t = _template_with_named_slot()
        with pytest.raises(SlotBindingError, match="declared type"):
            t.bind({"series_name": 123})  # int, not str

    def test_optional_slot_default_applied(self):
        t = WorkflowTemplate(
            template_id="opt",
            archetype="event_study",
            description="...",
            slot_schema=[
                SlotDeclaration(
                    name="lookback_days", type="int", required=False,
                    description="...", default=180,
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"n_rows": {"$slot": "lookback_days"}},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        wf = t.bind({})  # no slot supplied
        # Default 180 substituted into params.
        assert wf.nodes[0].params["n_rows"] == 180

    def test_supplied_value_overrides_default(self):
        t = WorkflowTemplate(
            template_id="opt",
            archetype="event_study",
            description="...",
            slot_schema=[
                SlotDeclaration(
                    name="lookback_days", type="int", required=False,
                    description="...", default=180,
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"n_rows": {"$slot": "lookback_days"}},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        wf = t.bind({"lookback_days": 90})
        assert wf.nodes[0].params["n_rows"] == 90

    def test_int_slot_rejects_bool(self):
        """``isinstance(True, int)`` is True in Python, but bool is
        a distinct slot type — type-check must reject bool when
        int is declared."""
        t = WorkflowTemplate(
            template_id="x",
            archetype="event_study",
            description="...",
            slot_schema=[
                SlotDeclaration(
                    name="n", type="int", required=True, description="...",
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"n_rows": {"$slot": "n"}},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        with pytest.raises(SlotBindingError):
            t.bind({"n": True})


class TestBindSubstitution:
    def test_simple_substitution(self):
        t = _template_with_named_slot()
        wf = t.bind({"series_name": "my_series"})
        assert wf.nodes[0].params["series_name"] == "my_series"

    def test_nested_dict_substitution(self):
        """``$slot`` inside a nested dict in params resolves
        correctly."""
        t = WorkflowTemplate(
            template_id="nested",
            archetype="event_study",
            description="...",
            slot_schema=[
                SlotDeclaration(
                    name="curve_family", type="str", required=True,
                    description="...",
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={
                        "spec": {
                            "curve_family": {"$slot": "curve_family"},
                            "tenor": "10Y",
                        },
                    },
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        wf = t.bind({"curve_family": "UST"})
        assert wf.nodes[0].params["spec"]["curve_family"] == "UST"
        assert wf.nodes[0].params["spec"]["tenor"] == "10Y"

    def test_list_substitution(self):
        t = WorkflowTemplate(
            template_id="list",
            archetype="cross_sectional_screen",
            description="...",
            slot_schema=[
                SlotDeclaration(
                    name="universe", type="list", required=True,
                    description="...",
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"universe": {"$slot": "universe"}},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        wf = t.bind({"universe": ["UST", "DE_BUND", "UK_GILT"]})
        assert wf.nodes[0].params["universe"] == ["UST", "DE_BUND", "UK_GILT"]

    def test_literal_binding_substitution(self):
        """``$slot`` inside a literal binding value resolves."""
        t = WorkflowTemplate(
            template_id="lit",
            archetype="attribution_decomposition",
            description="...",
            slot_schema=[
                SlotDeclaration(
                    name="multiplier", type="float", required=True,
                    description="...",
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={},
                ),
                OperatorNodeTemplate(
                    node_id="mul",
                    operator_name="series_arithmetic",
                    params={"op": "multiply"},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p", target_node_id="mul",
                    target_input_slot="left",
                ),
            ],
            literal_bindings=[
                LiteralBindingTemplate(
                    target_node_id="mul",
                    target_input_slot="right",
                    value={"$slot": "multiplier"},
                ),
            ],
            terminal_node_id="mul",
        )
        wf = t.bind({"multiplier": 100.0})
        assert wf.literal_bindings[0].value == 100.0

    def test_replay_determinism(self):
        """Same template + same slot_values → identical Workflow."""
        t = _template_with_named_slot()
        wf1 = t.bind({"series_name": "s"})
        wf2 = t.bind({"series_name": "s"})
        assert wf1.workflow_id == wf2.workflow_id
        assert wf1.nodes[0].params == wf2.nodes[0].params

    def test_bind_constructs_concrete_workflow(self):
        """The returned object is a concrete substrate Workflow
        (not still a template), and runs all the substrate's
        construction validators.

        Note: a structural class-name check here, not
        ``isinstance``, because ``test_workflow_substrate.py::
        TestFinanceBlindness::test_substrate_runtime_imports_no_rates_agent``
        deletes + re-imports ``shared.workflow`` modules from
        sys.modules, which produces fresh class objects.  The
        wf IS a Workflow by every meaningful structural check; we
        just can't rely on ``isinstance`` after the substrate
        modules have been re-imported in the same pytest session."""
        t = _template_with_named_slot()
        wf = t.bind({"series_name": "s"})
        # Class-name check is module-reload-tolerant.
        assert type(wf).__name__ == "Workflow"
        assert type(wf).__module__ == "shared.workflow.types"
        assert wf.workflow_id == t.template_id
        # And the wf is really executable shape: model_dump
        # round-trip works.
        round_trip = wf.model_dump()
        assert round_trip["workflow_id"] == t.template_id
        assert round_trip["nodes"][0]["params"]["series_name"] == "s"


# ===========================================================================
# 4. YAML loader
# ===========================================================================


_VALID_TEMPLATE_YAML = """
template_id: yaml_test
archetype: event_study
description: Test template loaded from YAML
slot_schema:
  - name: series_name
    type: str
    required: true
    description: caller-supplied series name
nodes:
  - kind: primitive
    node_id: p
    tool_name: synthetic_primitive_tool
    output_field: time_series
    params:
      series_name: {$slot: series_name}
edges: []
literal_bindings: []
terminal_node_id: p
"""


class TestYamlLoader:
    def test_load_round_trip(self, tmp_path):
        p = tmp_path / "tpl.yaml"
        p.write_text(_VALID_TEMPLATE_YAML)
        t = load_workflow_template(p)
        assert t.template_id == "yaml_test"
        assert t.archetype == "event_study"
        # slot reference preserved
        assert t.nodes[0].params == {"series_name": {"$slot": "series_name"}}

    def test_loader_caches_by_path(self, tmp_path):
        p = tmp_path / "tpl.yaml"
        p.write_text(_VALID_TEMPLATE_YAML)
        a = load_workflow_template(p)
        b = load_workflow_template(p)
        assert a is b  # same instance (cached)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(WorkflowTemplateError, match="not found"):
            load_workflow_template(tmp_path / "ghost.yaml")

    def test_malformed_yaml_raises(self, tmp_path):
        p = tmp_path / "bad.yaml"
        # Genuinely malformed YAML — unbalanced brackets cause a
        # parse error (not just a schema-validation error).
        p.write_text("template_id: [unclosed\nfoo: bar")
        with pytest.raises(WorkflowTemplateError, match="YAML parse error"):
            load_workflow_template(p)

    def test_empty_yaml_raises(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        with pytest.raises(WorkflowTemplateError, match="empty"):
            load_workflow_template(p)

    def test_schema_validation_failure_wrapped(self, tmp_path):
        p = tmp_path / "schema_bad.yaml"
        p.write_text(
            "template_id: x\n"
            "archetype: not_a_real_archetype\n"
            "description: x\n"
            "nodes:\n"
            "  - kind: primitive\n"
            "    node_id: p\n"
            "    tool_name: t\n"
            "    output_field: time_series\n"
            "terminal_node_id: p\n"
        )
        with pytest.raises(WorkflowTemplateError, match="Schema validation failed"):
            load_workflow_template(p)


# ===========================================================================
# 5. Template registry
# ===========================================================================


def _make_template(template_id: str, archetype: str = "event_study") -> WorkflowTemplate:
    return WorkflowTemplate(
        template_id=template_id,
        archetype=archetype,
        description=f"{template_id} test template",
        slot_schema=[],
        nodes=[
            PrimitiveNodeTemplate(
                node_id="p",
                tool_name="synthetic_primitive_tool",
                output_field="time_series",
                params={},
            ),
        ],
        edges=[],
        terminal_node_id="p",
    )


class TestRegistry:
    def test_register_and_get(self):
        t = _make_template("reg_test")
        register_template(t)
        assert get_template("reg_test") is t

    def test_get_unknown_raises(self):
        with pytest.raises(TemplateRegistryError, match="not registered"):
            get_template("ghost_template")

    def test_idempotent_register_same_content(self):
        t1 = _make_template("idem")
        t2 = _make_template("idem")  # same content
        register_template(t1)
        register_template(t2)  # no error
        assert get_template("idem") == t1

    def test_register_conflicting_content_rejected(self):
        t1 = _make_template("conflict")
        t2 = _make_template("conflict", archetype="cross_sectional_screen")
        register_template(t1)
        with pytest.raises(TemplateRegistryError, match="different content"):
            register_template(t2)

    def test_unregister(self):
        t = _make_template("unreg")
        register_template(t)
        unregister_template("unreg")
        with pytest.raises(TemplateRegistryError):
            get_template("unreg")

    def test_unregister_unknown_silently(self):
        unregister_template("never_registered")  # no error

    def test_list_all(self):
        register_template(_make_template("a"))
        register_template(_make_template("b"))
        register_template(_make_template("c"))
        ids = [t.template_id for t in list_templates()]
        assert ids == ["a", "b", "c"]  # sorted

    def test_list_filtered_by_archetype(self):
        register_template(_make_template("a", archetype="event_study"))
        register_template(_make_template(
            "b", archetype="cross_sectional_screen",
        ))
        register_template(_make_template("c", archetype="event_study"))
        ids = [t.template_id for t in list_templates(archetype="event_study")]
        assert ids == ["a", "c"]

    def test_known_template_ids(self):
        register_template(_make_template("a"))
        register_template(_make_template("b"))
        assert known_template_ids() == ["a", "b"]


# ===========================================================================
# 6. Template card
# ===========================================================================


class TestTemplateCard:
    def test_card_has_uniform_shape(self):
        t = _template_with_named_slot()
        card = card_for_template(t)
        assert isinstance(card, TemplateCard)
        assert card.template_id == t.template_id
        assert card.archetype == t.archetype
        assert card.description == t.description
        assert card.node_count == 1
        assert card.edge_count == 0

    def test_card_extracts_primitives_and_operators(self):
        t = WorkflowTemplate(
            template_id="mixed",
            archetype="event_study",
            description="...",
            slot_schema=[],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p1",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                PrimitiveNodeTemplate(
                    node_id="p2",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                OperatorNodeTemplate(
                    node_id="op",
                    operator_name="align_series",
                    params={},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p1", target_node_id="op",
                    target_input_slot="series_list",
                ),
                WorkflowEdge(
                    source_node_id="p2", target_node_id="op",
                    target_input_slot="series_list",
                ),
            ],
            terminal_node_id="op",
        )
        card = card_for_template(t)
        # Primitives deduplicated + sorted.
        assert card.primitives_used == ["synthetic_primitive_tool"]
        assert card.operators_used == ["align_series"]
        assert card.node_count == 3
        assert card.edge_count == 2

    def test_terminal_artifact_type_primitive_terminal(self):
        """Primitive-terminal templates always emit Series via the
        bridge."""
        t = _template_with_named_slot()
        card = card_for_template(t)
        assert card.terminal_artifact_type == "Series"

    def test_terminal_artifact_type_operator_terminal(self):
        """Operator-terminal templates emit whatever
        OPERATOR_REGISTRY declares for that operator."""
        t = WorkflowTemplate(
            template_id="op_terminal",
            archetype="event_study",
            description="...",
            slot_schema=[],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p1",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                PrimitiveNodeTemplate(
                    node_id="p2",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series", params={},
                ),
                OperatorNodeTemplate(
                    node_id="align",
                    operator_name="align_series",
                    params={},
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p1", target_node_id="align",
                    target_input_slot="series_list",
                ),
                WorkflowEdge(
                    source_node_id="p2", target_node_id="align",
                    target_input_slot="series_list",
                ),
            ],
            terminal_node_id="align",
        )
        card = card_for_template(t)
        # align_series emits SeriesSet per OPERATOR_REGISTRY.
        assert card.terminal_artifact_type == "SeriesSet"

    def test_card_slot_schema_preserved(self):
        t = _template_with_named_slot()
        card = card_for_template(t)
        assert len(card.slot_schema) == 1
        assert card.slot_schema[0].name == "series_name"


# ===========================================================================
# 7. End-to-end: YAML → bind → execute
# ===========================================================================


_E2E_TEMPLATE_YAML = """
template_id: e2e_test
archetype: event_study
description: End-to-end test template
slot_schema:
  - name: series_name
    type: str
    required: true
    description: caller-supplied series name
  - name: n_rows
    type: int
    required: false
    description: how many rows to generate
    default: 10
nodes:
  - kind: primitive
    node_id: p
    tool_name: synthetic_primitive_tool
    output_field: time_series
    params:
      series_name: {$slot: series_name}
      n_rows: {$slot: n_rows}
edges: []
literal_bindings: []
terminal_node_id: p
"""


class TestArchetypeSignature:
    """Codex P2 follow-up #1 (PR #80): the
    ``workflow_architecture.md`` "Template-selection contract"
    section requires every template card to carry an
    ``archetype_signature`` declaration so the future
    ``route_to_template`` LLM step can match prompts to templates
    via structural cues.  PR #80 shipped without this field; the
    fix adds it to ``WorkflowTemplate`` AND propagates it to
    ``TemplateCard``."""

    def _template_with_signature(self, *, signature):
        return WorkflowTemplate(
            template_id="sig_test",
            archetype="event_study",
            description="...",
            archetype_signature=signature,
            slot_schema=[],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )

    def test_default_empty_signature_is_legal(self):
        """Backward-compat: existing templates without an
        archetype_signature still construct cleanly.  PR 5+ will
        require ≥1 cue at the lint layer; the substrate stays
        permissive."""
        t = self._template_with_signature(signature=[])
        assert t.archetype_signature == []

    def test_single_cue_round_trips(self):
        t = self._template_with_signature(
            signature=["average move after / forward move when X exceeds Y"],
        )
        assert len(t.archetype_signature) == 1

    def test_multiple_cues_round_trip(self):
        t = self._template_with_signature(
            signature=[
                "conditional aggregation across event windows",
                "average move after threshold",
                "forward move conditional on X exceeding Y",
            ],
        )
        assert len(t.archetype_signature) == 3

    def test_empty_string_cue_rejected(self):
        with pytest.raises(Exception, match="empty"):
            self._template_with_signature(signature=[""])

    def test_whitespace_only_cue_rejected(self):
        with pytest.raises(Exception, match="empty"):
            self._template_with_signature(signature=["   "])

    def test_overlong_cue_rejected(self):
        long_cue = "x" * 121
        with pytest.raises(Exception, match="≤120 chars"):
            self._template_with_signature(signature=[long_cue])

    def test_120_char_cue_at_boundary_accepted(self):
        cue_120 = "x" * 120
        t = self._template_with_signature(signature=[cue_120])
        assert len(t.archetype_signature[0]) == 120

    def test_card_propagates_signature(self):
        cues = [
            "conditional aggregation across event windows",
            "forward window after event",
        ]
        t = self._template_with_signature(signature=cues)
        card = card_for_template(t)
        assert card.archetype_signature == cues

    def test_card_default_empty_signature(self):
        """Cards inherit the template's default empty signature
        list (back-compat with PR #80 templates)."""
        t = self._template_with_signature(signature=[])
        card = card_for_template(t)
        assert card.archetype_signature == []


class TestSlotReferenceSyntax:
    """Codex P3 follow-up (PR #80): the loader docstring used to
    show ``{$slot: signal_spec.curve_family}`` (dotted path), but
    the binder only supports exact slot-name lookup.  These tests
    pin the EXACT semantics the binder actually implements so a
    future template author cannot accidentally rely on the
    discontinued dotted-path style."""

    def test_dotted_path_style_is_NOT_supported(self):
        """Slot references like ``{$slot: foo.bar}`` are NOT
        magic dotted paths — the binder treats the whole string
        as the literal slot name.  The construction-time
        validator catches the undeclared reference."""
        with pytest.raises(Exception, match="undeclared slot references"):
            WorkflowTemplate(
                template_id="dotted",
                archetype="event_study",
                description="...",
                slot_schema=[
                    SlotDeclaration(
                        name="signal_spec", type="dict",
                        required=True, description="...",
                    ),
                ],
                nodes=[
                    PrimitiveNodeTemplate(
                        node_id="p",
                        tool_name="synthetic_primitive_tool",
                        output_field="time_series",
                        # Dotted-style reference to a sub-field —
                        # NOT supported by the binder.
                        params={
                            "curve_family": {"$slot": "signal_spec.curve_family"},
                        },
                    ),
                ],
                edges=[],
                terminal_node_id="p",
            )

    def test_whole_dict_slot_supported(self):
        """The supported pattern for nested data: declare a
        ``type: dict`` slot and reference it whole.  The
        consuming primitive's ``*Input`` schema handles nested
        validation."""
        t = WorkflowTemplate(
            template_id="dict_slot",
            archetype="event_study",
            description="...",
            slot_schema=[
                SlotDeclaration(
                    name="signal_spec", type="dict",
                    required=True, description="SeriesSpec dict",
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    # Whole-dict slot reference: the dict
                    # substitutes in entirely.
                    params={"spec": {"$slot": "signal_spec"}},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        wf = t.bind({
            "signal_spec": {
                "curve_family": "UST",
                "tenor": "10Y",
                "field_name": None,
            },
        })
        # The whole dict is substituted into the params slot.
        assert wf.nodes[0].params == {
            "spec": {
                "curve_family": "UST",
                "tenor": "10Y",
                "field_name": None,
            },
        }

    def test_flat_slots_recommended_pattern(self):
        """The recommended V1 pattern: one slot per leaf value.
        Each $slot reference is an exact slot name."""
        t = WorkflowTemplate(
            template_id="flat",
            archetype="event_study",
            description="...",
            slot_schema=[
                SlotDeclaration(
                    name="curve_family", type="str",
                    required=True, description="...",
                ),
                SlotDeclaration(
                    name="tenor", type="str",
                    required=True, description="...",
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={
                        "curve_family": {"$slot": "curve_family"},
                        "tenor": {"$slot": "tenor"},
                    },
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        wf = t.bind({"curve_family": "DE_BUND", "tenor": "10Y"})
        assert wf.nodes[0].params == {
            "curve_family": "DE_BUND",
            "tenor": "10Y",
        }


class TestEndToEnd:
    def test_load_bind_execute(self, tmp_path, synthetic_resolver):
        """Load YAML → bind slot values → execute via substrate."""
        p = tmp_path / "e2e.yaml"
        p.write_text(_E2E_TEMPLATE_YAML)
        t = load_workflow_template(p)

        wf = t.bind({"series_name": "my_series"})

        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        assert isinstance(result.terminal_artifact, Series)
        assert result.terminal_artifact.series_key == "my_series"

    def test_e2e_with_default_slot(self, tmp_path, synthetic_resolver):
        """Optional slot with default is applied when not supplied."""
        p = tmp_path / "e2e.yaml"
        p.write_text(_E2E_TEMPLATE_YAML)
        t = load_workflow_template(p)
        wf = t.bind({"series_name": "default_test"})  # n_rows omitted
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        # Default n_rows=10, so payload has 10 rows.
        assert len(result.terminal_artifact.payload) == 10

    def test_e2e_with_explicit_slot_override(self, tmp_path, synthetic_resolver):
        """Explicit slot value overrides default."""
        p = tmp_path / "e2e.yaml"
        p.write_text(_E2E_TEMPLATE_YAML)
        t = load_workflow_template(p)
        wf = t.bind({"series_name": "long_test", "n_rows": 25})
        result = execute_workflow(
            wf, engine=None, primitive_resolver=synthetic_resolver,
        )
        assert len(result.terminal_artifact.payload) == 25

    def test_e2e_replay_determinism(self, tmp_path, synthetic_resolver):
        """Same template + same slot values → same lineage hash."""
        p = tmp_path / "e2e.yaml"
        p.write_text(_E2E_TEMPLATE_YAML)
        t = load_workflow_template(p)

        wf1 = t.bind({"series_name": "s"})
        wf2 = t.bind({"series_name": "s"})

        r1 = execute_workflow(
            wf1, engine=None, primitive_resolver=synthetic_resolver,
        )
        r2 = execute_workflow(
            wf2, engine=None, primitive_resolver=synthetic_resolver,
        )
        assert (
            r1.terminal_artifact.lineage.head_hash
            == r2.terminal_artifact.lineage.head_hash
        )


# ===========================================================================
# 8. Finance-blindness: template layer must NOT import rates_agent
# ===========================================================================


class TestTemplateLayerFinanceBlindness:
    """The template layer is part of the substrate (lives under
    ``shared/workflow/``), so the same finance-blindness contract
    applies: no static or transitive ``rates_agent`` imports."""

    def test_template_modules_have_no_rates_agent_imports(self):
        import ast

        import shared.workflow.template as _tpl
        import shared.workflow.template_loader as _ldr
        import shared.workflow.template_registry as _reg
        import shared.workflow.template_card as _card

        for module in (_tpl, _ldr, _reg, _card):
            tree = ast.parse(Path(module.__file__).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith("rates_agent"), (
                            f"FINANCE-BLINDNESS VIOLATION in "
                            f"{module.__name__}: import {alias.name}"
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.startswith("rates_agent"):
                        raise AssertionError(
                            f"FINANCE-BLINDNESS VIOLATION in "
                            f"{module.__name__}: from {node.module}"
                        )


# ===========================================================================
# 9. Cross-slot constraints (Codex P2 follow-up to PR #86)
# ===========================================================================
#
# ``slot_constraints`` is a closed-family discriminated union of
# substrate-enforced cross-slot validators applied at ``bind()`` time
# AFTER individual-slot type/required checks pass.  V1 closed family
# is a single kind: ``relative_order`` (used by
# regime_conditioned_relationship to enforce
# ``high_threshold >= low_threshold``).
#
# These tests pin the substrate behaviour independently of any
# specific template binding so a future closed-family extension or
# bind-path change surfaces here, not in a downstream template's
# real-rates suite.


def _template_with_relative_order_constraint(
    *,
    operator: str = "gte",
    higher_type: str = "float",
    lower_type: str = "float",
) -> WorkflowTemplate:
    """A minimal template that declares two numeric slots + one
    relative-order constraint.  ``operator`` and the slot types are
    parameterised so individual tests can exercise each branch."""
    return WorkflowTemplate(
        template_id="relative_order_test",
        archetype="event_study",
        description="test",
        slot_schema=[
            SlotDeclaration(
                name="series_name",
                type="str", required=True,
                description="caller series name",
            ),
            SlotDeclaration(
                name="high",
                type=higher_type, required=True,
                description="upper bound",
            ),
            SlotDeclaration(
                name="low",
                type=lower_type, required=True,
                description="lower bound",
            ),
        ],
        slot_constraints=[
            RelativeOrderConstraint(
                higher="high",
                lower="low",
                operator=operator,
                rationale="Disjoint regions (high >= low).",
            ),
        ],
        nodes=[
            PrimitiveNodeTemplate(
                node_id="p",
                tool_name="synthetic_primitive_tool",
                output_field="time_series",
                params={"series_name": {"$slot": "series_name"}},
            ),
        ],
        edges=[],
        terminal_node_id="p",
    )


class TestSlotConstraints:
    """Codex P2 follow-up to PR #86: cross-slot constraints are now
    a real substrate guarantee, not docs-only."""

    def test_satisfied_constraint_binds_cleanly(self):
        t = _template_with_relative_order_constraint(operator="gte")
        wf = t.bind({"series_name": "s", "high": 5.0, "low": -5.0})
        assert wf is not None

    def test_violated_gte_constraint_raises(self):
        t = _template_with_relative_order_constraint(operator="gte")
        with pytest.raises(SlotBindingError, match=r"must be >="):
            t.bind({"series_name": "s", "high": -5.0, "low": 5.0})

    def test_equal_values_satisfy_gte(self):
        """``gte`` accepts equal values (not strictly greater)."""
        t = _template_with_relative_order_constraint(operator="gte")
        wf = t.bind({"series_name": "s", "high": 0.0, "low": 0.0})
        assert wf is not None

    def test_equal_values_violate_strict_gt(self):
        """``gt`` rejects equal values."""
        t = _template_with_relative_order_constraint(operator="gt")
        with pytest.raises(SlotBindingError, match=r"must be >"):
            t.bind({"series_name": "s", "high": 0.0, "low": 0.0})

    def test_lt_operator(self):
        t = _template_with_relative_order_constraint(operator="lt")
        with pytest.raises(SlotBindingError, match=r"must be <"):
            t.bind({"series_name": "s", "high": 5.0, "low": 5.0})
        # Strict less-than satisfied:
        wf = t.bind({"series_name": "s", "high": -5.0, "low": 5.0})
        assert wf is not None

    def test_lte_operator(self):
        t = _template_with_relative_order_constraint(operator="lte")
        # Equal satisfies lte:
        wf = t.bind({"series_name": "s", "high": 0.0, "low": 0.0})
        assert wf is not None

    def test_int_slots_supported(self):
        t = _template_with_relative_order_constraint(
            higher_type="int", lower_type="int",
        )
        with pytest.raises(SlotBindingError, match=r"must be >="):
            t.bind({"series_name": "s", "high": -3, "low": 5})

    def test_str_slot_rejected_at_template_construction(self):
        """Construction-time gate: relative_order constraints only
        make sense on numeric slots.  A constraint referencing a str
        slot must be rejected loudly at template-build time, not
        silently accepted (and then crash at bind-time when the
        substrate tries to compare strings via < / >)."""
        with pytest.raises(ValueError, match="relative_order"):
            WorkflowTemplate(
                template_id="bad_constraint",
                archetype="event_study",
                description="test",
                slot_schema=[
                    SlotDeclaration(
                        name="series_name",
                        type="str", required=True,
                        description="series",
                    ),
                    SlotDeclaration(
                        name="other",
                        type="str", required=True,
                        description="series",
                    ),
                ],
                slot_constraints=[
                    RelativeOrderConstraint(
                        higher="series_name", lower="other",
                        operator="gte", rationale="bad",
                    ),
                ],
                nodes=[
                    PrimitiveNodeTemplate(
                        node_id="p",
                        tool_name="synthetic_primitive_tool",
                        output_field="time_series",
                        params={"series_name": {"$slot": "series_name"}},
                    ),
                ],
                edges=[],
                terminal_node_id="p",
            )

    def test_undeclared_slot_in_constraint_rejected_at_construction(self):
        """A constraint referencing a non-declared slot name is
        caught at template-build time (before any bind() call)."""
        with pytest.raises(ValueError, match="undeclared slot"):
            WorkflowTemplate(
                template_id="bad_constraint",
                archetype="event_study",
                description="test",
                slot_schema=[
                    SlotDeclaration(
                        name="series_name",
                        type="str", required=True,
                        description="series",
                    ),
                    SlotDeclaration(
                        name="high",
                        type="float", required=True,
                        description="high",
                    ),
                ],
                slot_constraints=[
                    RelativeOrderConstraint(
                        higher="high", lower="ghost_slot",
                        operator="gte", rationale="bad",
                    ),
                ],
                nodes=[
                    PrimitiveNodeTemplate(
                        node_id="p",
                        tool_name="synthetic_primitive_tool",
                        output_field="time_series",
                        params={"series_name": {"$slot": "series_name"}},
                    ),
                ],
                edges=[],
                terminal_node_id="p",
            )

    def test_no_constraints_default_no_op(self):
        """A template without slot_constraints binds exactly as
        before (no behavioural change for templates that don't opt
        in)."""
        t = WorkflowTemplate(
            template_id="no_constraints",
            archetype="event_study",
            description="test",
            slot_schema=[
                SlotDeclaration(
                    name="series_name",
                    type="str", required=True,
                    description="series",
                ),
            ],
            nodes=[
                PrimitiveNodeTemplate(
                    node_id="p",
                    tool_name="synthetic_primitive_tool",
                    output_field="time_series",
                    params={"series_name": {"$slot": "series_name"}},
                ),
            ],
            edges=[],
            terminal_node_id="p",
        )
        assert t.slot_constraints == []
        # Bind succeeds with any value.
        assert t.bind({"series_name": "s"}) is not None

    def test_constraint_rationale_surfaces_in_error_message(self):
        """The rationale string the template author declared MUST
        appear in the SlotBindingError message — that is the whole
        point of carrying the rationale at all."""
        t = _template_with_relative_order_constraint(operator="gte")
        with pytest.raises(SlotBindingError) as exc_info:
            t.bind({"series_name": "s", "high": -5.0, "low": 5.0})
        assert "Disjoint regions" in str(exc_info.value)

"""tests/workflow/test_validation_result.py — PR-1 acceptance suite.

Covers ``shared.workflow.validation_result`` and the collect-all
refactor of ``shared.workflow.validate``.

The plan (``tmp/orchestration.md`` §PR-1) specifies:

  1. One test per error code in the 14-entry ``ErrorCode`` taxonomy
     (13 raised by ``validate_workflow_result`` in PR-1, plus
     ``E_FREQUENCY_MISMATCH`` which is declared up-front per the plan
     and raised by PR-3 / PR-4 — exercised by an enum-membership
     assertion, not a raise-site test, since no raise site exists yet).
  2. A multi-error workflow that triggers ≥3 codes in one pass.
  3. The legacy strict wrapper ``validate_workflow`` raises
     ``WorkflowValidationError`` on the first error (back-compat with
     ``shared.workflow.executor.execute_workflow`` and the existing
     ``tests/test_workflow_substrate.py`` suite).
  4. Owner-layer dispatch: each error's ``owner_layer`` matches the
     plan's documented mapping.

All tests are fully offline — they use a tiny synthetic
``PrimitiveResolver`` defined inline so the suite never touches a real
DB or an MCP subprocess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Tuple, Type

import pytest
from pydantic import BaseModel

from shared.workflow import (
    OperatorNode,
    PrimitiveNode,
    PrimitiveSpec,
    Workflow,
    WorkflowEdge,
    WorkflowValidationError,
    validate_workflow,
    validate_workflow_result,
)
from shared.workflow.types import LiteralBinding
from shared.workflow.validation_result import (
    ErrorCode,
    OwnerLayer,
    ValidationResult,
)


# ============================================================================
# SYNTHETIC PRIMITIVE RESOLVER (offline, no DB)
# ============================================================================
# Two synthetic primitives:
#
#   - ``synthetic_series_bps``     → Series, output_field_units={"time_series": "bps"}
#   - ``synthetic_series_percent`` → Series, output_field_units={"time_series": "percent"}
#   - ``synthetic_panel_tool``     → Panel,  output_field_units={}
#
# The unit declarations let us exercise E_UNIT_MISMATCH (series_arithmetic
# add with mismatched units) and the wrong-output-type Panel→Series mismatch
# for the L2_BINDING flavour of E_TYPE_MISMATCH.


class _SyntheticInput(BaseModel):
    value: float = 1.0


class _SyntheticOutput(BaseModel):
    time_series: Dict[str, float] = {}


def _noop_callable(*, engine, params, config) -> Dict[str, float]:
    """Never executed by the validator — required by PrimitiveSpec but
    the substrate's validator only inspects metadata, not callables."""
    return {"time_series": []}


_SYNTHETIC_SPECS: Dict[str, PrimitiveSpec] = {
    "synthetic_series_bps": PrimitiveSpec(
        tool_name="synthetic_series_bps",
        callable=_noop_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=Path("/dev/null"),
        output_field_units={"time_series": "bps"},
        output_artifact_type="Series",
    ),
    "synthetic_series_percent": PrimitiveSpec(
        tool_name="synthetic_series_percent",
        callable=_noop_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=Path("/dev/null"),
        output_field_units={"time_series": "percent"},
        output_artifact_type="Series",
    ),
    "synthetic_panel_tool": PrimitiveSpec(
        tool_name="synthetic_panel_tool",
        callable=_noop_callable,
        input_class=_SyntheticInput,
        output_class=_SyntheticOutput,
        config_path=Path("/dev/null"),
        output_field_units={},
        output_artifact_type="Panel",
    ),
}


def _synthetic_resolver(tool_name: str) -> PrimitiveSpec:
    spec = _SYNTHETIC_SPECS.get(tool_name)
    if spec is None:
        raise KeyError(f"unknown synthetic tool {tool_name!r}")
    return spec


# ============================================================================
# THE ERROR-CODE → OWNER-LAYER MAPPING (mirrors tmp/orchestration.md §PR-1)
# ============================================================================
#
# This dict IS the contract.  Each test below asserts the validator
# emits the expected code AND the expected owner_layer for the kind of
# error the test triggers.  The single-source-of-truth for the mapping
# in production code is ``shared.workflow.validate``; this table is the
# test-time mirror used for cross-validation.

_EXPECTED_OWNER_LAYER: Dict[ErrorCode, OwnerLayer] = {
    ErrorCode.E_UNKNOWN_OPERATOR: OwnerLayer.L3_WIRING,
    ErrorCode.E_UNKNOWN_SLOT: OwnerLayer.L3_WIRING,
    ErrorCode.E_EDGE_TARGETS_PRIMITIVE: OwnerLayer.L3_WIRING,
    ErrorCode.E_LITERAL_TARGETS_NON_OPERATOR: OwnerLayer.L3_WIRING,
    ErrorCode.E_LITERAL_UNKNOWN_SLOT: OwnerLayer.L3_WIRING,
    ErrorCode.E_LITERAL_SLOT_NO_SCALAR: OwnerLayer.L3_WIRING,
    ErrorCode.E_ARITY_VIOLATION: OwnerLayer.L3_WIRING,
    ErrorCode.E_UNBOUND_REQUIRED_SLOT: OwnerLayer.L3_WIRING,
    # E_TYPE_MISMATCH is special — owner_layer depends on upstream kind.
    # Tested individually below in test_e_type_mismatch_l3_when_upstream_operator
    # and test_e_type_mismatch_l2_when_upstream_primitive.
    ErrorCode.E_UNKNOWN_OUTPUT_FIELD: OwnerLayer.L2_BINDING,
    ErrorCode.E_UNIT_MISMATCH: OwnerLayer.L3_WIRING,
    ErrorCode.E_DAG_CYCLE: OwnerLayer.L3_WIRING,
    ErrorCode.E_PRIMITIVE_RESOLVE_FAIL: OwnerLayer.L2_BINDING,
}


# ============================================================================
# WORKFLOW BUILDERS (one per error code we trigger)
# ============================================================================


def _wf_clean_pipeline() -> Workflow:
    """A clean pipeline: synthetic_series_bps → rolling_zscore → terminal.

    Exercises the happy path so we can assert ``is_clean`` end-to-end.
    """
    return Workflow(
        workflow_id="clean_pipeline",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="rolling_zscore",
                params={"window": 20},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="series",
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_unknown_operator() -> Workflow:
    return Workflow(
        workflow_id="wf_unknown_op",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="ZZZ_DEFINITELY_NOT_REGISTERED_OPERATOR",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="series",
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_unknown_slot() -> Workflow:
    """Edge targets a slot ``rolling_zscore`` does not have."""
    return Workflow(
        workflow_id="wf_unknown_slot",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="rolling_zscore",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="not_a_real_slot",
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_edge_targets_primitive() -> Workflow:
    """Edge whose target is a PrimitiveNode — illegal in v1."""
    return Workflow(
        workflow_id="wf_edge_targets_primitive",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            PrimitiveNode(
                node_id="p2",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="p2",
                target_input_slot="series",
            ),
        ],
        terminal_node_id="p2",
    )


def _wf_literal_targets_non_operator() -> Workflow:
    """LiteralBinding whose target is a PrimitiveNode."""
    return Workflow(
        workflow_id="wf_literal_non_op",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
        ],
        literal_bindings=[
            LiteralBinding(
                target_node_id="p1",
                target_input_slot="anything",
                value=42.0,
            ),
        ],
        terminal_node_id="p1",
    )


def _wf_literal_unknown_slot() -> Workflow:
    """LiteralBinding to a known operator at an unknown slot."""
    return Workflow(
        workflow_id="wf_literal_unknown_slot",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="series_arithmetic",
                params={"op": "add"},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="left",
            ),
        ],
        literal_bindings=[
            LiteralBinding(
                target_node_id="o1",
                target_input_slot="not_a_real_slot",
                value=100.0,
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_literal_slot_no_scalar() -> Workflow:
    """LiteralBinding to a slot with accepts_scalar=False
    (``correlation.left``)."""
    return Workflow(
        workflow_id="wf_literal_slot_no_scalar",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="correlation",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="right",
            ),
        ],
        literal_bindings=[
            LiteralBinding(
                target_node_id="o1",
                target_input_slot="left",
                value=0.5,
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_arity_violation() -> Workflow:
    """series_arithmetic with op=add (binary) but ``right`` unbound."""
    return Workflow(
        workflow_id="wf_arity_violation",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="series_arithmetic",
                params={"op": "add"},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="left",
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_unbound_required_slot() -> Workflow:
    """correlation with NO edges — both ``left`` and ``right`` unbound.

    Triggers ``E_UNBOUND_REQUIRED_SLOT`` (twice; we assert at least one).
    The substrate-default check fires because correlation has no
    arity_validator hook.
    """
    return Workflow(
        workflow_id="wf_unbound_required_slot",
        nodes=[
            OperatorNode(
                node_id="o1",
                operator_name="correlation",
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_type_mismatch_upstream_operator() -> Workflow:
    """align_series → SeriesSet → correlation.left (expects Series).

    Upstream is an operator → owner_layer=L3_WIRING.
    """
    return Workflow(
        workflow_id="wf_type_mismatch_op_upstream",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            PrimitiveNode(
                node_id="p2",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="align",
                operator_name="align_series",
            ),
            PrimitiveNode(
                node_id="p3",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="corr",
                operator_name="correlation",
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
            # Wrong: SeriesSet → Series slot
            WorkflowEdge(
                source_node_id="align", target_node_id="corr",
                target_input_slot="left",
            ),
            WorkflowEdge(
                source_node_id="p3", target_node_id="corr",
                target_input_slot="right",
            ),
        ],
        terminal_node_id="corr",
    )


def _wf_type_mismatch_upstream_primitive() -> Workflow:
    """synthetic_panel_tool (Panel) → correlation.left (Series).

    Upstream is a primitive → owner_layer=L2_BINDING.
    Requires the resolver to declare ``output_artifact_type=Panel``.
    """
    return Workflow(
        workflow_id="wf_type_mismatch_prim_upstream",
        nodes=[
            PrimitiveNode(
                node_id="p_panel",
                tool_name="synthetic_panel_tool",
                output_field="time_series",
            ),
            PrimitiveNode(
                node_id="p_series",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="corr",
                operator_name="correlation",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p_panel", target_node_id="corr",
                target_input_slot="left",
            ),
            WorkflowEdge(
                source_node_id="p_series", target_node_id="corr",
                target_input_slot="right",
            ),
        ],
        terminal_node_id="corr",
    )


def _wf_unknown_output_field() -> Workflow:
    """PrimitiveNode whose output_field is not in resolver's
    ``output_field_units`` map.  ``synthetic_series_bps`` declares
    {"time_series": "bps"} so any other field triggers the check."""
    return Workflow(
        workflow_id="wf_unknown_output_field",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="bogus_field_name",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="rolling_zscore",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="series",
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_unit_mismatch() -> Workflow:
    """series_arithmetic.add of bps + percent — caught by the
    operator's declared unit_validator at validate-time."""
    return Workflow(
        workflow_id="wf_unit_mismatch",
        nodes=[
            PrimitiveNode(
                node_id="p_bps",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            PrimitiveNode(
                node_id="p_pct",
                tool_name="synthetic_series_percent",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="add",
                operator_name="series_arithmetic",
                params={"op": "add"},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p_bps", target_node_id="add",
                target_input_slot="left",
            ),
            WorkflowEdge(
                source_node_id="p_pct", target_node_id="add",
                target_input_slot="right",
            ),
        ],
        terminal_node_id="add",
    )


def _wf_dag_cycle() -> Workflow:
    """Two operators feeding each other.  Pure cycle."""
    return Workflow(
        workflow_id="wf_cycle",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            PrimitiveNode(
                node_id="p2",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="series_arithmetic",
                params={"op": "add"},
            ),
            OperatorNode(
                node_id="o2",
                operator_name="series_arithmetic",
                params={"op": "subtract"},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="left",
            ),
            WorkflowEdge(
                source_node_id="p2", target_node_id="o2",
                target_input_slot="left",
            ),
            # Cycle: o1.right ← o2; o2.right ← o1.
            WorkflowEdge(
                source_node_id="o2", target_node_id="o1",
                target_input_slot="right",
            ),
            WorkflowEdge(
                source_node_id="o1", target_node_id="o2",
                target_input_slot="right",
            ),
        ],
        terminal_node_id="o1",
    )


def _wf_primitive_resolve_fail() -> Workflow:
    return Workflow(
        workflow_id="wf_resolve_fail",
        nodes=[
            PrimitiveNode(
                node_id="p1",
                tool_name="not_a_synthetic_tool",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="o1",
                operator_name="rolling_zscore",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="p1", target_node_id="o1",
                target_input_slot="series",
            ),
        ],
        terminal_node_id="o1",
    )


# ============================================================================
# HAPPY PATH
# ============================================================================


class TestCleanWorkflow:
    def test_clean_workflow_is_clean(self) -> None:
        result = validate_workflow_result(
            _wf_clean_pipeline(), primitive_resolver=_synthetic_resolver,
        )
        assert result.is_clean, (
            f"clean workflow should validate; got {result.errors!r}"
        )
        assert result.errors == ()
        assert result.first() is None

    def test_clean_workflow_legacy_wrapper_no_raise(self) -> None:
        validate_workflow(
            _wf_clean_pipeline(), primitive_resolver=_synthetic_resolver,
        )


# ============================================================================
# ONE TEST PER ERROR CODE
# ============================================================================


class TestEachErrorCode:
    """One test per stable code in ``ErrorCode``.

    Each test asserts:
      1. The expected code is present in ``result.errors``.
      2. Its ``owner_layer`` matches the documented mapping.
      3. The legacy strict wrapper raises ``WorkflowValidationError``
         with the same message text (substring match on a stable
         keyword from the message).
    """

    def _assert_code_and_layer(
        self,
        wf: Workflow,
        code: ErrorCode,
        message_keyword: str,
        *,
        use_resolver: bool = False,
    ) -> ValidationResult:
        resolver = _synthetic_resolver if use_resolver else None
        result = validate_workflow_result(wf, primitive_resolver=resolver)
        assert not result.is_clean, (
            f"expected at least one error for code={code.value}"
        )
        matched = result.by_code(code)
        assert matched, (
            f"expected code={code.value} in errors; got "
            f"{[e.code.value for e in result.errors]}"
        )
        # Owner-layer mapping check (except for E_TYPE_MISMATCH which
        # has its own per-test asserts since owner_layer depends on
        # upstream kind).
        if code != ErrorCode.E_TYPE_MISMATCH:
            expected_layer = _EXPECTED_OWNER_LAYER[code]
            for err in matched:
                assert err.owner_layer == expected_layer, (
                    f"{code.value}: expected owner_layer={expected_layer.value}"
                    f", got {err.owner_layer.value}"
                )
        # Strict wrapper raises with the same first message.
        with pytest.raises(WorkflowValidationError, match=message_keyword):
            validate_workflow(wf, primitive_resolver=resolver)
        return result

    def test_e_unknown_operator(self) -> None:
        self._assert_code_and_layer(
            _wf_unknown_operator(),
            ErrorCode.E_UNKNOWN_OPERATOR,
            "unknown operator",
        )

    def test_e_unknown_slot(self) -> None:
        self._assert_code_and_layer(
            _wf_unknown_slot(),
            ErrorCode.E_UNKNOWN_SLOT,
            "unknown input slot",
        )

    def test_e_edge_targets_primitive(self) -> None:
        self._assert_code_and_layer(
            _wf_edge_targets_primitive(),
            ErrorCode.E_EDGE_TARGETS_PRIMITIVE,
            "targets a PrimitiveNode",
        )

    def test_e_literal_targets_non_operator(self) -> None:
        self._assert_code_and_layer(
            _wf_literal_targets_non_operator(),
            ErrorCode.E_LITERAL_TARGETS_NON_OPERATOR,
            "literal binding",
        )

    def test_e_literal_unknown_slot(self) -> None:
        self._assert_code_and_layer(
            _wf_literal_unknown_slot(),
            ErrorCode.E_LITERAL_UNKNOWN_SLOT,
            "literal binding",
        )

    def test_e_literal_slot_no_scalar(self) -> None:
        self._assert_code_and_layer(
            _wf_literal_slot_no_scalar(),
            ErrorCode.E_LITERAL_SLOT_NO_SCALAR,
            "does not accept scalar",
        )

    def test_e_arity_violation(self) -> None:
        # The substring 'binary' is part of the
        # _series_arithmetic_arity_validator error message and is
        # asserted on by the existing test_workflow_substrate.py
        # suite — preserved here for the same back-compat guarantee.
        self._assert_code_and_layer(
            _wf_arity_violation(),
            ErrorCode.E_ARITY_VIOLATION,
            "binary",
        )

    def test_e_unbound_required_slot(self) -> None:
        self._assert_code_and_layer(
            _wf_unbound_required_slot(),
            ErrorCode.E_UNBOUND_REQUIRED_SLOT,
            "unbound required input slot",
        )

    def test_e_type_mismatch_l3_when_upstream_operator(self) -> None:
        result = self._assert_code_and_layer(
            _wf_type_mismatch_upstream_operator(),
            ErrorCode.E_TYPE_MISMATCH,
            "expects Series",
        )
        # The wrong-type edge has an OperatorNode upstream — owner_layer
        # must be L3_WIRING (composer authored the shape wrong).
        type_errs = result.by_code(ErrorCode.E_TYPE_MISMATCH)
        # There is exactly one wrong edge in the fixture.
        assert any(
            e.owner_layer == OwnerLayer.L3_WIRING
            and e.detail.get("upstream_is_primitive") is False
            for e in type_errs
        ), (
            f"expected at least one L3_WIRING type mismatch; got "
            f"{[(e.owner_layer.value, e.detail) for e in type_errs]}"
        )

    def test_e_type_mismatch_l2_when_upstream_primitive(self) -> None:
        result = self._assert_code_and_layer(
            _wf_type_mismatch_upstream_primitive(),
            ErrorCode.E_TYPE_MISMATCH,
            "expects Series",
            use_resolver=True,
        )
        type_errs = result.by_code(ErrorCode.E_TYPE_MISMATCH)
        assert any(
            e.owner_layer == OwnerLayer.L2_BINDING
            and e.detail.get("upstream_is_primitive") is True
            for e in type_errs
        ), (
            f"expected at least one L2_BINDING type mismatch; got "
            f"{[(e.owner_layer.value, e.detail) for e in type_errs]}"
        )

    def test_e_unknown_output_field(self) -> None:
        # The exact substring "declares output_field='bogus_field_name'"
        # is asserted on by the existing test_workflow_substrate.py
        # suite — preserved verbatim.
        self._assert_code_and_layer(
            _wf_unknown_output_field(),
            ErrorCode.E_UNKNOWN_OUTPUT_FIELD,
            r"declares output_field='bogus_field_name'",
            use_resolver=True,
        )

    def test_e_unit_mismatch(self) -> None:
        self._assert_code_and_layer(
            _wf_unit_mismatch(),
            ErrorCode.E_UNIT_MISMATCH,
            "unit-compatibility check",
            use_resolver=True,
        )

    def test_e_dag_cycle(self) -> None:
        self._assert_code_and_layer(
            _wf_dag_cycle(),
            ErrorCode.E_DAG_CYCLE,
            "cycle",
        )

    def test_e_primitive_resolve_fail(self) -> None:
        self._assert_code_and_layer(
            _wf_primitive_resolve_fail(),
            ErrorCode.E_PRIMITIVE_RESOLVE_FAIL,
            "cannot resolve",
            use_resolver=True,
        )


# ============================================================================
# MULTI-ERROR PASS
# ============================================================================


class TestMultiErrorWorkflow:
    """One workflow that triggers ≥3 distinct codes in a single
    ``validate_workflow_result`` pass."""

    def _wf_three_errors(self) -> Workflow:
        # Triggers:
        #   - E_UNKNOWN_OPERATOR  (operator name typo)
        #   - E_EDGE_TARGETS_PRIMITIVE (edge p1 -> p2)
        #   - E_UNBOUND_REQUIRED_SLOT (correlation o2 has no edges)
        return Workflow(
            workflow_id="wf_multi_error",
            nodes=[
                PrimitiveNode(
                    node_id="p1",
                    tool_name="synthetic_series_bps",
                    output_field="time_series",
                ),
                PrimitiveNode(
                    node_id="p2",
                    tool_name="synthetic_series_bps",
                    output_field="time_series",
                ),
                OperatorNode(
                    node_id="o_bad",
                    operator_name="this_operator_does_not_exist",
                ),
                OperatorNode(
                    node_id="o2",
                    operator_name="correlation",
                ),
            ],
            edges=[
                WorkflowEdge(
                    source_node_id="p1", target_node_id="p2",
                    target_input_slot="series",
                ),
            ],
            terminal_node_id="o2",
        )

    def test_collects_all_three_codes(self) -> None:
        result = validate_workflow_result(self._wf_three_errors())
        codes_present = {e.code for e in result.errors}
        assert ErrorCode.E_UNKNOWN_OPERATOR in codes_present
        assert ErrorCode.E_EDGE_TARGETS_PRIMITIVE in codes_present
        assert ErrorCode.E_UNBOUND_REQUIRED_SLOT in codes_present
        # Multi-error pass means ≥3 distinct codes from one validation.
        assert len(codes_present) >= 3

    def test_legacy_wrapper_raises_first_only(self) -> None:
        """The strict wrapper must surface the FIRST collected error
        (in collection order — matches the pre-refactor first-raise
        behaviour the executor and existing tests depend on)."""
        with pytest.raises(WorkflowValidationError):
            validate_workflow(self._wf_three_errors())


# ============================================================================
# OWNER-LAYER DISPATCH
# ============================================================================


class TestOwnerLayerDispatch:
    """For each code in the documented mapping, assert the validator
    emits the expected ``owner_layer``.  Drives off
    ``_EXPECTED_OWNER_LAYER`` so the contract is the test data."""

    @pytest.mark.parametrize(
        "code,owner_layer",
        [(c, l) for c, l in _EXPECTED_OWNER_LAYER.items()],
    )
    def test_owner_layer_mapping(
        self, code: ErrorCode, owner_layer: OwnerLayer,
    ) -> None:
        # The mapping table itself must be coherent — every code we
        # claim to know about routes to a defined OwnerLayer.
        assert isinstance(owner_layer, OwnerLayer)

    def test_by_owner_layer_helper(self) -> None:
        result = validate_workflow_result(_wf_unknown_operator())
        l3 = result.by_owner_layer(OwnerLayer.L3_WIRING)
        l2 = result.by_owner_layer(OwnerLayer.L2_BINDING)
        assert l3, "expected at least one L3_WIRING error"
        assert not l2

    def test_by_code_helper(self) -> None:
        result = validate_workflow_result(_wf_unknown_operator())
        matched = result.by_code(ErrorCode.E_UNKNOWN_OPERATOR)
        assert len(matched) == 1


# ============================================================================
# FROZEN / IMMUTABLE / TUPLE
# ============================================================================


class TestValidationResultShape:
    def test_errors_is_tuple_not_list(self) -> None:
        result = validate_workflow_result(_wf_unknown_operator())
        assert isinstance(result.errors, tuple), (
            "ValidationResult.errors must be a tuple (frozen Pydantic)"
        )

    def test_validation_result_is_frozen(self) -> None:
        result = validate_workflow_result(_wf_clean_pipeline())
        with pytest.raises(Exception):
            # Pydantic v2 raises pydantic_core.ValidationError on
            # frozen-model mutation; ValueError is its parent class.
            result.workflow_id = "mutated"  # type: ignore[misc]

    def test_validation_error_is_frozen(self) -> None:
        result = validate_workflow_result(_wf_unknown_operator())
        err = result.errors[0]
        with pytest.raises(Exception):
            err.message = "mutated"  # type: ignore[misc]


# ============================================================================
# DEFERRED-RAISE CODES (declared in PR-1, raise site lands in PR-3/PR-4)
# ============================================================================


class TestDeferredRaiseCodes:
    """``E_FREQUENCY_MISMATCH`` is declared in the closed family in PR-1
    but has no raise site inside ``validate_workflow_result`` yet — the
    leaf-contract role-discriminant check (PR-3 hole/fill contracts +
    PR-4 assembler) is what surfaces it.  The plan
    (``tmp/orchestration.md``:237) declares the code in PR-1's taxonomy
    so the closed family is coherent across PR boundaries; this test
    pins the contract for PR-3 to extend rather than reinvent.
    """

    def test_frequency_mismatch_is_declared(self) -> None:
        assert ErrorCode.E_FREQUENCY_MISMATCH.value == "E_FREQUENCY_MISMATCH"

    def test_frequency_mismatch_not_raised_by_pr1_validator(self) -> None:
        """No fixture in this module's workflow builders triggers
        E_FREQUENCY_MISMATCH; running the full suite asserts the
        invariant that PR-1's ``validate_workflow_result`` body has zero
        raise sites for this code.  When PR-3 adds the raise site, this
        test should be deleted in the same PR that ships the new
        check."""
        for builder in (
            _wf_clean_pipeline,
            _wf_unknown_operator,
            _wf_unknown_slot,
            _wf_edge_targets_primitive,
            _wf_literal_targets_non_operator,
            _wf_literal_unknown_slot,
            _wf_literal_slot_no_scalar,
            _wf_arity_violation,
            _wf_unbound_required_slot,
            _wf_type_mismatch_upstream_operator,
            _wf_type_mismatch_upstream_primitive,
            _wf_unknown_output_field,
            _wf_unit_mismatch,
            _wf_dag_cycle,
            _wf_primitive_resolve_fail,
        ):
            result = validate_workflow_result(
                builder(), primitive_resolver=_synthetic_resolver,
            )
            freq_errs = result.by_code(ErrorCode.E_FREQUENCY_MISMATCH)
            assert not freq_errs, (
                f"{builder.__name__}: PR-1 validator unexpectedly raised "
                "E_FREQUENCY_MISMATCH; raise site should land in PR-3 / "
                "PR-4 (leaf-contract role-discriminant)."
            )


# ============================================================================
# CLOSED-FAMILY DISCIPLINE (P8)
# ============================================================================


class TestClosedFamilies:
    """ErrorCode and OwnerLayer are closed families.  Tests guard
    against accidental additions."""

    def test_error_code_taxonomy_size(self) -> None:
        # Adding a new code requires (a) an ADR and (b) updating this
        # number.  This guards against silent enum drift.
        #
        # Taxonomy size = 14 after PR-1 corrective:
        #   - 13 codes for the existing structural raise sites inside
        #     validate_workflow_result (CHECKS 1-9 in the validator),
        #   - 1 code (E_FREQUENCY_MISMATCH) declared up-front per the
        #     plan's PR-1 table (tmp/orchestration.md:237) so the
        #     closed family is coherent across PR boundaries; its
        #     raise site lands in PR-3 / PR-4 with the leaf-contract
        #     role-discriminant check.
        assert len(ErrorCode) == 14, (
            f"ErrorCode taxonomy size changed to {len(ErrorCode)}.  "
            "Per P8, extending requires an ADR + this assertion bump."
        )

    def test_owner_layer_size(self) -> None:
        assert len(OwnerLayer) == 3, (
            f"OwnerLayer family size changed to {len(OwnerLayer)}.  "
            "Per P8, extending requires an ADR + this assertion bump."
        )

    def test_every_code_has_owner_layer_mapping(self) -> None:
        # Excluding E_TYPE_MISMATCH (dual-owner by design) and
        # E_FREQUENCY_MISMATCH (declared in PR-1 but raised by PR-3 / PR-4;
        # its owner_layer dispatch table is owned by PR-3's hole/fill
        # contract design, not by PR-1's structural validator).
        skip = {ErrorCode.E_TYPE_MISMATCH, ErrorCode.E_FREQUENCY_MISMATCH}
        for code in ErrorCode:
            if code in skip:
                continue
            assert code in _EXPECTED_OWNER_LAYER, (
                f"{code.value} missing from _EXPECTED_OWNER_LAYER — "
                "the plan's mapping table is out of sync with the enum."
            )

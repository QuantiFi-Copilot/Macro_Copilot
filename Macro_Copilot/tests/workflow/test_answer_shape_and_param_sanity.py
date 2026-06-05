"""tests/workflow/test_answer_shape_and_param_sanity.py

Phase A of the orchestration-upgrade plan (the DETERMINISTIC SPINE):
  - D2: the SOFT output-shape contract (``shared.workflow.answer_shape``)
        + the terminal-shape check (CHECK 10, E_TERMINAL_SHAPE_MISMATCH).
  - D4: per-operator STATIC param-sanity (CHECK 11, E_PARAM_SANITY).
  - A5: the "never ship wrong" invariant — a shape-mismatched DAG is NOT
        is_clean, so the deterministic gate blocks it before execution.

All pure code — no LLM, no API, no credits.  These checks give the
robustness guarantee independent of any model (plan P1).
"""

from __future__ import annotations

import pytest

from shared.artifacts.registry import ArtifactTypeName
from shared.workflow.answer_shape import (
    AnswerShape,
    acceptable_artifact_types,
    is_unconstrained,
    parse_answer_shapes,
    shape_contract_satisfied,
)
from shared.workflow.types import (
    OperatorNode,
    PrimitiveNode,
    Workflow,
    WorkflowEdge,
)
from shared.workflow.validate import validate_workflow_result
from shared.workflow.validation_result import (
    ErrorCode,
    OwnerLayer,
    Severity,
)


# ============================================================================
# Workflow builders — a leaf → one operator → terminal.  No resolver needed:
# primitive output type defaults to "Series"; operator output type comes from
# the registry's OutputDescriptor.
# ============================================================================


def _wf_terminal(operator_name: str, params: dict | None = None) -> Workflow:
    return Workflow(
        workflow_id=f"wf_{operator_name}",
        nodes=[
            PrimitiveNode(
                node_id="leaf",
                tool_name="synthetic_series_bps",
                output_field="time_series",
            ),
            OperatorNode(
                node_id="op",
                operator_name=operator_name,
                params=params or {},
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="leaf",
                target_node_id="op",
                target_input_slot="series",
            ),
        ],
        terminal_node_id="op",
    )


# rolling_zscore terminates in a Series; summarize_series in a ScalarMetric.
def _wf_series_terminal() -> Workflow:
    return _wf_terminal("rolling_zscore", {"window": 20})


def _wf_scalar_terminal() -> Workflow:
    return _wf_terminal("summarize_series", {"statistic": "mean"})


# ============================================================================
# 1. AnswerShape vocab (D2)
# ============================================================================


class TestAnswerShapeVocab:
    def test_scalar_maps_to_scalar_metric(self):
        assert acceptable_artifact_types({AnswerShape.SCALAR}) == frozenset(
            {ArtifactTypeName.SCALAR_METRIC}
        )

    def test_panel_accepts_both_panel_kinds(self):
        assert acceptable_artifact_types({AnswerShape.PANEL}) == frozenset(
            {ArtifactTypeName.PANEL, ArtifactTypeName.WINDOWED_PANEL}
        )

    def test_any_is_whole_family(self):
        assert acceptable_artifact_types({AnswerShape.ANY}) == frozenset(
            ArtifactTypeName
        )

    def test_multi_shape_union(self):
        got = acceptable_artifact_types({AnswerShape.SCALAR, AnswerShape.SERIES})
        assert got == frozenset(
            {ArtifactTypeName.SCALAR_METRIC, ArtifactTypeName.SERIES}
        )

    def test_unconstrained_predicate(self):
        assert is_unconstrained(set())
        assert is_unconstrained({AnswerShape.ANY})
        assert is_unconstrained({AnswerShape.SCALAR, AnswerShape.ANY})
        assert not is_unconstrained({AnswerShape.SCALAR})

    def test_satisfied(self):
        assert shape_contract_satisfied({AnswerShape.SCALAR}, "ScalarMetric")
        assert not shape_contract_satisfied({AnswerShape.SCALAR}, "Series")
        assert shape_contract_satisfied({AnswerShape.ANY}, "Series")  # unconstrained
        assert shape_contract_satisfied(set(), "Panel")  # empty == unconstrained
        assert shape_contract_satisfied(
            {AnswerShape.SCALAR, AnswerShape.SERIES}, "Series"
        )

    def test_parse_drops_unknown_and_defaults_to_any(self):
        assert parse_answer_shapes(["scalar", "blah"]) == frozenset(
            {AnswerShape.SCALAR}
        )
        assert parse_answer_shapes([]) == frozenset({AnswerShape.ANY})
        assert parse_answer_shapes(["totally_unknown"]) == frozenset(
            {AnswerShape.ANY}
        )
        # case/space-insensitive
        assert parse_answer_shapes([" Scalar "]) == frozenset({AnswerShape.SCALAR})

    def test_every_non_any_shape_maps_to_nonempty_subset(self):
        # Lockstep: each shape (except ANY) maps to >=1 artifact type, all
        # of which are real closed-family members.
        family = frozenset(ArtifactTypeName)
        for shape in AnswerShape:
            acc = acceptable_artifact_types({shape})
            assert acc, f"{shape} maps to no artifact types"
            assert acc <= family


# ============================================================================
# 2. Terminal-shape check — CHECK 10 / E_TERMINAL_SHAPE_MISMATCH (D2)
# ============================================================================


class TestTerminalShapeCheck:
    def test_series_terminal_violates_scalar_contract(self):
        res = validate_workflow_result(
            _wf_series_terminal(),
            expected_answer_shapes=frozenset({AnswerShape.SCALAR}),
        )
        assert not res.is_clean
        errs = res.by_code(ErrorCode.E_TERMINAL_SHAPE_MISMATCH)
        assert len(errs) == 1
        e = errs[0]
        assert e.owner_layer == OwnerLayer.L3_WIRING
        assert e.severity == Severity.ERROR
        assert e.node_id == "op"
        assert e.detail["terminal_artifact_type"] == "Series"
        assert e.detail["expected_answer_shapes"] == ["scalar"]

    def test_scalar_terminal_satisfies_scalar_contract(self):
        res = validate_workflow_result(
            _wf_scalar_terminal(),
            expected_answer_shapes=frozenset({AnswerShape.SCALAR}),
        )
        assert res.is_clean
        assert not res.by_code(ErrorCode.E_TERMINAL_SHAPE_MISMATCH)

    def test_any_contract_is_permissive(self):
        res = validate_workflow_result(
            _wf_series_terminal(),
            expected_answer_shapes=frozenset({AnswerShape.ANY}),
        )
        assert not res.by_code(ErrorCode.E_TERMINAL_SHAPE_MISMATCH)

    def test_multi_shape_contract_permits_series(self):
        res = validate_workflow_result(
            _wf_series_terminal(),
            expected_answer_shapes=frozenset(
                {AnswerShape.SERIES, AnswerShape.SCALAR}
            ),
        )
        assert not res.by_code(ErrorCode.E_TERMINAL_SHAPE_MISMATCH)

    def test_no_contract_is_backcompat_noop(self):
        # The legacy call (no expected_answer_shapes) never runs CHECK 10.
        res = validate_workflow_result(_wf_series_terminal())
        assert not res.by_code(ErrorCode.E_TERMINAL_SHAPE_MISMATCH)
        assert res.is_clean


# ============================================================================
# 3. Param-sanity check — CHECK 11 / E_PARAM_SANITY (D4)
# ============================================================================


class TestParamSanity:
    def test_min_periods_gt_window_flags(self):
        wf = _wf_terminal("rolling_zscore", {"window": 10, "min_periods": 20})
        res = validate_workflow_result(wf)
        assert not res.is_clean
        errs = res.by_code(ErrorCode.E_PARAM_SANITY)
        assert len(errs) == 1
        assert errs[0].owner_layer == OwnerLayer.L3_WIRING
        assert errs[0].operator_name == "rolling_zscore"
        assert "min_periods" in errs[0].message

    def test_min_periods_le_window_ok(self):
        wf = _wf_terminal("rolling_zscore", {"window": 60, "min_periods": 30})
        res = validate_workflow_result(wf)
        assert not res.by_code(ErrorCode.E_PARAM_SANITY)

    def test_min_periods_absent_ok(self):
        # min_periods defaults to window → invariant holds → no flag.
        wf = _wf_terminal("rolling_zscore", {"window": 60})
        res = validate_workflow_result(wf)
        assert not res.by_code(ErrorCode.E_PARAM_SANITY)

    def test_non_rolling_operator_has_no_hook(self):
        # summarize_series has no param_sanity_validator → never flagged,
        # whatever odd params it carries.
        wf = _wf_terminal("summarize_series", {"min_periods": 999})
        res = validate_workflow_result(wf)
        assert not res.by_code(ErrorCode.E_PARAM_SANITY)

    def test_all_four_rolling_ops_have_the_hook(self):
        from shared.workflow.registry import OPERATOR_REGISTRY
        for name in (
            "rolling_zscore",
            "rolling_statistic",
            "rolling_correlation",
            "rolling_regression",
        ):
            assert (
                OPERATOR_REGISTRY[name].param_sanity_validator is not None
            ), f"{name} missing param_sanity_validator"


# ============================================================================
# 4. A5 — the "never ship wrong" invariant
# ============================================================================


class TestNeverShipWrongInvariant:
    def test_shape_mismatch_makes_result_not_clean(self):
        # The deterministic gate (is_clean) blocks a shape-mismatched DAG.
        # Combined with the executor only running is_clean workflows, a
        # wrong-shaped answer cannot reach execution (full end-to-end
        # gating is exercised in the Phase B self-correction tests).
        res = validate_workflow_result(
            _wf_series_terminal(),
            expected_answer_shapes=frozenset({AnswerShape.SCALAR}),
        )
        assert res.is_clean is False
        assert res.first() is not None
        assert res.first().code == ErrorCode.E_TERMINAL_SHAPE_MISMATCH

    def test_param_insanity_makes_result_not_clean(self):
        res = validate_workflow_result(
            _wf_terminal("rolling_zscore", {"window": 5, "min_periods": 60})
        )
        assert res.is_clean is False

    def test_clean_dag_with_satisfied_contract_is_clean(self):
        # Positive control: the right shape + sane params → executes.
        res = validate_workflow_result(
            _wf_scalar_terminal(),
            expected_answer_shapes=frozenset({AnswerShape.SCALAR}),
        )
        assert res.is_clean

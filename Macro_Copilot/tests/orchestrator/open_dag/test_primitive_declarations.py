"""tests/orchestrator/open_dag/test_primitive_declarations.py — PR-A3 corrective.

Replaces the earlier ``TestDeclarePrimitiveOutputType`` block that
tested the thin ``declare_primitive_output_type`` helper.  Per
plan ``tmp/orchestration.md`` §PR-3 lines 358 and 447, the
declaration object needs to carry output_artifact_type,
output_field_units, and available output fields — not just one
string.

These tests use the live ``rates_primitive_resolver`` (no DB engine —
metadata-only lookup) and a synthetic exploding-callable resolver to
prove the helper never invokes the primitive.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError as PydanticValidationError

from shared.workflow import PrimitiveSpec
from orchestrator.open_dag.primitive_declarations import (
    PrimitiveDeclaration,
    declare_primitive_output,
)


# ============================================================================
# RICHER DECLARATION FIELDS
# ============================================================================


class TestDeclarationFields:
    def test_curve_spread_declares_series_with_units(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        decl = declare_primitive_output(
            rates_primitive_resolver, "calculate_curve_spread_tool",
        )
        assert isinstance(decl, PrimitiveDeclaration)
        assert decl.tool_name == "calculate_curve_spread_tool"
        assert decl.output_artifact_type == "Series"
        # Curve spread has at least one declared output_field
        # (time_series + family-specific extras); the assertion
        # accepts any non-empty declaration.
        assert decl.output_field_units, (
            "calculate_curve_spread_tool should declare at least one "
            "output_field_units entry — see rates_agent.workflows "
            "primitive registration."
        )
        # available_output_fields is the sorted tuple of keys.
        assert (
            decl.available_output_fields
            == tuple(sorted(decl.output_field_units.keys()))
        )

    def test_panel_primitive_declares_panel(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        decl = declare_primitive_output(
            rates_primitive_resolver, "build_sovereign_yield_panel_tool",
        )
        assert decl.output_artifact_type == "Panel"

    def test_declaration_is_frozen(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        decl = declare_primitive_output(
            rates_primitive_resolver, "calculate_curve_spread_tool",
        )
        with pytest.raises(PydanticValidationError):
            decl.tool_name = "mutated"  # type: ignore[misc]


# ============================================================================
# NO-EXECUTION CONTRACT
# ============================================================================


class TestNoExecution:
    def test_does_not_invoke_primitive_callable(self) -> None:
        """Pass a synthetic resolver whose primitive's callable would
        assert-fail on invocation; declare_primitive_output must NOT
        call it."""

        def _explodes(*args, **kwargs):
            raise AssertionError(
                "declare_primitive_output invoked the primitive's "
                "callable — violates the no-execution contract."
            )

        class _Input(BaseModel):
            pass

        class _Output(BaseModel):
            pass

        spec = PrimitiveSpec(
            tool_name="exploding_tool",
            callable=_explodes,
            input_class=_Input,
            output_class=_Output,
            config_path=Path("/dev/null"),
            output_artifact_type="SeriesSet",
            output_field_units={"time_series_bps": "bps"},
        )

        def _resolver(tool_name: str) -> PrimitiveSpec:
            assert tool_name == "exploding_tool"
            return spec

        decl = declare_primitive_output(_resolver, "exploding_tool")
        assert decl.tool_name == "exploding_tool"
        assert decl.output_artifact_type == "SeriesSet"
        assert decl.output_field_units == {"time_series_bps": "bps"}
        assert decl.available_output_fields == ("time_series_bps",)


# ============================================================================
# RESOLVER ERROR PROPAGATION
# ============================================================================


class TestResolverErrors:
    def test_unknown_tool_propagates_resolver_keyerror(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        with pytest.raises(KeyError):
            declare_primitive_output(
                rates_primitive_resolver,
                "definitely_not_a_registered_primitive",
            )


# ============================================================================
# PARAMS-RESERVED CONTRACT
# ============================================================================


class TestParamsReserved:
    def test_params_argument_is_currently_ignored(self) -> None:
        from rates_agent.workflows import rates_primitive_resolver

        a = declare_primitive_output(
            rates_primitive_resolver, "calculate_curve_spread_tool",
        )
        b = declare_primitive_output(
            rates_primitive_resolver,
            "calculate_curve_spread_tool",
            params={"curve_family": "UST", "short_tenor": "2Y"},
        )
        assert a == b

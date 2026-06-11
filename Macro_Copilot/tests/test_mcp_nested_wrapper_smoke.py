"""
test_mcp_nested_wrapper_smoke.py — Falsifies the assumption that
FastMCP can introspect and serve nested-Pydantic tool signatures.

Why this test exists
--------------------
The v6 plan committed to a nested-MCP-wrapper pattern (mirroring the
Pydantic input model directly in the tool function signature) for
tools whose schemas have nested types: rolling_regression,
beta_adjusted_spread, half_life, yield_change_attribution_pca,
yield_change_decomposition_simple.  Until rolling_regression landed,
the codebase had no precedent for this pattern — every existing tool
used flat scalar wrappers.

Codex's v5/v6 reviews flagged this as an integration risk: "the new
nested-MCP wrapper pattern is plausible, but still unproven in this
repo."  This smoke test makes the assumption falsifiable.  If it
ever fails, the v6 plan must downgrade nested-input tools to a flat-
JSON-string-arg pattern (target_spec_json: str, parsed inside the
wrapper).

What this test pins
-------------------
A. Schema generation — registration + introspection:
   1. ``calculate_rolling_regression_tool`` is registered on the MCP server.
   2. Its inputSchema is a well-formed JSON-Schema object with
      nested ``$defs`` for SeriesSpec.
   3. The schema correctly types target_spec as a SeriesSpec ref
      and regressor_specs as an array of SeriesSpec refs.
   4. The schema's required-fields set matches the tool's required
      parameters.

B. Transport execution — actually invoking through call_tool():
   5. A valid structured-JSON args dict reaches the tool body and
      produces the expected snapshot output.
   6. Invalid nested input surfaces as a controlled error envelope
      at the MCP boundary, NOT a Python traceback.
   7. Codex follow-up: schema-generation success was previously
      claimed as "risk falsified", but until call_tool was
      exercised, the transport path was unproven.  This block
      closes that gap.

The tests use FastMCP's introspection API (``list_tools``) and its
transport API (``call_tool``) — the same APIs the MCP client uses
when negotiating with and invoking tools on a server.
"""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# Module-scoped event-loop helper.  FastMCP's list_tools() is an
# async coroutine; pytest-asyncio is not configured in this repo, so
# we drive it manually via asyncio.run().
# ---------------------------------------------------------------------------

def _list_mcp_tools_sync():
    from rates_agent.sovereign_bonds.mcp_server import mcp
    return asyncio.run(mcp.list_tools())


def _find_tool(tools, name: str):
    for t in tools:
        if t.name == name:
            return t
    raise AssertionError(
        f"tool {name!r} not registered on the MCP server "
        f"(available: {sorted(t.name for t in tools)})"
    )


# ===========================================================================
# 1. The nested-input tool is registered
# ===========================================================================

class TestNestedInputToolRegistered:
    def test_rolling_regression_tool_present(self):
        tools = _list_mcp_tools_sync()
        tool = _find_tool(tools, "calculate_rolling_regression_tool")
        assert tool.name == "calculate_rolling_regression_tool"
        # FastMCP populates a description from the docstring.
        assert tool.description, "rolling_regression_tool has no description"


# ===========================================================================
# 2. The schema is well-formed with nested $defs
# ===========================================================================

class TestNestedSchemaShape:
    def test_schema_has_seriesspec_def(self):
        tools = _list_mcp_tools_sync()
        tool = _find_tool(tools, "calculate_rolling_regression_tool")
        schema = tool.inputSchema
        assert isinstance(schema, dict)
        assert schema.get("type") == "object"
        # Pydantic emits nested object types under $defs.
        defs = schema.get("$defs", {})
        assert "SeriesSpec" in defs, (
            "SeriesSpec should appear under $defs because rolling_"
            "regression's input mirrors the Pydantic model directly"
        )
        series_spec = defs["SeriesSpec"]
        assert series_spec.get("type") == "object"
        # SeriesSpec's fields are present.
        spec_props = series_spec.get("properties", {})
        for f in ("curve_family", "tenor", "field_name"):
            assert f in spec_props, f"SeriesSpec missing field {f!r}"
        # curve_family + tenor are required, field_name is optional.
        spec_required = set(series_spec.get("required", []))
        assert spec_required == {"curve_family", "tenor"}

    def test_target_spec_is_seriesspec_ref(self):
        tools = _list_mcp_tools_sync()
        tool = _find_tool(tools, "calculate_rolling_regression_tool")
        schema = tool.inputSchema
        target_prop = schema["properties"]["target_spec"]
        # Pydantic v2 emits {'$ref': '#/$defs/SeriesSpec'} for nested
        # model fields.
        assert target_prop.get("$ref") == "#/$defs/SeriesSpec", (
            f"target_spec property is not a SeriesSpec ref: {target_prop!r}"
        )

    def test_regressor_specs_is_array_of_seriesspec(self):
        tools = _list_mcp_tools_sync()
        tool = _find_tool(tools, "calculate_rolling_regression_tool")
        schema = tool.inputSchema
        regressor_prop = schema["properties"]["regressor_specs"]
        assert regressor_prop.get("type") == "array"
        items = regressor_prop.get("items", {})
        assert items.get("$ref") == "#/$defs/SeriesSpec", (
            f"regressor_specs items are not SeriesSpec refs: {items!r}"
        )

    def test_central_knob_is_required_integer(self):
        tools = _list_mcp_tools_sync()
        tool = _find_tool(tools, "calculate_rolling_regression_tool")
        schema = tool.inputSchema
        # regression_window_days must be present, integer, required.
        prop = schema["properties"]["regression_window_days"]
        assert prop.get("type") == "integer"
        required = set(schema.get("required", []))
        assert "regression_window_days" in required, (
            "regression_window_days must be required at the MCP "
            "schema layer (it's the central knob)"
        )

    def test_top_level_required_set(self):
        tools = _list_mcp_tools_sync()
        tool = _find_tool(tools, "calculate_rolling_regression_tool")
        schema = tool.inputSchema
        required = set(schema.get("required", []))
        # target_spec, regressor_specs, regression_window_days are
        # required.  lookback_days has a default so it's optional.
        assert {"target_spec", "regressor_specs", "regression_window_days"} \
            <= required


# ===========================================================================
# 3. Sibling tools' flat schemas are unchanged (precedent test should
# not regress the existing pattern)
# ===========================================================================

class TestFlatToolsStillFlat:
    """Sanity check: introducing a nested-input tool did not silently
    coerce the existing flat tools (zscore_custom et al.) into nested
    schemas."""

    def test_zscore_custom_schema_is_flat(self):
        tools = _list_mcp_tools_sync()
        tool = _find_tool(tools, "calculate_zscore_custom_tool")
        schema = tool.inputSchema
        # No $defs needed — every property is a primitive type.
        # (Pydantic may still emit an empty $defs; check property types.)
        props = schema["properties"]
        for name, prop in props.items():
            # Each prop is either a primitive (type: integer/string)
            # or anyOf for Optional[primitive].
            if "type" in prop:
                assert prop["type"] in ("integer", "string", "boolean", "number"), (
                    f"zscore_custom param {name!r} unexpectedly typed "
                    f"as {prop['type']!r}"
                )
            elif "anyOf" in prop:
                # Optional[primitive] form, e.g. Optional[str]
                continue
            else:
                raise AssertionError(
                    f"zscore_custom param {name!r} has an unrecognised "
                    f"schema shape {prop!r}; this should be flat scalar"
                )


# ===========================================================================
# 4. Transport execution — invoking via call_tool()
#
# The schema-generation tests above prove FastMCP CAN INTROSPECT a
# nested-Pydantic signature.  These tests prove FastMCP CAN ACTUALLY
# RUN one — i.e., the structured-JSON args path through the transport
# layer reaches the tool body, and the validation-error path surfaces
# cleanly at the MCP boundary.  Codex's review of the initial PR
# pointed out that the schema-only tests were not enough to call the
# nested-wrapper risk fully falsified; this block closes that gap.
# ===========================================================================

import json
from unittest.mock import MagicMock, patch


def _well_formed_rr_output() -> dict:
    """Minimal-shape return value the wrapper expects from
    ``calculate_rolling_regression``.  Reused across the two
    transport-execution tests."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "target_label": "UST_10Y",
            "regressor_labels": ["DE_BUND_10Y"],
            "current_alpha_pct": 0.50,
            "current_betas": {"DE_BUND_10Y": 1.50},
            "current_residual_pct": 0.01,
            "current_r_squared": 0.95,
            "current_condition_flag": 0,
            "regression_window_days_used": 252,
            "regression_min_periods_used": 30,
            "add_constant_used": True,
            "observation_count": 252,
        },
        "time_series_betas": [],
        "time_series_alpha": {
            "series_name": "x", "units": "percent", "description": "x",
            "rows": [],
        },
        "time_series_residual": {
            "series_name": "x", "units": "percent", "description": "x",
            "rows": [],
        },
        "time_series_r_squared": {
            "series_name": "x", "units": "ratio", "description": "x",
            "rows": [],
        },
        "time_series_condition_flag": {
            "series_name": "x", "units": "count", "description": "x",
            "rows": [],
        },
    }


class TestTransportExecution:
    """The risk Codex flagged: introspection success ≠ transport
    success.  These tests actually invoke the registered tool through
    FastMCP's call_tool() path with structured JSON args."""

    def test_call_tool_with_valid_nested_args_succeeds(self):
        """Drive the full transport path: structured JSON args go in
        through ``call_tool``, FastMCP validates them against the
        introspected schema, constructs the SeriesSpec / List[SeriesSpec]
        Pydantic objects, and reaches the tool body.  We mock the
        engine + compute layer so the test does not hit the DB; the
        point is to verify the TRANSPORT path, not the math (which is
        covered by the compute tests)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_rolling_regression",
            return_value=_well_formed_rr_output(),
        ) as mock_compute:
            args = {
                "target_spec": {"curve_family": "UST", "tenor": "10Y"},
                "regressor_specs": [
                    {"curve_family": "DE_BUND", "tenor": "10Y"},
                ],
                "regression_window_days": 252,
                "lookback_days": 365,
            }
            result = asyncio.run(
                mcp_module.mcp.call_tool("calculate_rolling_regression_tool", args)
            )

        # FastMCP's call_tool returns (list[TextContent], dict).
        assert isinstance(result, tuple) and len(result) == 2
        contents, structured = result
        assert isinstance(contents, list) and len(contents) == 1
        text = contents[0].text
        # The wrapper returned a JSON-encoded dict; decode and verify
        # the transport path produced the structured snapshot we mocked.
        parsed = json.loads(text)
        assert "current_metrics" in parsed
        assert parsed["current_metrics"]["target_label"] == "UST_10Y"
        # And the underlying Python tool was invoked exactly once with
        # a properly-constructed Pydantic input object.
        assert mock_compute.call_count == 1
        params = mock_compute.call_args.kwargs["params"]
        assert params.target_spec.curve_family == "UST"
        assert params.target_spec.tenor == "10Y"
        assert len(params.regressor_specs) == 1
        assert params.regressor_specs[0].curve_family == "DE_BUND"
        assert params.regression_window_days == 252

    def test_call_tool_with_invalid_nested_args_surfaces_controlled_error(self):
        """Invalid input (missing required nested field) must surface
        as a clean MCP validation error — NOT an unhandled Python
        traceback.  FastMCP raises a ToolError whose message names the
        offending field."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        # Missing target_spec.tenor — schema validation should reject.
        args = {
            "target_spec": {"curve_family": "UST"},  # tenor missing
            "regressor_specs": [
                {"curve_family": "DE_BUND", "tenor": "10Y"},
            ],
            "regression_window_days": 252,
        }

        # FastMCP wraps the underlying validation error.  Catch any
        # exception and verify it names the missing field — that's
        # the user-facing surface the LLM client sees.
        with pytest.raises(Exception) as exc_info:
            asyncio.run(
                mcp_module.mcp.call_tool("calculate_rolling_regression_tool", args)
            )
        msg = str(exc_info.value)
        assert "tenor" in msg.lower(), (
            f"validation error did not name the missing nested field "
            f"'tenor'; got: {msg!r}"
        )

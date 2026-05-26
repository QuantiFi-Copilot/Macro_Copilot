"""
test_real_yield_level_wiring.py — Caller-wiring smoke tests for the
linker real_yield_level primitive.

Mirrors ``test_ois_rate_level_wiring.py`` (the structural OIS analog),
scoped to real_yield_level's external callers.  Today there are exactly
two:

  1. rates_agent/inflation_indexed_bonds/mcp_server.py
     ::get_real_yield_level_tool
  2. rates_agent/workflows/__init__.py PrimitiveSpec registration

Future REST endpoints + batch surfaces will be added behind the same
``CONFIG_PATH`` import.

Key load-bearing properties under test:
  - The MCP wrapper passes config explicitly (no auto-load fallback).
    ``config=`` argument is observable at the callsite.
  - The MCP wrapper uses the empty-string / None-sentinel pattern so
    the YAML ``default_field_name`` actually flows through.
  - The hub re-export and CONFIG_PATH public symbol are stable.
  - The wire-frozen + canonical TimeSeries fields survive a full
    round-trip through the response model.
  - The workflow registry exposes the primitive with the correct
    ``output_field_units`` declaration (``percent``).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
    CONFIG_PATH as REAL_YIELD_LEVEL_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_real_yield_level_output() -> dict:
    """Mock matches the wire shape: snapshot ``current_metrics`` PLUS
    the canonical ``shared.schemas.time_series.TimeSeries`` payload."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-08",
            "curve_family": "USD_TIPS",
            "tenor": "10Y",
            "real_yield_pct": 1.85,
            "daily_change_bps": 1.5,
            "weekly_change_bps": 8.0,
            "monthly_change_bps": -3.0,
            "z_score": 0.85,
            "high_252d_pct": 2.45,
            "low_252d_pct": 1.25,
            "percentile_252d": 58.0,
            "observation_count": 252,
        },
        "time_series": {
            "series_name": "usd_tips_10y_real_yield",
            "units": "percent",
            "description": "Test linker real-yield series.",
            "rows": [
                {"date": "2026-04-07", "value": 1.84},
                {"date": "2026-04-08", "value": 1.85},
            ],
        },
    }


def _assert_real_yield_level_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "get_real_yield_level called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "get_real_yield_level_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'get_real_yield_level_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_field_name" in cfg.conventions


# ===========================================================================
# rates_agent/inflation_indexed_bonds/mcp_server.py — get_real_yield_level_tool
# ===========================================================================

class TestMcpRealYieldLevelToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_real_yield_level",
            return_value=_well_formed_real_yield_level_output(),
        ) as mock_ryl:
            output_json = mcp_module.get_real_yield_level_tool(
                curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_ryl.call_count == 1
        _assert_real_yield_level_config_passed(mock_ryl.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'YLD_YTM_MID'.  Hardcoding any other
        string here shadows the YAML's default_field_name — same
        shadowing pattern fixed for sovereign curve_move_classifier in
        commit b2605ee."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_real_yield_level_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_real_yield_level",
            return_value=_well_formed_real_yield_level_output(),
        ) as mock_ryl:
            mcp_module.get_real_yield_level_tool(
                curve_family="USD_TIPS",
                tenor="10Y",
                lookback_days=365,
                # field_name omitted → defaults to ""
            )
        params = mock_ryl.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach RealYieldLevelInput as None "
            f"(sentinel for 'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_real_yield_level",
            return_value=_well_formed_real_yield_level_output(),
        ) as mock_ryl:
            mcp_module.get_real_yield_level_tool(
                curve_family="USD_TIPS",
                tenor="10Y",
                field_name="YLD_YTM_BID",
            )
        params = mock_ryl.call_args.kwargs["params"]
        assert params.field_name == "YLD_YTM_BID"

    def test_time_series_stripped_from_llm_response(self):
        """The MCP wrapper must NOT serialize the canonical TimeSeries
        payload back to the LLM — frontend REST surfaces get the full
        dict directly from the tool, but the LLM context shouldn't
        consume thousands of historical rows just to answer 'where's
        TIPS 10Y?'.  Mirrors sovereign get_yield_levels_tool's
        behaviour."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_real_yield_level",
            return_value=_well_formed_real_yield_level_output(),
        ):
            output_json = mcp_module.get_real_yield_level_tool(
                curve_family="USD_TIPS",
                tenor="10Y",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert "time_series" not in parsed, (
            "get_real_yield_level_tool must strip the canonical "
            "time_series before returning to the LLM"
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_real_yield_level_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(REAL_YIELD_LEVEL_CONFIG_PATH)
        assert cfg.tool.name == "get_real_yield_level_tool"
        assert cfg.tool.domain == "inflation_indexed_bonds"


# ===========================================================================
# Response-model validation
# ===========================================================================

class TestResponseModelValidation:
    """Pin the wire contract for real_yield_level.  Mirrors the
    response-model validation pattern added in PR #60 to the sovereign
    wiring suites — guards against the canonical TimeSeries shape
    silently regressing into a bespoke dict.
    """

    def test_mock_validates_against_response_model(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level.schemas import (
            RealYieldLevelOutput,
        )
        validated = RealYieldLevelOutput.model_validate(
            _well_formed_real_yield_level_output(),
        )
        assert validated.time_series is not None
        assert validated.time_series.units.value == "percent"

    def test_response_model_requires_time_series_field(self):
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level.schemas import (
            RealYieldLevelOutput,
        )
        from pydantic import ValidationError
        bad_mock = _well_formed_real_yield_level_output()
        bad_mock.pop("time_series")
        with pytest.raises(ValidationError):
            RealYieldLevelOutput.model_validate(bad_mock)

    def test_round_trip_preserves_canonical_fields(self):
        """``model_validate(...).model_dump()`` MUST preserve the
        canonical TimeSeries shape — guards against a future schema
        edit that drops fields silently on serialization."""
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level.schemas import (
            RealYieldLevelOutput,
        )
        validated = RealYieldLevelOutput.model_validate(
            _well_formed_real_yield_level_output(),
        )
        dumped = validated.model_dump()
        assert "current_metrics" in dumped
        assert "time_series" in dumped
        assert dumped["time_series"]["units"] == "percent"
        assert dumped["time_series"]["series_name"] == "usd_tips_10y_real_yield"


# ===========================================================================
# Workflow PrimitiveSpec registration
# ===========================================================================

class TestWorkflowRegistration:
    """Registration in ``rates_agent.workflows`` is part of the
    primitive contract (Codex P2 / PR #79).  These guard against an
    accidental miswiring that would break operator-template
    composition."""

    def test_resolver_returns_real_yield_level_spec(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("get_real_yield_level_tool")
        assert spec.tool_name == "get_real_yield_level_tool"
        assert spec.config_path == REAL_YIELD_LEVEL_CONFIG_PATH

    def test_spec_uses_correct_input_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
            RealYieldLevelInput, RealYieldLevelOutput,
        )
        spec = rates_primitive_resolver("get_real_yield_level_tool")
        assert spec.input_class is RealYieldLevelInput
        assert spec.output_class is RealYieldLevelOutput

    def test_spec_uses_correct_callable(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.inflation_indexed_bonds.tools.real_yield_level import (
            get_real_yield_level,
        )
        spec = rates_primitive_resolver("get_real_yield_level_tool")
        assert spec.callable is get_real_yield_level

    def test_output_field_units_declares_percent_for_time_series(self):
        """The canonical ``time_series`` field is in PERCENT units (real
        yield).  The substrate's validate-time unit-compat checks rely
        on this declaration to refuse downstream operators that would
        misinterpret the units."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("get_real_yield_level_tool")
        assert spec.output_field_units == {"time_series": "percent"}

    def test_known_rates_primitives_includes_real_yield_level(self):
        from rates_agent.workflows import known_rates_primitives
        assert "get_real_yield_level_tool" in known_rates_primitives()


# ===========================================================================
# Linker instrument_type guard — MCP wrapper surface
# ===========================================================================

class TestMcpLinkerInstrumentTypeGuard:
    """The MCP wrapper must surface the controlled-error envelope when
    the underlying tool refuses to compute against a nominal curve
    family.  Without this guarantee, an LLM hitting the linker tool
    with curve_family='UST' would receive nominal yields presented
    under ``real_yield_pct`` semantics — a proxy violation forbidden by
    DESIGN_PRINCIPLES.md §1, §3 and STANDARD_TOOL_AND_YAML_RULES.md §J.
    """

    def test_nominal_curve_family_surfaces_controlled_error_envelope(self):
        """Adversarial probe at the MCP boundary: when the underlying
        ``get_real_yield_level`` returns the controlled
        ``{"error": "..."}`` envelope (because the instrument_type
        filter eliminated all rows for a nominal curve), the wrapper
        must serialise that envelope back to the LLM verbatim — never
        wrap or hide it as a successful payload."""
        from rates_agent.inflation_indexed_bonds import mcp_server as mcp_module

        controlled_error = {
            "error": (
                "No linker real-yield data found for curve_family='UST', "
                "tenor='10Y', field='YLD_YTM_MID' since 2025-01-01 "
                "(instrument_type='inflation_linker').  If you passed a "
                "nominal sovereign curve_family (e.g. 'UST', 'DE_BUND'), "
                "use the sovereign_bonds get_yield_levels_tool instead — "
                "this tool only returns inflation-linker rows."
            )
        }
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "get_real_yield_level",
            return_value=controlled_error,
        ):
            output_json = mcp_module.get_real_yield_level_tool(
                curve_family="UST",
                tenor="10Y",
                lookback_days=365,
            )
        parsed = json.loads(output_json)
        assert "error" in parsed, (
            "MCP wrapper must surface the controlled error envelope when "
            "the underlying tool refuses a nominal curve family — "
            f"instead got {parsed!r}"
        )
        # No fabricated metrics may leak through.
        assert "current_metrics" not in parsed
        assert "real_yield_pct" not in parsed
        # The instrument_type rationale must travel back to the LLM so
        # it can route nominal questions to the sovereign tool instead
        # of retrying.
        assert "inflation_linker" in parsed["error"]

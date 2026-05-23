"""
test_futures_price_level_wiring.py — Caller-wiring smoke tests for the
                                      bond-futures price-level monitor

Mirrors test_yield_levels_wiring.py for the futures_price_level tool.
Caller surface in V1:

  1. rates_agent/bond_futures/mcp_server.py::get_futures_price_level_tool

Key load-bearing properties under test:

  - MCP wrapper passes config= explicitly (no auto-load fallback).
  - MCP wrapper uses the empty-string / None-sentinel pattern so the
    YAML default_price_field actually flows through (the bug that
    bit curve_move_classifier and was fixed in commit b2605ee).
  - The LLM-facing response withholds the historical ``time_series``
    rows but PRESERVES the P5 ``methodology_disclosure`` (so the
    rolling-generic-price caveat is propagated upward).
  - Workflow registration is honest: PrimitiveSpec is registered with
    the correct CONFIG_PATH and with ``output_field_units = {}``
    (because the bond-futures price unit is not a member of the
    closed-enum TimeSeriesUnits family in V1 — see schemas module
    docstring + workflows registration comment).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.bond_futures.tools.futures_price_level import (
    CONFIG_PATH as FUTURES_PRICE_LEVEL_CONFIG_PATH,
    METHODOLOGY_DISCLOSURE,
    FuturesPriceLevelOutput,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    """Mock matches the live wire shape — MUST validate against
    ``FuturesPriceLevelOutput``."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST_FUT",
            "contract_code": "TY1",
            "tenor": "10Y",
            "quote_units": "points",
            "contract_size": 100000.0,
            "expiry_date": "2026-12-21",
            "security_name": "TYZ6 COMB",
            "current_price": 110.453125,
            "daily_change_price": -0.125,
            "weekly_change_price": -0.5,
            "monthly_change_price": 0.75,
            "z_score": -0.42,
            "high_252d_price": 113.5,
            "low_252d_price": 108.25,
            "percentile_252d": 41.9,
            "observation_count": 252,
        },
        "time_series": [
            {"date": "2026-04-29", "price": 110.578125},
            {"date": "2026-04-30", "price": 110.453125},
        ],
        "methodology_disclosure": METHODOLOGY_DISCLOSURE,
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_futures_price_level called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "get_futures_price_level_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'get_futures_price_level_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "default_price_field" in cfg.conventions
    assert "price_round_decimals" in cfg.conventions


# ===========================================================================
# 1. MCP wrapper
# ===========================================================================

class TestMcpFuturesPriceLevelWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.bond_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_price_level",
            return_value=_well_formed_output(),
        ) as spy:
            output_json = mcp_module.get_futures_price_level_tool(
                curve_family="UST_FUT",
                contract_code="TY1",
                lookback_days=365,
                field_name="PX_LAST",
            )
        assert spy.call_count == 1
        _assert_config_passed(spy.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        # P5 disclosure must reach the LLM-facing payload.
        assert parsed.get("methodology_disclosure") == METHODOLOGY_DISCLOSURE
        # time_series is stripped from the LLM-facing payload (frontend
        # REST surfaces get the full payload via the dict result).
        assert "time_series" not in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'PX_LAST'. Hardcoding any other string
        here shadows the YAML's default_price_field."""
        from rates_agent.bond_futures import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.get_futures_price_level_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.bond_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_price_level",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_price_level_tool(
                curve_family="UST_FUT",
                contract_code="TY1",
                lookback_days=365,
                # field_name omitted
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach FuturesPriceLevelInput as None "
            f"(sentinel for 'use YAML default_price_field'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.bond_futures import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_futures_price_level",
            return_value=_well_formed_output(),
        ) as spy:
            mcp_module.get_futures_price_level_tool(
                curve_family="UST_FUT",
                contract_code="TY1",
                field_name="PX_BID",
            )
        params = spy.call_args.kwargs["params"]
        assert params.field_name == "PX_BID"

    def test_validation_error_returns_controlled_envelope(self):
        from rates_agent.bond_futures import mcp_server as mcp_module

        # Pass an empty curve_family (min_length=1 fails) — must produce
        # the controlled {"error": ...} envelope, not raise.
        output_json = mcp_module.get_futures_price_level_tool(
            curve_family="",
            contract_code="TY1",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]


# ===========================================================================
# 2. CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.bond_futures.tools.futures_price_level import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.bond_futures.tools.futures_price_level.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_futures_price_level_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(FUTURES_PRICE_LEVEL_CONFIG_PATH)
        assert cfg.tool.name == "get_futures_price_level_tool"
        assert cfg.tool.domain == "bond_futures"


# ===========================================================================
# 3. Response-model validation (mock parity)
# ===========================================================================

class TestResponseModelValidation:
    def test_mock_validates_against_response_model(self):
        # If this raises, the wiring-test mock is out of sync with the
        # live schema (which is exactly the failure mode this guard
        # pins).
        validated = FuturesPriceLevelOutput.model_validate(_well_formed_output())
        assert validated.methodology_disclosure == METHODOLOGY_DISCLOSURE
        assert validated.current_metrics.current_price == 110.453125

    def test_response_model_requires_methodology_disclosure(self):
        """Removing methodology_disclosure from the mock MUST raise —
        proves the field is REQUIRED on the response model, not optional.
        Without this guard, the P5 caveat could be dropped in a future
        refactor and the wiring tests would still pass."""
        from pydantic import ValidationError
        bad = _well_formed_output()
        bad.pop("methodology_disclosure")
        with pytest.raises(ValidationError):
            FuturesPriceLevelOutput.model_validate(bad)


# ===========================================================================
# 4. Workflow registration
# ===========================================================================

class TestWorkflowRegistration:
    def test_registered_in_rates_primitive_resolver(self):
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("get_futures_price_level_tool")
        assert spec.tool_name == "get_futures_price_level_tool"
        assert spec.config_path == FUTURES_PRICE_LEVEL_CONFIG_PATH

    def test_output_field_units_is_empty_by_design(self):
        """P8 / P5: bond-futures price space has no honest member of
        the closed-enum ``TimeSeriesUnits`` family in V1. Declaring
        ``percent`` or ``bps`` would silently lie. ``{}`` is the
        documented exempt mode (see shared/workflow/validate.py:368)
        until a future ADR extends TimeSeriesUnits."""
        from rates_agent.workflows import rates_primitive_resolver
        spec = rates_primitive_resolver("get_futures_price_level_tool")
        assert spec.output_field_units == {}

    def test_registered_input_and_output_classes(self):
        from rates_agent.workflows import rates_primitive_resolver
        from rates_agent.bond_futures.tools.futures_price_level import (
            FuturesPriceLevelInput,
            FuturesPriceLevelOutput,
        )
        spec = rates_primitive_resolver("get_futures_price_level_tool")
        assert spec.input_class is FuturesPriceLevelInput
        assert spec.output_class is FuturesPriceLevelOutput

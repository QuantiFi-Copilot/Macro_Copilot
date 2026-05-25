"""
test_asset_swap_spread_wiring.py — Caller-wiring tests for the per-bond
INGESTED Bloomberg ASW primitive.
=======================================================================

Pins the MCP wrapper's load-bearing properties:

  - Imports CONFIG_PATH + compute function from the primitive package.
  - Loads bundled config explicitly with
    ``load_tool_config(ASSET_SWAP_SPREAD_CONFIG_PATH)`` and passes
    ``config=`` to the compute function (PR7 + DESIGN_PRINCIPLES §8).
  - Translates the empty-string ``field_name`` sentinel to None so
    the YAML default flows through (curve_move_classifier wrapper-
    shadowing fix, commit b2605ee).
  - Translates the empty-string ``as_of_date`` sentinel to None so
    latest-mode flows through.
  - Converts ``AssetSwapSpreadUnavailableError`` (typed P6 refusal)
    into a controlled JSON error envelope at the transport boundary.
  - Converts Pydantic ``ValidationError`` into a controlled error envelope.
  - Converts unexpected exceptions into a controlled error envelope.
  - Registered in workflows ``_PRIMITIVE_SPECS`` (NOT in
    WORKFLOW_INCOMPATIBLE_TOOLS — output IS bridge-composable as a
    BPS TimeSeries).
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from rates_agent.ois.tools.asset_swap_spread import (
    CONFIG_PATH as ASSET_SWAP_SPREAD_CONFIG_PATH,
    AssetSwapSpreadUnavailableError,
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


def _well_formed_output() -> dict:
    """Mock matching the live AssetSwapSpreadOutput shape."""
    return {
        "current_metrics": {
            "as_of_date": "2026-05-21",
            "vendor_ticker": "/isin/DE000BU22130",
            "country": "Germany",
            "currency": "EUR",
            "cusip": "DI7485532",
            "isin": "DE000BU22130",
            "maturity_date": "2028-06-14",
            "current_asw_spread_bps": -19.471,
            "daily_change_bps": None,
            "weekly_change_bps": None,
            "monthly_change_bps": None,
            "z_score": None,
            "high_252d_bps": None,
            "low_252d_bps": None,
            "percentile_252d": None,
            "observation_count": 1,
            "lookback_days": 365,
        },
        "time_series": {
            "series_name": "asw_spread_isin_de000bu22130",
            "units": "bps",
            "description": "x",
            "rows": [{"date": "2026-05-21", "value": -19.471}],
        },
        "methodology_note": (
            "Source: ``ASSET_SWAP_SPD_MID`` ingested from Bloomberg... "
            "P12 ... swap_spread ... CAD ... security_name ... "
            "AssetSwapSpreadUnavailableError ..."
        ),
    }


def _assert_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "compute called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "get_asset_swap_spread_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'get_asset_swap_spread_tool' (catches imports of the wrong "
        "tool's CONFIG_PATH)"
    )
    assert "default_asw_field_name" in cfg.conventions


# ===========================================================================
# MCP wrapper
# ===========================================================================


class TestMcpWrapperWiring:
    def test_passes_config_to_compute(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module, "get_asset_swap_spread",
            return_value=_well_formed_output(),
        ) as mock_compute:
            output_json = mcp_module.get_asset_swap_spread_tool(
                vendor_ticker="/isin/DE000BU22130",
                as_of_date="",
                lookback_days=365,
                field_name="",
            )
        assert mock_compute.call_count == 1
        _assert_config_passed(mock_compute.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        assert parsed["current_metrics"]["current_asw_spread_bps"] == -19.471

    def test_field_name_empty_sentinel_flows_none(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module, "get_asset_swap_spread",
            return_value=_well_formed_output(),
        ) as mock_compute:
            mcp_module.get_asset_swap_spread_tool(
                vendor_ticker="/isin/X",
                field_name="",  # sentinel
            )
        params = mock_compute.call_args.kwargs["params"]
        assert params.field_name is None

    def test_field_name_explicit_passes_through(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module, "get_asset_swap_spread",
            return_value=_well_formed_output(),
        ) as mock_compute:
            mcp_module.get_asset_swap_spread_tool(
                vendor_ticker="/isin/X",
                field_name="ASSET_SWAP_SPD_BID",
            )
        params = mock_compute.call_args.kwargs["params"]
        assert params.field_name == "ASSET_SWAP_SPD_BID"

    def test_as_of_date_empty_sentinel_flows_none(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module, "get_asset_swap_spread",
            return_value=_well_formed_output(),
        ) as mock_compute:
            mcp_module.get_asset_swap_spread_tool(
                vendor_ticker="/isin/X",
                as_of_date="",
            )
        params = mock_compute.call_args.kwargs["params"]
        assert params.as_of_date is None

    def test_as_of_date_explicit_parsed(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module, "get_asset_swap_spread",
            return_value=_well_formed_output(),
        ) as mock_compute:
            mcp_module.get_asset_swap_spread_tool(
                vendor_ticker="/isin/X",
                as_of_date="2026-05-21",
            )
        params = mock_compute.call_args.kwargs["params"]
        assert params.as_of_date == date(2026, 5, 21)

    def test_invalid_as_of_date_returns_envelope(self):
        from rates_agent.ois import mcp_server as mcp_module

        output_json = mcp_module.get_asset_swap_spread_tool(
            vendor_ticker="/isin/X",
            as_of_date="not-a-date",
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "as_of_date" in parsed["error"]

    def test_invalid_params_return_envelope(self):
        from rates_agent.ois import mcp_server as mcp_module

        output_json = mcp_module.get_asset_swap_spread_tool(
            vendor_ticker="",  # min_length=1 violation
        )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_typed_exception_converted_to_envelope(self):
        """AssetSwapSpreadUnavailableError at the compute layer is
        converted to a controlled error envelope at the MCP boundary
        (P6 transport boundary)."""
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module, "get_asset_swap_spread",
            side_effect=AssetSwapSpreadUnavailableError(
                vendor_ticker="/isin/MISSING",
                reason="vendor_ticker not found in macro_data.instrument_master",
            ),
        ):
            output_json = mcp_module.get_asset_swap_spread_tool(
                vendor_ticker="/isin/MISSING",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed
        assert "/isin/MISSING" in parsed["error"]
        assert "instrument_master" in parsed["error"]

    def test_unexpected_exception_converted_to_envelope(self):
        from rates_agent.ois import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module, "get_asset_swap_spread",
            side_effect=RuntimeError("synthetic"),
        ):
            output_json = mcp_module.get_asset_swap_spread_tool(
                vendor_ticker="/isin/X",
            )
        parsed = json.loads(output_json)
        assert "error" in parsed


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================


class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.ois.tools.asset_swap_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.ois.tools.asset_swap_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_asset_swap_spread_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(ASSET_SWAP_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "get_asset_swap_spread_tool"


# ===========================================================================
# Workflow registration — IS bridge-composable (TimeSeries output)
# ===========================================================================


class TestWorkflowRegistration:
    def test_registered_in_primitive_specs(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS
        assert "get_asset_swap_spread_tool" in _PRIMITIVE_SPECS

    def test_not_in_workflow_incompatible_tools(self):
        """asset_swap_spread emits a canonical BPS TimeSeries — IS
        bridge-composable.  Must NOT be in WORKFLOW_INCOMPATIBLE_TOOLS."""
        from rates_agent.workflows import WORKFLOW_INCOMPATIBLE_TOOLS
        assert "get_asset_swap_spread_tool" not in WORKFLOW_INCOMPATIBLE_TOOLS

    def test_primitive_spec_output_units(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS
        spec = _PRIMITIVE_SPECS["get_asset_swap_spread_tool"]
        assert spec.output_field_units == {"time_series": "bps"}

    def test_primitive_spec_callable_and_classes(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS
        from rates_agent.ois.tools.asset_swap_spread import (
            AssetSwapSpreadInput,
            AssetSwapSpreadOutput,
            get_asset_swap_spread,
        )
        spec = _PRIMITIVE_SPECS["get_asset_swap_spread_tool"]
        assert spec.callable is get_asset_swap_spread
        assert spec.input_class is AssetSwapSpreadInput
        assert spec.output_class is AssetSwapSpreadOutput


# ===========================================================================
# Mock-vs-schema parity
# ===========================================================================


class TestMockValidatesAgainstSchema:
    def test_well_formed_mock_validates(self):
        """The wiring mock MUST validate against the live
        AssetSwapSpreadOutput model."""
        from rates_agent.ois.tools.asset_swap_spread import (
            AssetSwapSpreadOutput,
        )
        validated = AssetSwapSpreadOutput.model_validate(_well_formed_output())
        assert validated.current_metrics.current_asw_spread_bps == -19.471

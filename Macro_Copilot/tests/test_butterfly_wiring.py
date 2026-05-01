"""
test_butterfly_wiring.py — Caller-wiring smoke tests

Mirrors test_yield_levels_wiring.py but smaller, because butterfly has
NO separate batch reimplementation (no _compute_butterfly_snapshot in
api/routes/rates/cards.py).  The two external callers are:

  1. rates_agent/sovereign_bonds/mcp_server.py::calculate_butterfly_tool
  2. api/routes/rates/detail.py::butterfly_detail (/detail/butterfly)

Key load-bearing properties under test:

  - Each surface passes config explicitly (no auto-load fallback).
  - Each surface uses the empty-string / None-sentinel pattern so
    the YAML default_field_name actually flows through (the bug that
    bit curve_move_classifier and was fixed in commit b2605ee).
  - The hub re-export and CONFIG_PATH public symbol are stable.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.butterfly import (
    CONFIG_PATH as BUTTERFLY_CONFIG_PATH,
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


def _well_formed_butterfly_output() -> dict:
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST",
            "butterfly_label": "2s5s10s",
            "current_butterfly_bps": -45.20,
            "daily_change_bps": 1.5,
            "current_z_score": -0.85,
            "rolling_window_days": 252,
            "high_252d_bps": 12.40,
            "low_252d_bps": -78.10,
            "percentile_252d": 36.2,
            "wing_short_bps": -85.30,
            "wing_long_bps": -130.50,
            "short_tenor_yield": 4.50,
            "belly_tenor_yield": 4.20,
            "long_tenor_yield": 4.40,
        },
        "time_series": [],
    }


def _assert_butterfly_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_butterfly called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "calculate_butterfly_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'calculate_butterfly_tool' (catches imports of the wrong tool's "
        "CONFIG_PATH)"
    )
    assert "z_score_window_days" in cfg.conventions
    assert "bps_round_decimals" in cfg.conventions


# ===========================================================================
# api/routes/rates/detail.py — /detail/butterfly endpoint
# ===========================================================================

class TestDetailButterflyEndpointWiring:
    def test_butterfly_detail_passes_config(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bf:
            detail_module.butterfly_detail(
                engine=mock_engine,
                curve_family="UST",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_bf.call_count == 1
        _assert_butterfly_config_passed(mock_bf.call_args)

    def test_field_name_default_is_none(self):
        """The Query default for field_name must be None; a hardcoded
        string would shadow the YAML's default_field_name."""
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.butterfly_detail)
        query_obj = sig.parameters["field_name"].default
        assert query_obj.default is None, (
            f"Query default for field_name must be None, got "
            f"{query_obj.default!r}"
        )

    def test_omitted_field_name_flows_none(self):
        """Mirrors FastAPI's request-parse resolution by passing
        field_name=None directly.  Confirms None reaches
        ButterflyInput unchanged."""
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bf:
            detail_module.butterfly_detail(
                engine=mock_engine,
                curve_family="UST",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                field_name=None,
            )
        params = mock_bf.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"None must reach ButterflyInput unchanged; got {params.field_name!r}"
        )


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — calculate_butterfly_tool
# ===========================================================================

class TestMcpButterflyToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bf:
            output_json = mcp_module.calculate_butterfly_tool(
                curve_family="UST",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_bf.call_count == 1
        _assert_butterfly_config_passed(mock_bf.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        """The MCP wrapper's field_name default must be the empty-
        string sentinel, NOT 'YLD_YTM_MID'.  Hardcoding any other
        string here shadows the YAML's default_field_name."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.calculate_butterfly_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_butterfly_input(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bf:
            mcp_module.calculate_butterfly_tool(
                curve_family="UST",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
                lookback_days=365,
                # field_name omitted
            )
        params = mock_bf.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach ButterflyInput as None "
            f"(sentinel for 'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_butterfly",
            return_value=_well_formed_butterfly_output(),
        ) as mock_bf:
            mcp_module.calculate_butterfly_tool(
                curve_family="UST",
                short_tenor="2Y",
                belly_tenor="5Y",
                long_tenor="10Y",
                field_name="PX_LAST",
            )
        params = mock_bf.call_args.kwargs["params"]
        assert params.field_name == "PX_LAST"


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.butterfly import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.butterfly.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_butterfly_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(BUTTERFLY_CONFIG_PATH)
        assert cfg.tool.name == "calculate_butterfly_tool"

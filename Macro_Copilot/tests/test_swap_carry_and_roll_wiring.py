"""Caller-wiring tests for swap_carry_and_roll (§7-C). All offline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.swap_carry_and_roll import (
    CONFIG_PATH as SWAP_CARRY_AND_ROLL_CONFIG_PATH,
)
from shared.config import clear_tool_config_cache, load_tool_config


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed() -> dict:
    return {
        "current_metrics": {
            "as_of_date": "2026-05-22", "curve_family": "USD_SOFR_OIS",
            "tenor": "10Y", "horizon": "3M", "spot_rate_pct": 4.0,
            "rolled_spot_rate_pct": 3.99, "financing_rate_pct": 3.0,
            "roll_bps": 1.5, "carry_bps": 100.0, "total_carry_roll_bps": 101.5,
        },
        "time_series": [{"date": "2026-05-22", "roll_bps": 1.5,
                         "carry_bps": 100.0, "total_bps": 101.5}],
        "time_series_total_carry_roll": {
            "series_name": "USD_SOFR_OIS_10Y_3M_carry_roll", "units": "bps",
            "description": "x", "rows": [],
        },
        "methodology_disclosures": ["d"],
    }


def test_config_identity():
    cfg = load_tool_config(SWAP_CARRY_AND_ROLL_CONFIG_PATH)
    assert cfg.tool.name == "calculate_swap_carry_and_roll_tool"
    assert cfg.tool.domain == "ois"


class TestMcpWrapper:
    def test_passes_config_and_engine(self):
        from rates_agent.ois import mcp_server as mcp
        eng = MagicMock()
        with patch.object(mcp, "_get_engine", return_value=eng), patch.object(
            mcp, "calculate_swap_carry_and_roll", return_value=_well_formed(),
        ) as compute:
            out = mcp.calculate_swap_carry_and_roll_tool(curve_family="USD_SOFR_OIS")
        _, kw = compute.call_args
        assert kw["engine"] is eng
        assert kw["config"].tool.name == "calculate_swap_carry_and_roll_tool"
        parsed = json.loads(out)
        assert parsed["current_metrics"]["total_carry_roll_bps"] == 101.5
        assert "time_series" not in parsed

    def test_empty_horizon_and_field_sentinels(self):
        from rates_agent.ois import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_swap_carry_and_roll",
                    return_value=_well_formed(),
                ) as compute:
            mcp.calculate_swap_carry_and_roll_tool(
                curve_family="X", horizon="", field_name="")
        _, kw = compute.call_args
        assert kw["params"].horizon is None
        assert kw["params"].field_name is None

    def test_explicit_horizon_passes_through(self):
        from rates_agent.ois import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_swap_carry_and_roll",
                    return_value=_well_formed(),
                ) as compute:
            mcp.calculate_swap_carry_and_roll_tool(curve_family="X", horizon="6M")
        _, kw = compute.call_args
        assert kw["params"].horizon == "6M"

    def test_compute_exception_envelope(self):
        from rates_agent.ois import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_swap_carry_and_roll",
                    side_effect=RuntimeError("boom"),
                ):
            out = mcp.calculate_swap_carry_and_roll_tool(curve_family="X")
        parsed = json.loads(out)
        assert "error" in parsed and "Calculation failed" in parsed["error"]


def test_registered_bps_bridge():
    from rates_agent.workflows import _PRIMITIVE_SPECS
    from rates_agent.ois.tools.swap_carry_and_roll import (
        SwapCarryAndRollInput, SwapCarryAndRollOutput,
    )
    spec = _PRIMITIVE_SPECS["calculate_swap_carry_and_roll_tool"]
    assert spec.input_class is SwapCarryAndRollInput
    assert spec.output_class is SwapCarryAndRollOutput
    assert spec.output_field_units == {"time_series_total_carry_roll": "bps"}
    assert spec.output_artifact_type == "Series"

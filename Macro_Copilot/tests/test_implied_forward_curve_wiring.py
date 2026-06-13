"""Caller-wiring tests for implied_forward_curve (§7-C). All offline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.implied_forward_curve import (
    CONFIG_PATH as IMPLIED_FORWARD_CURVE_CONFIG_PATH,
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
            "forward_horizon": "1Y", "n_anchors": 6, "n_observations": 250,
            "forward_curve": [{"anchor_tenor": "1Y", "forward_label": "1Y1Y",
                               "forward_rate_pct": 3.6}],
        },
        "methodology_disclosures": ["d"],
        "forward_strip": {"_dropped": "the typed Panel"},
    }


def test_config_identity():
    cfg = load_tool_config(IMPLIED_FORWARD_CURVE_CONFIG_PATH)
    assert cfg.tool.name == "calculate_implied_forward_curve_tool"
    assert cfg.tool.domain == "ois"


class TestMcpWrapper:
    def test_passes_config_and_drops_panel(self):
        from rates_agent.ois import mcp_server as mcp
        eng = MagicMock()
        with patch.object(mcp, "_get_engine", return_value=eng), patch.object(
            mcp, "calculate_implied_forward_curve", return_value=_well_formed(),
        ) as compute:
            out = mcp.calculate_implied_forward_curve_tool(curve_family="USD_SOFR_OIS")
        _, kw = compute.call_args
        assert kw["engine"] is eng
        assert kw["config"].tool.name == "calculate_implied_forward_curve_tool"
        parsed = json.loads(out)
        assert parsed["current_metrics"]["forward_horizon"] == "1Y"
        assert "forward_strip" not in parsed  # the typed Panel is dropped

    def test_default_anchors_when_omitted(self):
        from rates_agent.ois import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_implied_forward_curve",
                    return_value=_well_formed(),
                ) as compute:
            mcp.calculate_implied_forward_curve_tool(curve_family="X")
        _, kw = compute.call_args
        assert kw["params"].anchor_tenors == ["1Y", "2Y", "3Y", "5Y", "7Y", "10Y"]
        assert kw["params"].forward_horizon is None  # sentinel → YAML default

    def test_explicit_horizon_and_anchors(self):
        from rates_agent.ois import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_implied_forward_curve",
                    return_value=_well_formed(),
                ) as compute:
            mcp.calculate_implied_forward_curve_tool(
                curve_family="X", forward_horizon="2Y",
                anchor_tenors=["2Y", "5Y", "10Y"],
            )
        _, kw = compute.call_args
        assert kw["params"].forward_horizon == "2Y"
        assert kw["params"].anchor_tenors == ["2Y", "5Y", "10Y"]

    def test_compute_exception_envelope(self):
        from rates_agent.ois import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_implied_forward_curve",
                    side_effect=RuntimeError("boom"),
                ):
            out = mcp.calculate_implied_forward_curve_tool(curve_family="X")
        parsed = json.loads(out)
        assert "error" in parsed and "Calculation failed" in parsed["error"]


def test_registered_panel_bridge():
    from rates_agent.workflows import _PRIMITIVE_SPECS
    from rates_agent.ois.tools.implied_forward_curve import (
        ImpliedForwardCurveInput, ImpliedForwardCurveOutput,
    )
    spec = _PRIMITIVE_SPECS["calculate_implied_forward_curve_tool"]
    assert spec.input_class is ImpliedForwardCurveInput
    assert spec.output_class is ImpliedForwardCurveOutput
    assert spec.output_field_units == {"forward_strip": "percent"}
    assert spec.output_artifact_type == "Panel"

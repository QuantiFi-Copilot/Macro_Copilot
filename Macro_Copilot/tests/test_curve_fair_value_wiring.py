"""Caller-wiring tests for curve_fair_value (Bucket-2). All offline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.curve_fair_value import (
    CONFIG_PATH as CURVE_FAIR_VALUE_CONFIG_PATH,
)
from shared.config import clear_tool_config_cache, load_tool_config


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_output() -> dict:
    return {
        "current_metrics": {
            "as_of_date": "2026-05-22", "curve_family": "UST",
            "tenors_used": ["2Y", "10Y", "30Y", "5Y"], "n_components": 3,
            "per_tenor": [], "cheapest_tenor": "7Y",
            "cheapest_residual_bps": 0.8, "richest_tenor": "3Y",
            "richest_residual_bps": -0.5, "explained_variance_ratio": [0.9],
            "near_degenerate": False, "feature_columns": [], "n_complete_rows": 700,
            "fit_scope": "full_sample", "methodology_label": "x",
        },
        "time_series_by_tenor": [{"date": "2026-05-22", "residual_bps": 0.8}],
        "time_series_residual": {
            "series_name": "UST_10Y_pca_residual", "units": "bps",
            "description": "x", "rows": [],
        },
        "methodology_disclosures": ["d"],
    }


class TestConfig:
    def test_config_identity(self):
        cfg = load_tool_config(CURVE_FAIR_VALUE_CONFIG_PATH)
        assert cfg.tool.name == "calculate_curve_fair_value_tool"


class TestMcpWrapper:
    def test_passes_config_and_engine(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        eng = MagicMock()
        with patch.object(mcp, "_get_engine", return_value=eng), patch.object(
            mcp, "calculate_curve_fair_value",
            return_value=_well_formed_output(),
        ) as compute:
            out = mcp.calculate_curve_fair_value_tool(curve_family="UST")
        _, kw = compute.call_args
        assert kw["engine"] is eng
        assert kw["config"].tool.name == "calculate_curve_fair_value_tool"
        parsed = json.loads(out)
        assert parsed["current_metrics"]["cheapest_tenor"] == "7Y"
        assert "time_series_by_tenor" not in parsed

    def test_default_tenors_applied_when_omitted(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_curve_fair_value",
                    return_value=_well_formed_output(),
                ) as compute:
            mcp.calculate_curve_fair_value_tool(curve_family="UST")
        _, kw = compute.call_args
        # The schema default 6-point set applies.
        assert kw["params"].tenors == ["2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]

    def test_explicit_tenors_pass_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_curve_fair_value",
                    return_value=_well_formed_output(),
                ) as compute:
            mcp.calculate_curve_fair_value_tool(
                curve_family="UST", tenors=["2Y", "5Y", "10Y", "30Y"],
                focus_tenor="5Y",
            )
        _, kw = compute.call_args
        assert kw["params"].tenors == ["2Y", "5Y", "10Y", "30Y"]
        assert kw["params"].focus_tenor == "5Y"

    def test_empty_field_name_sentinel(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_curve_fair_value",
                    return_value=_well_formed_output(),
                ) as compute:
            mcp.calculate_curve_fair_value_tool(curve_family="UST", field_name="")
        _, kw = compute.call_args
        assert kw["params"].field_name is None

    def test_invalid_params_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        out = mcp.calculate_curve_fair_value_tool(
            curve_family="UST", tenors=["2Y", "5Y"],  # < 4 → ValidationError
        )
        parsed = json.loads(out)
        assert "error" in parsed and "Invalid parameters" in parsed["error"]

    def test_compute_exception_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_curve_fair_value",
                    side_effect=RuntimeError("boom"),
                ):
            out = mcp.calculate_curve_fair_value_tool(curve_family="UST")
        parsed = json.loads(out)
        assert "error" in parsed and "Calculation failed" in parsed["error"]


class TestRegistration:
    def test_registered_bps_bridge(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS
        from rates_agent.sovereign_bonds.tools.curve_fair_value import (
            CurveFairValueInput, CurveFairValueOutput,
        )
        spec = _PRIMITIVE_SPECS["calculate_curve_fair_value_tool"]
        assert spec.input_class is CurveFairValueInput
        assert spec.output_class is CurveFairValueOutput
        assert spec.output_field_units == {"time_series_residual": "bps"}
        assert spec.output_artifact_type == "Series"

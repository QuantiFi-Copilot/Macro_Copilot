"""Caller-wiring tests for pca_neutral_butterfly_weights (§7-C). All offline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.pca_neutral_butterfly_weights import (
    CONFIG_PATH as PCA_NEUTRAL_BUTTERFLY_WEIGHTS_CONFIG_PATH,
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
            "as_of_date": "2026-05-22", "curve_family": "UST",
            "short_tenor": "2Y", "belly_tenor": "5Y", "long_tenor": "10Y",
            "belly_weight": 2.0, "short_weight": -1.05, "long_weight": -1.1,
            "n_pcs_neutralized": 2, "residual_pc_exposures": [],
            "current_fly_bps": 23.3, "fit_tenors": ["2Y", "5Y", "10Y"],
            "n_complete_rows": 799, "near_degenerate": False,
        },
        "time_series": [{"date": "2026-05-22", "neutral_fly_bps": 23.3}],
        "time_series_neutral_fly": {
            "series_name": "UST_2Y_5Y_10Y_pca_neutral_fly", "units": "bps",
            "description": "x", "rows": [],
        },
        "methodology_disclosures": ["d"],
    }


def test_config_identity():
    cfg = load_tool_config(PCA_NEUTRAL_BUTTERFLY_WEIGHTS_CONFIG_PATH)
    assert cfg.tool.name == "calculate_pca_neutral_butterfly_weights_tool"
    assert cfg.tool.domain == "sovereign_bonds"


class TestMcpWrapper:
    def test_passes_config_and_engine(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        eng = MagicMock()
        with patch.object(mcp, "_get_engine", return_value=eng), patch.object(
            mcp, "calculate_pca_neutral_butterfly_weights",
            return_value=_well_formed(),
        ) as compute:
            out = mcp.calculate_pca_neutral_butterfly_weights_tool(curve_family="UST")
        _, kw = compute.call_args
        assert kw["engine"] is eng
        assert kw["config"].tool.name == "calculate_pca_neutral_butterfly_weights_tool"
        parsed = json.loads(out)
        assert parsed["current_metrics"]["short_weight"] == -1.05
        assert "time_series" not in parsed

    def test_default_fit_tenors_omitted(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_pca_neutral_butterfly_weights",
                    return_value=_well_formed(),
                ) as compute:
            mcp.calculate_pca_neutral_butterfly_weights_tool(
                curve_family="UST", field_name="")
        _, kw = compute.call_args
        assert kw["params"].fit_tenors == ["2Y", "3Y", "5Y", "7Y", "10Y", "30Y"]
        assert kw["params"].field_name is None

    def test_invalid_params_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        out = mcp.calculate_pca_neutral_butterfly_weights_tool(
            curve_family="UST", short_tenor="5Y", belly_tenor="5Y", long_tenor="10Y")
        parsed = json.loads(out)
        assert "error" in parsed and "Invalid parameters" in parsed["error"]

    def test_compute_exception_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_pca_neutral_butterfly_weights",
                    side_effect=RuntimeError("boom"),
                ):
            out = mcp.calculate_pca_neutral_butterfly_weights_tool(curve_family="UST")
        parsed = json.loads(out)
        assert "error" in parsed and "Calculation failed" in parsed["error"]


def test_registered_bps_bridge():
    from rates_agent.workflows import _PRIMITIVE_SPECS
    from rates_agent.sovereign_bonds.tools.pca_neutral_butterfly_weights import (
        PcaNeutralButterflyWeightsInput, PcaNeutralButterflyWeightsOutput,
    )
    spec = _PRIMITIVE_SPECS["calculate_pca_neutral_butterfly_weights_tool"]
    assert spec.input_class is PcaNeutralButterflyWeightsInput
    assert spec.output_class is PcaNeutralButterflyWeightsOutput
    assert spec.output_field_units == {"time_series_neutral_fly": "bps"}
    assert spec.output_artifact_type == "Series"

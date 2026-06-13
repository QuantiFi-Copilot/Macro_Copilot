"""Caller-wiring tests for ois_policy_path_regime (Bucket-2). All offline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.ois.tools.ois_policy_path_regime import (
    CONFIG_PATH as OIS_POLICY_PATH_REGIME_CONFIG_PATH,
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
            "as_of_date": "2026-05-22", "curve_family": "SOFR_FUT",
            "current_regime_index": 0, "current_regime_name": "easing_priced",
            "regime_persistence": 0.97, "n_states": 3, "loglik": -100.0,
            "n_iter": 14, "converged": True, "fit_scope": "full_sample",
            "feature_columns": ["front_level", "realized_vol",
                                "strip_curvature", "strip_slope"],
            "n_core_rows": 880, "per_regime": [], "methodology_label": "x",
        },
        "time_series": [{"date": "2026-05-22", "regime_index": 0,
                         "regime_name": "easing_priced"}],
        "time_series_regime": {
            "series_name": "SOFR_FUT_policy_path_regime_k3", "units": "factor_level",
            "description": "x", "rows": [],
        },
        "methodology_disclosures": ["d"],
    }


def test_config_identity():
    cfg = load_tool_config(OIS_POLICY_PATH_REGIME_CONFIG_PATH)
    assert cfg.tool.name == "calculate_ois_policy_path_regime_tool"
    assert cfg.tool.domain == "ois"


class TestMcpWrapper:
    def test_passes_config_and_engine(self):
        from rates_agent.ois import mcp_server as mcp
        eng = MagicMock()
        with patch.object(mcp, "_get_engine", return_value=eng), patch.object(
            mcp, "calculate_ois_policy_path_regime", return_value=_well_formed(),
        ) as compute:
            out = mcp.calculate_ois_policy_path_regime_tool(curve_family="SOFR_FUT")
        _, kw = compute.call_args
        assert kw["engine"] is eng
        assert kw["config"].tool.name == "calculate_ois_policy_path_regime_tool"
        parsed = json.loads(out)
        assert parsed["current_metrics"]["current_regime_name"] == "easing_priced"
        assert "time_series" not in parsed

    def test_invalid_curve_family_envelope(self):
        from rates_agent.ois import mcp_server as mcp
        out = mcp.calculate_ois_policy_path_regime_tool(curve_family="UST")
        parsed = json.loads(out)
        assert "error" in parsed and "Invalid parameters" in parsed["error"]

    def test_compute_exception_envelope(self):
        from rates_agent.ois import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_ois_policy_path_regime",
                    side_effect=RuntimeError("boom"),
                ):
            out = mcp.calculate_ois_policy_path_regime_tool(curve_family="SOFR_FUT")
        parsed = json.loads(out)
        assert "error" in parsed and "Calculation failed" in parsed["error"]


def test_registered_factor_level_bridge():
    from rates_agent.workflows import _PRIMITIVE_SPECS
    from rates_agent.ois.tools.ois_policy_path_regime import (
        OISPolicyPathRegimeInput, OISPolicyPathRegimeOutput,
    )
    spec = _PRIMITIVE_SPECS["calculate_ois_policy_path_regime_tool"]
    assert spec.input_class is OISPolicyPathRegimeInput
    assert spec.output_class is OISPolicyPathRegimeOutput
    assert spec.output_field_units == {"time_series_regime": "factor_level"}
    assert spec.output_artifact_type == "Series"

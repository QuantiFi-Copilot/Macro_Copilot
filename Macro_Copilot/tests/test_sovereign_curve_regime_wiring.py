"""Caller-wiring tests for sovereign_curve_regime (the first Bucket-2
primitive).

Pins the load-bearing wiring (all offline — engine + compute patched):

  - the MCP wrapper loads the bundled config and passes ``config=`` to
    the compute (PR7 / call-site config visibility);
  - the empty-string field_name sentinel maps to None (→ YAML default);
  - Pydantic + compute errors become a controlled JSON envelope (P6);
  - the PrimitiveSpec is registered with the FACTOR_LEVEL bridge
    declaration and the right schema classes.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.sovereign_curve_regime import (
    CONFIG_PATH as SOV_CURVE_REGIME_CONFIG_PATH,
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
            "as_of_date": "2026-05-22",
            "curve_family": "UST",
            "current_regime_index": 1,
            "current_regime_name": "high_volatility",
            "regime_persistence": 0.96,
            "n_states": 2,
            "loglik": -1234.5,
            "n_iter": 21,
            "converged": True,
            "fit_scope": "full_sample",
            "feature_columns": ["curvature", "level", "realized_vol", "slope"],
            "n_core_rows": 779,
            "per_regime": [],
            "methodology_label": "x",
        },
        "time_series": [{"date": "2026-05-22", "regime_index": 1,
                         "regime_name": "high_volatility"}],
        "time_series_regime": {
            "series_name": "UST_curve_regime_k2", "units": "factor_level",
            "description": "x", "rows": [],
        },
        "methodology_disclosures": ["d1"],
    }


class TestConfigIdentity:
    def test_config_is_the_curve_regime_tool(self):
        cfg = load_tool_config(SOV_CURVE_REGIME_CONFIG_PATH)
        assert cfg.tool.name == "calculate_sovereign_curve_regime_tool"


class TestMcpWrapper:
    def test_passes_config_and_engine_to_compute(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        mock_engine = MagicMock()
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module, "calculate_sovereign_curve_regime",
            return_value=_well_formed_output(),
        ) as mock_compute:
            out = mcp_module.calculate_sovereign_curve_regime_tool(
                curve_family="UST",
            )
        _, kwargs = mock_compute.call_args
        assert kwargs["engine"] is mock_engine
        assert kwargs["config"] is not None
        assert kwargs["config"].tool.name == "calculate_sovereign_curve_regime_tool"
        parsed = json.loads(out)
        # The wrapper surfaces current_metrics + disclosures, drops the
        # per-row time series.
        assert parsed["current_metrics"]["current_regime_name"] == "high_volatility"
        assert "time_series" not in parsed

    def test_empty_field_name_maps_to_none_sentinel(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        with patch.object(
            mcp_module, "_get_engine", return_value=MagicMock(),
        ), patch.object(
            mcp_module, "calculate_sovereign_curve_regime",
            return_value=_well_formed_output(),
        ) as mock_compute:
            mcp_module.calculate_sovereign_curve_regime_tool(
                curve_family="UST", field_name="",
            )
        _, kwargs = mock_compute.call_args
        assert kwargs["params"].field_name is None

    def test_explicit_field_name_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        with patch.object(
            mcp_module, "_get_engine", return_value=MagicMock(),
        ), patch.object(
            mcp_module, "calculate_sovereign_curve_regime",
            return_value=_well_formed_output(),
        ) as mock_compute:
            mcp_module.calculate_sovereign_curve_regime_tool(
                curve_family="UST", field_name="YLD_YTM_BID",
            )
        _, kwargs = mock_compute.call_args
        assert kwargs["params"].field_name == "YLD_YTM_BID"

    def test_invalid_params_return_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        out = mcp_module.calculate_sovereign_curve_regime_tool(
            curve_family="UST", short_tenor="5Y", belly_tenor="5Y",
            long_tenor="10Y",  # duplicate tenor → ValidationError
        )
        parsed = json.loads(out)
        assert "error" in parsed
        assert "Invalid parameters" in parsed["error"]

    def test_compute_exception_returns_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        with patch.object(
            mcp_module, "_get_engine", return_value=MagicMock(),
        ), patch.object(
            mcp_module, "calculate_sovereign_curve_regime",
            side_effect=RuntimeError("boom"),
        ):
            out = mcp_module.calculate_sovereign_curve_regime_tool(
                curve_family="UST",
            )
        parsed = json.loads(out)
        assert "error" in parsed
        assert "Calculation failed" in parsed["error"]

    def test_compute_error_envelope_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        with patch.object(
            mcp_module, "_get_engine", return_value=MagicMock(),
        ), patch.object(
            mcp_module, "calculate_sovereign_curve_regime",
            return_value={"error": "no data"},
        ):
            out = mcp_module.calculate_sovereign_curve_regime_tool(
                curve_family="ZZZ",
            )
        assert json.loads(out) == {"error": "no data"}


class TestPrimitiveSpecRegistration:
    def test_registered_with_factor_level_bridge(self):
        from rates_agent.workflows import _PRIMITIVE_SPECS
        from rates_agent.sovereign_bonds.tools.sovereign_curve_regime import (
            SovereignCurveRegimeInput,
            SovereignCurveRegimeOutput,
        )
        spec = _PRIMITIVE_SPECS["calculate_sovereign_curve_regime_tool"]
        assert spec.input_class is SovereignCurveRegimeInput
        assert spec.output_class is SovereignCurveRegimeOutput
        assert spec.output_field_units == {"time_series_regime": "factor_level"}
        assert spec.output_artifact_type == "Series"

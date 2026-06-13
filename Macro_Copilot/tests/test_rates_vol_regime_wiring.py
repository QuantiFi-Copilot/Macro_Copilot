"""Caller-wiring tests for rates_vol_regime (Bucket-2). All offline."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.rates_vol_regime import (
    CONFIG_PATH as RATES_VOL_REGIME_CONFIG_PATH,
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
            "as_of_date": "2026-05-22", "curve_family": "UST", "tenor": "10Y",
            "current_conditional_vol_daily_bps": 5.1,
            "current_conditional_vol_annualized_bps": 81.0,
            "vol_percentile": 88.0, "regime_label": "elevated",
            "persistence": 0.95, "omega": 0.0002, "alpha": 0.1, "beta": 0.85,
            "mu": 0.0, "loglik": -123.0, "near_integrated": False,
            "is_vol_mean_reverting": True, "fit_scope": "full_sample",
            "n_core_rows": 1200, "methodology_label": "x",
        },
        "time_series": [{"date": "2026-05-22", "conditional_vol_bps": 5.1}],
        "time_series_conditional_vol": {
            "series_name": "UST_10Y_garch_conditional_vol", "units": "bps",
            "description": "x", "rows": [],
        },
        "methodology_disclosures": ["d"],
    }


def test_config_identity():
    cfg = load_tool_config(RATES_VOL_REGIME_CONFIG_PATH)
    assert cfg.tool.name == "calculate_rates_vol_regime_tool"


class TestMcpWrapper:
    def test_passes_config_and_engine(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        eng = MagicMock()
        with patch.object(mcp, "_get_engine", return_value=eng), patch.object(
            mcp, "calculate_rates_vol_regime", return_value=_well_formed(),
        ) as compute:
            out = mcp.calculate_rates_vol_regime_tool(curve_family="UST")
        _, kw = compute.call_args
        assert kw["engine"] is eng
        assert kw["config"].tool.name == "calculate_rates_vol_regime_tool"
        parsed = json.loads(out)
        assert parsed["current_metrics"]["regime_label"] == "elevated"
        assert "time_series" not in parsed

    def test_empty_field_name_sentinel(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_rates_vol_regime", return_value=_well_formed(),
                ) as compute:
            mcp.calculate_rates_vol_regime_tool(curve_family="UST", field_name="")
        _, kw = compute.call_args
        assert kw["params"].field_name is None

    def test_invalid_params_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        out = mcp.calculate_rates_vol_regime_tool(
            curve_family="UST", lookback_days=10,  # < ge=200 → ValidationError
        )
        parsed = json.loads(out)
        assert "error" in parsed and "Invalid parameters" in parsed["error"]

    def test_compute_exception_envelope(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp
        with patch.object(mcp, "_get_engine", return_value=MagicMock()), \
                patch.object(
                    mcp, "calculate_rates_vol_regime",
                    side_effect=RuntimeError("boom"),
                ):
            out = mcp.calculate_rates_vol_regime_tool(curve_family="UST")
        parsed = json.loads(out)
        assert "error" in parsed and "Calculation failed" in parsed["error"]


def test_registered_bps_bridge():
    from rates_agent.workflows import _PRIMITIVE_SPECS
    from rates_agent.sovereign_bonds.tools.rates_vol_regime import (
        RatesVolRegimeInput, RatesVolRegimeOutput,
    )
    spec = _PRIMITIVE_SPECS["calculate_rates_vol_regime_tool"]
    assert spec.input_class is RatesVolRegimeInput
    assert spec.output_class is RatesVolRegimeOutput
    assert spec.output_field_units == {"time_series_conditional_vol": "bps"}
    assert spec.output_artifact_type == "Series"

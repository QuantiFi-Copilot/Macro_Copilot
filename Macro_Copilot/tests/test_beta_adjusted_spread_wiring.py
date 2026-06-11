"""
test_beta_adjusted_spread_wiring.py — Caller-wiring smoke tests.

Two callers in this sprint:
  1. rates_agent/sovereign_bonds/mcp_server.py::calculate_beta_adjusted_spread_tool
  2. api/routes/rates/detail.py::beta_adjusted_spread_detail
     (/detail/beta-adjusted-spread)

Per the v6 plan Delta H/N: beta_adjusted_spread takes only flat
scalar inputs (no nested SeriesSpec), so it ships with BOTH MCP and
FastAPI GET this sprint — same transport shape as zscore_custom.

Key load-bearing properties pinned:
  - Each surface passes config explicitly (no auto-load fallback).
  - The MCP wrapper's signature is flat scalar (per the v6 transport
    rule for non-nested-input tools).
  - The FastAPI route's `field_name` Query default is None (not a
    hardcoded string).
  - The MCP wrapper withholds time-series payloads from the LLM.
  - The cross-layer small-window error maps to HTTP 422 at the
    FastAPI route (via the user_input_phrases shape).
  - The CONFIG_PATH public symbol is identical via package init and
    compute module imports.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
    CONFIG_PATH as BETA_ADJUSTED_SPREAD_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_bas_output() -> dict:
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "target_curve_family": "IT_BTP",
            "target_tenor": "10Y",
            "regressor_curve_family": "DE_BUND",
            "regressor_tenor": "10Y",
            "spread_label": "IT_BTP-DE_BUND 10Y/10Y (beta-adjusted, 252d)",
            "current_beta": 1.05,
            "current_alpha_pct": 0.10,
            "current_residual_bps": 5.20,
            "current_residual_z_score": 0.85,
            "current_r_squared": 0.92,
            "current_condition_flag": 0,
            "regression_window_days_used": 252,
            "regression_min_periods_used": 30,
            "z_score_window_days_used": 252,
            "add_constant_used": True,
            "observation_count": 252,
        },
        "time_series_beta": {
            "series_name": "IT_BTP_10Y_on_DE_BUND_10Y_beta_252d",
            "units": "ratio",
            "description": "test",
            "rows": [{"date": "2026-04-30", "value": 1.05}],
        },
        "time_series_residual": {
            "series_name": "IT_BTP_10Y_minus_DE_BUND_10Y_beta_adjusted_residual_bps_252d",
            "units": "bps",
            "description": "test",
            "rows": [{"date": "2026-04-30", "value": 5.20}],
        },
        "time_series_residual_z_score": {
            "series_name": "IT_BTP_10Y_minus_DE_BUND_10Y_beta_adjusted_residual_zscore_252d",
            "units": "z_score",
            "description": "test",
            "rows": [{"date": "2026-04-30", "value": 0.85}],
        },
    }


def _assert_bas_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "beta_adjusted_spread_tool"
    assert "regression_min_periods" in cfg.conventions
    assert "regression_window_days" not in cfg.conventions  # central knob


# ===========================================================================
# api/routes/rates/detail.py — /detail/beta-adjusted-spread endpoint
# ===========================================================================

class TestDetailBasEndpointWiring:
    def test_route_passes_config(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_beta_adjusted_spread",
            return_value=_well_formed_bas_output(),
        ) as mock_bas:
            detail_module.beta_adjusted_spread_detail(
                engine=mock_engine,
                target_curve_family="IT_BTP", target_tenor="10Y",
                regressor_curve_family="DE_BUND", regressor_tenor="10Y",
                regression_window_days=252,
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_bas.call_count == 1
        _assert_bas_config_passed(mock_bas.call_args)

    def test_field_name_default_is_none(self):
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.beta_adjusted_spread_detail)
        query_obj = sig.parameters["field_name"].default
        assert query_obj.default is None

    def test_regression_window_days_is_required(self):
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.beta_adjusted_spread_detail)
        query_obj = sig.parameters["regression_window_days"].default
        assert query_obj.default is Ellipsis or query_obj.default is ... or \
            getattr(query_obj, "is_required", lambda: True)(), (
            f"regression_window_days must be REQUIRED at the Query "
            f"layer; got default={query_obj.default!r}"
        )

    def test_omitted_field_name_flows_none(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_beta_adjusted_spread",
            return_value=_well_formed_bas_output(),
        ) as mock_bas:
            detail_module.beta_adjusted_spread_detail(
                engine=mock_engine,
                target_curve_family="IT_BTP", target_tenor="10Y",
                regressor_curve_family="DE_BUND", regressor_tenor="10Y",
                regression_window_days=252,
                lookback_days=365,
                field_name=None,
            )
        params = mock_bas.call_args.kwargs["params"]
        assert params.field_name is None

    def test_small_window_route_returns_422(self):
        """The compute-layer controlled error envelope from the
        small-window guard must surface as HTTP 422 — the
        user_input_phrases shape match is shared with zscore_custom
        and rolling_regression."""
        from fastapi import HTTPException
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        controlled_error = {
            "error": (
                "regression_window_days=20 is smaller than the YAML's "
                "regression_min_periods=30.  Either request a larger "
                "regression_window_days (>= 30), or edit "
                "regression_min_periods in beta_adjusted_spread/config.yaml."
            )
        }
        with patch.object(
            detail_module,
            "calculate_beta_adjusted_spread",
            return_value=controlled_error,
        ):
            with pytest.raises(HTTPException) as exc_info:
                detail_module.beta_adjusted_spread_detail(
                    engine=mock_engine,
                    target_curve_family="IT_BTP", target_tenor="10Y",
                    regressor_curve_family="DE_BUND", regressor_tenor="10Y",
                    regression_window_days=20,
                    lookback_days=365,
                    field_name=None,
                )
        assert exc_info.value.status_code == 422
        assert "regression_min_periods" in str(exc_info.value.detail)


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — calculate_beta_adjusted_spread_tool
# ===========================================================================

class TestMcpBasWrapper:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_beta_adjusted_spread",
            return_value=_well_formed_bas_output(),
        ) as mock_bas:
            output_json = mcp_module.calculate_beta_adjusted_spread_tool(
                target_curve_family="IT_BTP", target_tenor="10Y",
                regressor_curve_family="DE_BUND", regressor_tenor="10Y",
                regression_window_days=252,
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_bas.call_count == 1
        _assert_bas_config_passed(mock_bas.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_signature_is_flat_scalar(self):
        """Per the v6 transport rule, beta_adjusted_spread is a
        flat-input tool; its MCP signature must be flat scalar (no
        nested Pydantic models like SeriesSpec)."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import typing

        fn = mcp_module.calculate_beta_adjusted_spread_tool
        underlying = getattr(fn, "fn", fn)
        underlying = getattr(underlying, "__wrapped__", underlying)
        hints = typing.get_type_hints(underlying)
        hints.pop("return", None)

        scalar_types = {str, int, float, bool}
        for name, ann in hints.items():
            assert ann in scalar_types, (
                f"parameter {name!r} has non-scalar annotation {ann!r}; "
                "beta_adjusted_spread must keep a flat scalar MCP "
                "signature"
            )

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.calculate_beta_adjusted_spread_tool)
        default = sig.parameters["field_name"].default
        assert default == ""

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_beta_adjusted_spread",
            return_value=_well_formed_bas_output(),
        ) as mock_bas:
            mcp_module.calculate_beta_adjusted_spread_tool(
                target_curve_family="IT_BTP", target_tenor="10Y",
                regressor_curve_family="DE_BUND", regressor_tenor="10Y",
                regression_window_days=252,
                # field_name omitted → defaults to ""
            )
        params = mock_bas.call_args.kwargs["params"]
        assert params.field_name is None

    def test_explicit_field_name_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_beta_adjusted_spread",
            return_value=_well_formed_bas_output(),
        ) as mock_bas:
            mcp_module.calculate_beta_adjusted_spread_tool(
                target_curve_family="IT_BTP", target_tenor="10Y",
                regressor_curve_family="DE_BUND", regressor_tenor="10Y",
                regression_window_days=252,
                field_name="YLD_BID",
            )
        params = mock_bas.call_args.kwargs["params"]
        assert params.field_name == "YLD_BID"

    def test_time_series_withheld_from_llm_response(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_beta_adjusted_spread",
            return_value=_well_formed_bas_output(),
        ):
            output_json = mcp_module.calculate_beta_adjusted_spread_tool(
                target_curve_family="IT_BTP", target_tenor="10Y",
                regressor_curve_family="DE_BUND", regressor_tenor="10Y",
                regression_window_days=252,
            )
        parsed = json.loads(output_json)
        for k in (
            "time_series_beta",
            "time_series_residual",
            "time_series_residual_z_score",
        ):
            assert k not in parsed, f"{k!r} leaked to LLM response"
        assert "current_metrics" in parsed


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.beta_adjusted_spread import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.beta_adjusted_spread.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_beta_adjusted_spread_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(BETA_ADJUSTED_SPREAD_CONFIG_PATH)
        assert cfg.tool.name == "beta_adjusted_spread_tool"
        assert cfg.tool.category == "desk_invariant_primitive"

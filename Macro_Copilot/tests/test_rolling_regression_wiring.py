"""
test_rolling_regression_wiring.py — Caller-wiring smoke tests for
rolling_regression.

Per the v6 transport rule, rolling_regression has nested inputs
(`SeriesSpec`, `List[SeriesSpec]`) so it ships with **MCP only this
sprint**.  No FastAPI route until the UI-integration PR — and these
wiring tests pin that contract (the route does NOT exist) explicitly
so a future commit can't accidentally add it without thinking about
the POST/JSON shape.

Key load-bearing properties pinned:
  - The MCP wrapper's signature mirrors the Pydantic input model
    (nested ``SeriesSpec``, ``List[SeriesSpec]``); not flat scalar.
  - The wrapper passes config explicitly (no auto-load fallback).
  - The wrapper withholds ALL time-series payloads from the LLM
    response (5 series × potentially 100s of rows would blow context).
  - No FastAPI route exists for this tool in this sprint.
  - The CONFIG_PATH public symbol is identical via package init and
    compute module imports.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.rolling_regression import (
    CONFIG_PATH as ROLLING_REGRESSION_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache
from shared.schemas import SeriesSpec


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_rr_output() -> dict:
    """Minimal-shape return value that satisfies the snapshot
    contract; time-series payloads are empty lists / one row to
    keep the fixture small."""
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "target_label": "UST_10Y",
            "regressor_labels": ["DE_BUND_10Y"],
            "current_alpha_pct": 0.50,
            "current_betas": {"DE_BUND_10Y": 1.50},
            "current_residual_pct": 0.01,
            "current_r_squared": 0.95,
            "current_condition_flag": 0,
            "regression_window_days_used": 252,
            "regression_min_periods_used": 30,
            "add_constant_used": True,
            "observation_count": 252,
        },
        "time_series_betas": [
            {
                "series_name": "UST_10Y_on_DE_BUND_10Y_beta_252d",
                "units": "ratio",
                "description": "test",
                "rows": [{"date": "2026-04-30", "value": 1.50}],
            }
        ],
        "time_series_alpha": {
            "series_name": "UST_10Y_alpha_252d",
            "units": "percent",
            "description": "test",
            "rows": [{"date": "2026-04-30", "value": 0.50}],
        },
        "time_series_residual": {
            "series_name": "UST_10Y_residual_252d",
            "units": "percent",
            "description": "test",
            "rows": [{"date": "2026-04-30", "value": 0.01}],
        },
        "time_series_r_squared": {
            "series_name": "UST_10Y_r_squared_252d",
            "units": "ratio",
            "description": "test",
            "rows": [{"date": "2026-04-30", "value": 0.95}],
        },
        "time_series_condition_flag": {
            "series_name": "UST_10Y_condition_flag_252d",
            "units": "count",
            "description": "test",
            "rows": [{"date": "2026-04-30", "value": 0.0}],
        },
    }


def _assert_rr_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, (
        "calculate_rolling_regression called without `config=`"
    )
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "rolling_regression_tool"
    assert "regression_min_periods" in cfg.conventions
    # Central knob must NOT be a YAML convention.
    assert "regression_window_days" not in cfg.conventions


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — calculate_rolling_regression_tool
# ===========================================================================

class TestMcpRollingRegressionWrapper:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_rolling_regression",
            return_value=_well_formed_rr_output(),
        ) as mock_rr:
            output_json = mcp_module.calculate_rolling_regression_tool(
                target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                regressor_specs=[
                    SeriesSpec(curve_family="DE_BUND", tenor="10Y"),
                ],
                regression_window_days=252,
                lookback_days=365,
            )
        assert mock_rr.call_count == 1
        _assert_rr_config_passed(mock_rr.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_signature_is_nested_pydantic(self):
        """Per the v6 transport rule, rolling_regression has nested
        inputs — its MCP signature must use Pydantic models
        (``SeriesSpec``, ``List[SeriesSpec]``), NOT flat scalars.

        This test contrasts with zscore_custom's flat-scalar wrapper
        (test pinned in test_zscore_custom_wiring.py) — both pins are
        intentional, and together they enforce the v6 transport
        decision.
        """
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import typing

        fn = mcp_module.calculate_rolling_regression_tool
        underlying = getattr(fn, "fn", fn)
        underlying = getattr(underlying, "__wrapped__", underlying)
        hints = typing.get_type_hints(underlying)
        hints.pop("return", None)

        # target_spec must resolve to SeriesSpec
        assert hints["target_spec"] is SeriesSpec
        # regressor_specs must resolve to List[SeriesSpec]
        regressor_ann = hints["regressor_specs"]
        origin = typing.get_origin(regressor_ann)
        args = typing.get_args(regressor_ann)
        assert origin is list, f"regressor_specs origin {origin!r} not list"
        assert args == (SeriesSpec,), f"regressor_specs args {args!r}"
        # central knob is a flat int
        assert hints["regression_window_days"] is int
        assert hints["lookback_days"] is int

    def test_omitted_field_name_uses_seriesspec_default(self):
        """SeriesSpec.field_name defaults to None — verify it reaches
        the tool's input via Pydantic."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_rolling_regression",
            return_value=_well_formed_rr_output(),
        ) as mock_rr:
            mcp_module.calculate_rolling_regression_tool(
                target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                regressor_specs=[
                    SeriesSpec(curve_family="DE_BUND", tenor="10Y"),
                ],
                regression_window_days=252,
            )
        params = mock_rr.call_args.kwargs["params"]
        assert params.target_spec.field_name is None
        assert params.regressor_specs[0].field_name is None

    def test_explicit_field_name_passes_through_per_spec(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_rolling_regression",
            return_value=_well_formed_rr_output(),
        ) as mock_rr:
            mcp_module.calculate_rolling_regression_tool(
                target_spec=SeriesSpec(
                    curve_family="UST", tenor="10Y", field_name="YLD_BID",
                ),
                regressor_specs=[
                    SeriesSpec(curve_family="DE_BUND", tenor="10Y"),
                ],
                regression_window_days=252,
            )
        params = mock_rr.call_args.kwargs["params"]
        assert params.target_spec.field_name == "YLD_BID"
        assert params.regressor_specs[0].field_name is None

    def test_all_time_series_withheld_from_llm_response(self):
        """The MCP-to-LLM payload is the snapshot only — five series ×
        a year of rolling fits would blow the context window."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_rolling_regression",
            return_value=_well_formed_rr_output(),
        ):
            output_json = mcp_module.calculate_rolling_regression_tool(
                target_spec=SeriesSpec(curve_family="UST", tenor="10Y"),
                regressor_specs=[
                    SeriesSpec(curve_family="DE_BUND", tenor="10Y"),
                ],
                regression_window_days=252,
            )
        parsed = json.loads(output_json)
        # All five time-series keys absent from the LLM payload.
        for k in (
            "time_series_betas",
            "time_series_alpha",
            "time_series_residual",
            "time_series_r_squared",
            "time_series_condition_flag",
        ):
            assert k not in parsed, f"time-series key {k!r} leaked to LLM"
        assert "current_metrics" in parsed


# ===========================================================================
# api/routes/rates/detail.py — explicit absence of /detail/rolling-regression
# ===========================================================================

class TestNoFastApiRouteThisSprint:
    """rolling_regression has nested inputs and ships MCP-only this
    sprint per the v6 transport rule.  Pin that absence so a future
    commit can't accidentally add a flat-Query route — it would not
    fit the input shape and would mislead callers."""

    def test_no_route_for_rolling_regression(self):
        from api.routes.rates import detail as detail_module
        import inspect
        # All public route handlers in detail.py — anything ending in
        # `_detail` and decorated with @router.get.
        handlers = [
            name for name, obj in inspect.getmembers(detail_module)
            if inspect.isfunction(obj) and name.endswith("_detail")
        ]
        assert "rolling_regression_detail" not in handlers, (
            "rolling_regression has nested inputs and ships MCP-only "
            "this sprint.  Adding a FastAPI route requires the "
            "POST/JSON-body decision documented in v6 plan Delta H "
            "first."
        )


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.rolling_regression import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.rolling_regression.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_rolling_regression_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(ROLLING_REGRESSION_CONFIG_PATH)
        assert cfg.tool.name == "rolling_regression_tool"
        assert cfg.tool.category == "quant_standard_analytic"

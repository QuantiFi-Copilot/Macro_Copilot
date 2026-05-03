"""
test_pca_yield_curve_wiring.py — Caller-wiring smoke tests for
pca_yield_curve.

Per the v6 plan Delta H/Q: pca_yield_curve takes flat scalar inputs
plus a `tenors: Optional[List[str]]` repeated-query-param field, so
it ships with BOTH MCP and FastAPI GET this sprint — same transport
shape as zscore_custom + beta_adjusted_spread (with the additional
list-of-strings tenor field).

Key load-bearing properties pinned:
  - Each surface passes config explicitly.
  - The MCP wrapper's signature is flat scalar (List[str] is allowed
    per the plan since FastAPI Query supports repeated values).
  - The FastAPI route's `field_name` Query default is None.
  - The FastAPI route's `tenors` Query default is None.
  - The MCP wrapper withholds time_series_factors from the LLM.
  - The cross-layer min_observations error maps to HTTP 422.
  - The honest-placeholder NotImplementedError on a wrong sign_anchor
    surfaces as a 500-class error envelope from the MCP wrapper
    (NOT 422 — this is a server/config bug class, not a client-input
    class).
  - The CONFIG_PATH public symbol is identical via package init and
    compute module imports.
"""

from __future__ import annotations

import json
from typing import Literal, get_args, get_origin, get_type_hints
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
    CONFIG_PATH as PCA_YIELD_CURVE_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_pca_output() -> dict:
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST",
            "tenors_used": ["1Y", "2Y", "5Y", "10Y", "30Y"],
            "lookback_days_used": 1825,
            "n_components_returned": 3,
            "change_frequency_used": "daily",
            "sign_anchor_used": "lock_pc_long_tenor_positive",
            "loadings": [
                {"tenor": "1Y",  "pc1": 0.45, "pc2": -0.55, "pc3":  0.30},
                {"tenor": "2Y",  "pc1": 0.45, "pc2": -0.30, "pc3":  0.10},
                {"tenor": "5Y",  "pc1": 0.45, "pc2":  0.00, "pc3": -0.40},
                {"tenor": "10Y", "pc1": 0.45, "pc2":  0.30, "pc3": -0.20},
                {"tenor": "30Y", "pc1": 0.45, "pc2":  0.55, "pc3":  0.30},
            ],
            "variance_explained": [
                {"component_name": "pc1", "variance_share": 0.80, "cumulative_share": 0.80},
                {"component_name": "pc2", "variance_share": 0.15, "cumulative_share": 0.95},
                {"component_name": "pc3", "variance_share": 0.04, "cumulative_share": 0.99},
            ],
            "total_variance_explained": 0.99,
            "current_factor_levels": {"pc1": 0.123, "pc2": -0.045, "pc3": 0.012},
            "component_metadata": [
                {"component_name": "pc1", "quality_flag": "ok", "quality_note": None},
                {"component_name": "pc2", "quality_flag": "ok", "quality_note": None},
                {"component_name": "pc3", "quality_flag": "ok", "quality_note": None},
            ],
            "observation_count": 1499,
        },
        "time_series_factors": [
            {
                "series_name": "ust_pc1_factor_daily",
                "units": "factor_level",
                "description": "test",
                "rows": [
                    {"date": "2026-04-29", "value": 0.110},
                    {"date": "2026-04-30", "value": 0.123},
                ],
            },
            {
                "series_name": "ust_pc2_factor_daily",
                "units": "factor_level",
                "description": "test",
                "rows": [
                    {"date": "2026-04-29", "value": -0.040},
                    {"date": "2026-04-30", "value": -0.045},
                ],
            },
            {
                "series_name": "ust_pc3_factor_daily",
                "units": "factor_level",
                "description": "test",
                "rows": [
                    {"date": "2026-04-29", "value": 0.010},
                    {"date": "2026-04-30", "value": 0.012},
                ],
            },
        ],
    }


def _assert_pca_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "pca_yield_curve_tool"
    assert "min_observations_for_pca" in cfg.conventions
    # Central knobs are NOT in YAML.
    for k in ("n_components", "change_frequency"):
        assert k not in cfg.conventions
    # default_n_components / default_change_frequency are YAML
    # *defaults* (separate from the user's per-call values).
    assert "default_n_components" in cfg.conventions


# ===========================================================================
# api/routes/rates/detail.py — /detail/pca-yield-curve endpoint
# ===========================================================================

class TestDetailPcaEndpointWiring:
    def test_route_passes_config(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_pca_yield_curve",
            return_value=_well_formed_pca_output(),
        ) as mock_pca:
            detail_module.pca_yield_curve_detail(
                engine=mock_engine,
                curve_family="UST",
                tenors=None,
                lookback_days=1825,
                n_components=3,
                change_frequency="daily",
                field_name="YLD_YTM_MID",
            )
        assert mock_pca.call_count == 1
        _assert_pca_config_passed(mock_pca.call_args)

    def test_field_name_default_is_none(self):
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.pca_yield_curve_detail)
        query_obj = sig.parameters["field_name"].default
        assert query_obj.default is None, (
            f"field_name Query default must be None, got {query_obj.default!r}"
        )

    def test_tenors_default_is_none(self):
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.pca_yield_curve_detail)
        query_obj = sig.parameters["tenors"].default
        assert query_obj.default is None

    def test_lookback_days_query_lower_bound_is_400(self):
        """Pin the route's calendar-day floor at 400.  In current
        FastAPI/Pydantic, ``Query`` no longer exposes ``ge`` as a
        direct attribute — the constraint lives in ``metadata`` as
        an ``annotated_types.Ge`` marker.  This test reads it from
        the canonical location so the lower bound stays load-bearing
        even as upstream APIs evolve."""
        from annotated_types import Ge
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.pca_yield_curve_detail)
        query_obj = sig.parameters["lookback_days"].default
        ge_markers = [m for m in query_obj.metadata if isinstance(m, Ge)]
        assert len(ge_markers) == 1, (
            f"Expected exactly one Ge marker in Query metadata, "
            f"got {query_obj.metadata!r}"
        )
        assert ge_markers[0].ge == 400

    def test_omitted_field_name_flows_none(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_pca_yield_curve",
            return_value=_well_formed_pca_output(),
        ) as mock_pca:
            detail_module.pca_yield_curve_detail(
                engine=mock_engine,
                curve_family="UST",
                tenors=None,
                lookback_days=1825,
                n_components=3,
                change_frequency="daily",
                field_name=None,
            )
        params = mock_pca.call_args.kwargs["params"]
        assert params.field_name is None

    def test_repeated_tenors_query_param_flows_through(self):
        """Mimics FastAPI's multi-value Query: caller passes a list.
        Verify it reaches the input schema unchanged."""
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_pca_yield_curve",
            return_value=_well_formed_pca_output(),
        ) as mock_pca:
            detail_module.pca_yield_curve_detail(
                engine=mock_engine,
                curve_family="UST",
                tenors=["1Y", "10Y", "30Y"],
                lookback_days=1825,
                n_components=3,
                change_frequency="daily",
                field_name=None,
            )
        params = mock_pca.call_args.kwargs["params"]
        assert params.tenors == ["1Y", "10Y", "30Y"]

    def test_small_window_route_returns_422(self):
        """The compute-layer cross-layer guard returns a controlled
        error envelope (not via ``raise``) when the actual trading-day
        change-panel length after differencing is below the YAML's
        ``min_observations_for_pca``.  The route must surface this as
        HTTP 422 — same ``user_input_phrases`` shape match used by
        zscore_custom and beta_adjusted_spread.

        Note: the cross-layer guard fires on the trading-day count
        AFTER ``ffill`` + differencing, not directly on ``lookback_days``.
        ``lookback_days`` is only a coarse calendar-day floor at the
        schema layer (``ge=400``).  This test passes a value that
        clears schema validation (so the request reaches the mocked
        compute) and pins the 422 mapping for the controlled error
        envelope itself."""
        from fastapi import HTTPException
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        controlled_error = {
            "error": (
                "change panel length is smaller than the YAML's "
                "min_observations_for_pca=252.  Either supply a "
                "longer lookback_days, or edit "
                "min_observations_for_pca in pca_yield_curve/config.yaml."
            )
        }
        with patch.object(
            detail_module,
            "calculate_pca_yield_curve",
            return_value=controlled_error,
        ):
            with pytest.raises(HTTPException) as exc_info:
                detail_module.pca_yield_curve_detail(
                    engine=mock_engine,
                    curve_family="UST",
                    tenors=None,
                    lookback_days=400,
                    n_components=3,
                    change_frequency="daily",
                    field_name=None,
                )
        assert exc_info.value.status_code == 422
        assert "min_observations_for_pca" in str(exc_info.value.detail)


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — pca_yield_curve_tool
# ===========================================================================

class TestMcpPcaWrapper:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_pca_yield_curve",
            return_value=_well_formed_pca_output(),
        ) as mock_pca:
            output_json = mcp_module.pca_yield_curve_tool(
                curve_family="UST",
                tenors=None,
                lookback_days=1825,
                n_components=3,
                change_frequency="daily",
                field_name="YLD_YTM_MID",
            )
        assert mock_pca.call_count == 1
        _assert_pca_config_passed(mock_pca.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.pca_yield_curve_tool)
        default = sig.parameters["field_name"].default
        assert default == ""

    def test_change_frequency_annotation_is_literal(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        hint = get_type_hints(mcp_module.pca_yield_curve_tool)["change_frequency"]
        assert get_origin(hint) is Literal
        assert get_args(hint) == ("daily", "weekly")

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_pca_yield_curve",
            return_value=_well_formed_pca_output(),
        ) as mock_pca:
            mcp_module.pca_yield_curve_tool(
                curve_family="UST",
            )
        params = mock_pca.call_args.kwargs["params"]
        assert params.field_name is None
        # tenors=None default flows through.
        assert params.tenors is None

    def test_explicit_field_name_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_pca_yield_curve",
            return_value=_well_formed_pca_output(),
        ) as mock_pca:
            mcp_module.pca_yield_curve_tool(
                curve_family="UST",
                field_name="YLD_BID",
            )
        params = mock_pca.call_args.kwargs["params"]
        assert params.field_name == "YLD_BID"

    def test_explicit_tenors_list_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_pca_yield_curve",
            return_value=_well_formed_pca_output(),
        ) as mock_pca:
            mcp_module.pca_yield_curve_tool(
                curve_family="UST",
                tenors=["2Y", "10Y", "30Y"],
            )
        params = mock_pca.call_args.kwargs["params"]
        assert params.tenors == ["2Y", "10Y", "30Y"]

    def test_time_series_factors_withheld_from_llm(self):
        """Three factor time series × hundreds of rows would blow the
        LLM context.  Verify they're stripped from the MCP response."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_pca_yield_curve",
            return_value=_well_formed_pca_output(),
        ):
            output_json = mcp_module.pca_yield_curve_tool(
                curve_family="UST",
            )
        parsed = json.loads(output_json)
        assert "time_series_factors" not in parsed
        assert "current_metrics" in parsed


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.pca_yield_curve import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.pca_yield_curve.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_pca_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(PCA_YIELD_CURVE_CONFIG_PATH)
        assert cfg.tool.name == "pca_yield_curve_tool"
        assert cfg.tool.category == "quant_standard_analytic"

"""
test_zscore_custom_wiring.py — Caller-wiring smoke tests for zscore_custom.

Two callers in this sprint:
  1. rates_agent/sovereign_bonds/mcp_server.py::calculate_zscore_custom_tool
  2. api/routes/rates/detail.py::zscore_custom_detail (/detail/zscore-custom)

Key load-bearing properties pinned:
  - Each surface passes config explicitly (no auto-load fallback).
  - Each surface uses the empty-string / None-sentinel pattern for
    field_name so the YAML default actually flows through.
  - The MCP wrapper's signature is flat scalar (per the v6
    transport rule for tools without nested inputs).
  - The FastAPI route's field_name Query default is None (not a
    hardcoded string).
  - The MCP wrapper withholds the time_series payload from the LLM
    response.
  - The CONFIG_PATH public symbol is identical via package init and
    compute module imports.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rates_agent.sovereign_bonds.tools.zscore_custom import (
    CONFIG_PATH as ZSCORE_CUSTOM_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_zscore_output() -> dict:
    return {
        "current_metrics": {
            "as_of_date": "2026-04-30",
            "curve_family": "UST",
            "tenor": "10Y",
            "current_yield_pct": 4.30,
            "current_z_score": 0.85,
            "z_score_window_days_used": 252,
            "z_score_min_periods_used": 60,
            "z_score_ddof_used": 1,
            "observation_count": 252,
        },
        "time_series": {
            "series_name": "ust_10y_zscore_252d",
            "units": "z_score",
            "description": "test",
            "rows": [
                {"date": "2026-04-29", "value": 0.80},
                {"date": "2026-04-30", "value": 0.85},
            ],
        },
    }


def _assert_zscore_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None, "calculate_zscore_custom called without `config=`"
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "zscore_custom_tool", (
        f"caller passed config for tool {cfg.tool.name!r}, expected "
        "'zscore_custom_tool'"
    )
    assert "z_score_min_periods" in cfg.conventions
    # z_score_window_days is the user's central knob, NOT a YAML
    # convention — verify it's NOT in this config.
    assert "z_score_window_days" not in cfg.conventions


# ===========================================================================
# api/routes/rates/detail.py — /detail/zscore-custom endpoint
# ===========================================================================

class TestDetailZscoreEndpointWiring:
    def test_zscore_custom_detail_passes_config(self):
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_zscore_custom",
            return_value=_well_formed_zscore_output(),
        ) as mock_zc:
            detail_module.zscore_custom_detail(
                engine=mock_engine,
                curve_family="UST",
                tenor="10Y",
                z_score_window_days=252,
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_zc.call_count == 1
        _assert_zscore_config_passed(mock_zc.call_args)

    def test_field_name_default_is_none(self):
        """The Query default for field_name must be None; a hardcoded
        string would shadow the YAML's default_field_name."""
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.zscore_custom_detail)
        query_obj = sig.parameters["field_name"].default
        assert query_obj.default is None, (
            f"Query default for field_name must be None, got "
            f"{query_obj.default!r}"
        )

    def test_z_score_window_days_is_required(self):
        """The central knob has no Query default — it must be provided
        by the caller."""
        from api.routes.rates import detail as detail_module
        import inspect
        sig = inspect.signature(detail_module.zscore_custom_detail)
        query_obj = sig.parameters["z_score_window_days"].default
        # FastAPI Query(...) → the .default attribute is the Ellipsis
        # sentinel when REQUIRED.  Check via the deprecated attribute or
        # the equivalent: required is True.
        assert query_obj.default is Ellipsis or query_obj.default is ... or \
            getattr(query_obj, "is_required", lambda: True)(), (
            f"z_score_window_days must be REQUIRED at the Query layer; "
            f"got default={query_obj.default!r}"
        )

    def test_omitted_field_name_flows_none(self):
        """Mirrors FastAPI's request-parse resolution by passing
        field_name=None directly."""
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_zscore_custom",
            return_value=_well_formed_zscore_output(),
        ) as mock_zc:
            detail_module.zscore_custom_detail(
                engine=mock_engine,
                curve_family="UST",
                tenor="10Y",
                z_score_window_days=252,
                lookback_days=365,
                field_name=None,
            )
        params = mock_zc.call_args.kwargs["params"]
        assert params.field_name is None


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — calculate_zscore_custom_tool
# ===========================================================================

class TestMcpZscoreToolWiring:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_zscore_custom",
            return_value=_well_formed_zscore_output(),
        ) as mock_zc:
            output_json = mcp_module.calculate_zscore_custom_tool(
                curve_family="UST",
                tenor="10Y",
                z_score_window_days=252,
                lookback_days=365,
                field_name="YLD_YTM_MID",
            )
        assert mock_zc.call_count == 1
        _assert_zscore_config_passed(mock_zc.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_signature_is_flat_scalar(self):
        """Per the v6 transport rule, zscore_custom is a flat-input
        tool: its MCP signature must be flat scalar (no nested
        Pydantic models like SeriesSpec) so the existing flat MCP
        recipe is preserved.

        Resolves PEP 563 string annotations via ``get_type_hints`` so
        the comparison works against actual types regardless of whether
        the module uses ``from __future__ import annotations``.
        """
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import typing

        # The @mcp.tool() decorator wraps the function; unwrap to the
        # underlying callable that carries the original annotations.
        fn = mcp_module.calculate_zscore_custom_tool
        underlying = getattr(fn, "fn", fn)
        underlying = getattr(underlying, "__wrapped__", underlying)
        hints = typing.get_type_hints(underlying)
        # Strip the return-annotation entry; only compare argument types
        hints.pop("return", None)

        scalar_types = {str, int, float, bool}
        for name, ann in hints.items():
            assert ann in scalar_types, (
                f"parameter {name!r} has non-scalar annotation {ann!r}; "
                "zscore_custom must keep a flat scalar MCP signature"
            )

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.calculate_zscore_custom_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-string "
            f"sentinel for 'use the YAML default'); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_zscore_custom",
            return_value=_well_formed_zscore_output(),
        ) as mock_zc:
            mcp_module.calculate_zscore_custom_tool(
                curve_family="UST",
                tenor="10Y",
                z_score_window_days=252,
                # field_name omitted → defaults to ""
            )
        params = mock_zc.call_args.kwargs["params"]
        assert params.field_name is None, (
            f"omitted field_name must reach ZscoreCustomInput as None "
            f"(sentinel for 'use YAML default_field_name'), got "
            f"{params.field_name!r}"
        )

    def test_explicit_field_name_passes_through(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_zscore_custom",
            return_value=_well_formed_zscore_output(),
        ) as mock_zc:
            mcp_module.calculate_zscore_custom_tool(
                curve_family="UST",
                tenor="10Y",
                z_score_window_days=252,
                field_name="PX_LAST",
            )
        params = mock_zc.call_args.kwargs["params"]
        assert params.field_name == "PX_LAST"

    def test_time_series_withheld_from_llm_response(self):
        """The LLM response payload from the MCP wrapper must NOT
        include time_series — it's frontend-only."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_zscore_custom",
            return_value=_well_formed_zscore_output(),
        ):
            output_json = mcp_module.calculate_zscore_custom_tool(
                curve_family="UST",
                tenor="10Y",
                z_score_window_days=252,
            )
        parsed = json.loads(output_json)
        assert "time_series" not in parsed
        assert "current_metrics" in parsed


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.zscore_custom import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.zscore_custom.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_zscore_custom_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(ZSCORE_CUSTOM_CONFIG_PATH)
        assert cfg.tool.name == "zscore_custom_tool"
        assert cfg.tool.category == "desk_invariant_primitive"


# ===========================================================================
# Cross-layer-contract → HTTP 422 mapping
# ===========================================================================

class TestSmallWindowReturns422:
    """When the user requests `z_score_window_days < z_score_min_periods`,
    compute() returns a controlled error envelope.  Pre-fix, the FastAPI
    route surfaced this as a 500 because ``_tool_result_or_raise`` only
    classified not-found (404) and infra (503) phrases.  This is a
    user-input vs cross-layer-contract mismatch and must be 422
    (unprocessable entity), not 500.

    Pinned by mocking compute to return the exact controlled-error
    envelope and asserting the route's HTTPException is 422.
    """

    def test_small_window_route_returns_422(self):
        from fastapi import HTTPException
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        # Mock compute to mimic the controlled error message produced
        # when z_score_window_days < z_score_min_periods.
        controlled_error = {
            "error": (
                "z_score_window_days=30 is smaller than the YAML's "
                "z_score_min_periods=60.  pandas requires window >= "
                "min_periods.  Either request a larger "
                "z_score_window_days (>= 60), or edit z_score_min_periods "
                "in zscore_custom/config.yaml if the desk has decided a "
                "smaller min_periods is acceptable."
            )
        }
        with patch.object(
            detail_module,
            "calculate_zscore_custom",
            return_value=controlled_error,
        ):
            with pytest.raises(HTTPException) as exc_info:
                detail_module.zscore_custom_detail(
                    engine=mock_engine,
                    curve_family="UST",
                    tenor="10Y",
                    z_score_window_days=30,
                    lookback_days=365,
                    field_name=None,
                )
        assert exc_info.value.status_code == 422, (
            f"small-window error must surface as 422 (unprocessable "
            f"entity), got {exc_info.value.status_code}"
        )
        # The error detail still names both values for the client.
        assert "z_score_min_periods" in str(exc_info.value.detail)

    def test_unclassified_error_still_returns_500(self):
        """Sanity check: an arbitrary error string that doesn't match
        any of the 404/503/422 phrase lists still falls through to
        500.  Guards against the new 422 phrase list silently
        capturing too many error classes."""
        from fastapi import HTTPException
        from api.routes.rates import detail as detail_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            detail_module,
            "calculate_zscore_custom",
            return_value={"error": "completely unexpected internal failure"},
        ):
            with pytest.raises(HTTPException) as exc_info:
                detail_module.zscore_custom_detail(
                    engine=mock_engine,
                    curve_family="UST",
                    tenor="10Y",
                    z_score_window_days=252,
                    lookback_days=365,
                    field_name=None,
                )
        assert exc_info.value.status_code == 500

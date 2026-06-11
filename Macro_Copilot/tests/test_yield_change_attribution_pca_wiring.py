"""
test_yield_change_attribution_pca_wiring.py — Caller-wiring smoke tests.

Per the v6 transport rule, yield_change_attribution_pca has nested
union-style input (``Optional[PastedPcaLoadings]`` plus inline-fit
fallbacks) so it ships **MCP only this sprint**.  No FastAPI route
until the UI-integration PR — pinned by the explicit absence test.

Carries forward the half_life follow-up's lesson: the pasted_loadings
path must NOT require a live DB engine (it doesn't fetch).  Three
new wiring tests pin the conditional engine acquisition.

Plus the rolling_regression follow-up's lesson on nested-MCP
transport: schema-introspection success ≠ transport-execution
success.  TestNestedMcpTransportExecution invokes ``mcp.call_tool``
with both fit_inline (flat-only args) AND pasted (full nested
shape) and verifies the wrapper successfully constructs the right
Pydantic input + reaches the patched compute().
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import List
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca import (
    CONFIG_PATH as YIELD_CHANGE_ATTRIBUTION_PCA_CONFIG_PATH,
)
from shared.config import ToolConfig, clear_tool_config_cache
from shared.schemas import (
    PastedPcaComponentMetadata,
    PastedPcaLoadings,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_tool_config_cache()
    yield
    clear_tool_config_cache()


def _well_formed_ycap_output() -> dict:
    return {
        "current_metrics": {
            "start_date_requested": "2026-04-20",
            "start_date_resolved": "2026-04-20",
            "end_date_requested": "2026-04-30",
            "end_date_resolved": "2026-04-30",
            "curve_family": "UST",
            "target_tenor": "10Y",
            "total_change_bps": 12.5,
            "component_contributions": [
                {
                    "component_name": "pc1",
                    "contribution_bps": 8.0,
                    "loading_at_target_tenor": 0.4123,
                    "variance_share_in_fit_window": 0.7000,
                    "quality_flag": "ok",
                },
                {
                    "component_name": "pc2",
                    "contribution_bps": 3.5,
                    "loading_at_target_tenor": -0.2345,
                    "variance_share_in_fit_window": 0.2000,
                    "quality_flag": "ok",
                },
                {
                    "component_name": "pc3",
                    "contribution_bps": 0.8,
                    "loading_at_target_tenor": 0.1234,
                    "variance_share_in_fit_window": 0.0500,
                    "quality_flag": "ok",
                },
            ],
            "residual_bps": 0.2,
            "n_components_used": 3,
            "loadings_source": "fit_inline",
            "loadings_window_start": "2021-04-30",
            "loadings_window_end": "2026-04-30",
            "loadings_change_frequency_used": "daily",
            "loadings_n_observations_in_fit": 1250,
            "loadings_sign_anchor_used": "lock_pc_long_tenor_positive",
            "loadings_change_window_overlap_pct": 100.0,
        },
    }


def _make_pasted_loadings() -> PastedPcaLoadings:
    """Minimal valid pasted_loadings for the transport tests.  Built
    with QR so loadings are unit-norm orthonormal."""
    tenors = ["1Y", "2Y", "5Y", "10Y"]
    rng = np.random.default_rng(11)
    A = rng.normal(size=(4, 3))
    Q, _ = np.linalg.qr(A)
    components = Q.T  # shape [3, 4]
    for k in range(3):
        if components[k, 3] < 0:
            components[k] *= -1.0
    return PastedPcaLoadings(
        curve_family="UST",
        tenors=tenors,
        components=[list(map(float, components[k])) for k in range(3)],
        component_names=["pc1", "pc2", "pc3"],
        variance_shares=[0.70, 0.20, 0.10],
        fit_window_start="2021-01-04",
        fit_window_end="2026-04-30",
        change_frequency_used="daily",
        n_observations_in_fit=1250,
        sign_anchor_used="lock_pc_long_tenor_positive",
        component_metadata=[
            PastedPcaComponentMetadata(
                component_name=f"pc{k+1}",
                quality_flag="ok",
                quality_note=None,
            )
            for k in range(3)
        ],
    )


def _assert_ycap_config_passed(call_args) -> None:
    cfg = call_args.kwargs.get("config")
    assert cfg is not None
    assert isinstance(cfg, ToolConfig)
    assert cfg.tool.name == "yield_change_attribution_pca_tool"
    assert "min_observations_for_pca" in cfg.conventions
    # Date-resolution policies must be locked at the YAML defaults.
    assert cfg.convention_value("start_date_resolution") == "forward"
    assert cfg.convention_value("end_date_resolution") == "backward"


# ===========================================================================
# rates_agent/sovereign_bonds/mcp_server.py — calculate_yield_change_attribution_pca_tool
# ===========================================================================

class TestMcpYcapWrapper:
    def test_passes_config_to_tool(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_yield_change_attribution_pca",
            return_value=_well_formed_ycap_output(),
        ) as mock_ycap:
            output_json = mcp_module.calculate_yield_change_attribution_pca_tool(
                curve_family="UST",
                target_tenor="10Y",
                start_date="2026-04-20",
                end_date="2026-04-30",
            )
        assert mock_ycap.call_count == 1
        _assert_ycap_config_passed(mock_ycap.call_args)
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed

    def test_signature_uses_optional_pasted_loadings_plus_flat_inline(self):
        """Per v6 plan Delta P: the wrapper signature exposes
        Optional[PastedPcaLoadings] plus the flat inline-fit fields.
        Pin the type hints so a maintainer can't accidentally remove
        the nested input."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import typing

        fn = mcp_module.calculate_yield_change_attribution_pca_tool
        underlying = getattr(fn, "fn", fn)
        underlying = getattr(underlying, "__wrapped__", underlying)
        hints = typing.get_type_hints(underlying)
        hints.pop("return", None)

        # pasted_loadings is Optional[PastedPcaLoadings]
        ann = hints["pasted_loadings"]
        origin = typing.get_origin(ann)
        args = typing.get_args(ann)
        assert origin is typing.Union, (
            f"pasted_loadings must be Optional[PastedPcaLoadings]; "
            f"got origin {origin!r}"
        )
        assert PastedPcaLoadings in args and type(None) in args
        # Other fields are flat scalars / List[str].
        assert hints["curve_family"] is str
        assert hints["target_tenor"] is str
        assert hints["start_date"] is str
        assert hints["end_date"] is str
        assert hints["pca_lookback_days"] is int
        assert hints["n_components"] is int
        assert hints["field_name"] is str

    def test_pasted_path_STILL_acquires_db_engine(self):
        """T14's pasted_loadings path is DIFFERENT from half_life's
        pasted_series path: T14 still needs the DB to fetch the
        change-window yield panel (to compute Δy at the requested
        tenors).  ``pasted_loadings`` supplies the LOADINGS only —
        not the yield panel.

        Pin the contract so a future maintainer doesn't try to
        replicate the half_life "skip engine on pasted" optimisation
        here without first plumbing pasted_yield_panel as a separate
        sibling-tool input."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        paste = _make_pasted_loadings()
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ) as mock_get_engine, patch.object(
            mcp_module,
            "calculate_yield_change_attribution_pca",
            return_value=_well_formed_ycap_output(),
        ) as mock_compute:
            mcp_module.calculate_yield_change_attribution_pca_tool(
                curve_family="UST",
                target_tenor="10Y",
                start_date="2026-04-20",
                end_date="2026-04-30",
                pasted_loadings=paste,
            )
        # _get_engine IS called on the pasted path — change-window
        # fetch still requires the DB.  compute() receives the live
        # engine.
        assert mock_get_engine.call_count == 1
        assert mock_compute.call_args.kwargs["engine"] is mock_engine

    def test_fit_inline_path_acquires_db_engine(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ) as mock_get_engine, patch.object(
            mcp_module,
            "calculate_yield_change_attribution_pca",
            return_value=_well_formed_ycap_output(),
        ) as mock_compute:
            mcp_module.calculate_yield_change_attribution_pca_tool(
                curve_family="UST",
                target_tenor="10Y",
                start_date="2026-04-20",
                end_date="2026-04-30",
            )
        assert mock_get_engine.call_count == 1
        assert mock_compute.call_args.kwargs["engine"] is mock_engine

    def test_field_name_default_is_empty_string_sentinel(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module
        import inspect
        sig = inspect.signature(mcp_module.calculate_yield_change_attribution_pca_tool)
        default = sig.parameters["field_name"].default
        assert default == "", (
            f"MCP wrapper's field_name default must be '' (empty-"
            f"string sentinel); got {default!r}"
        )

    def test_omitted_field_name_flows_none_to_input(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_yield_change_attribution_pca",
            return_value=_well_formed_ycap_output(),
        ) as mock_compute:
            mcp_module.calculate_yield_change_attribution_pca_tool(
                curve_family="UST",
                target_tenor="10Y",
                start_date="2026-04-20",
                end_date="2026-04-30",
                # field_name omitted → defaults to ""
            )
        params = mock_compute.call_args.kwargs["params"]
        assert params.field_name is None

    def test_no_time_series_in_llm_response(self):
        """T14 is a snapshot-only tool; verify the wrapper's LLM
        response carries only current_metrics."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_yield_change_attribution_pca",
            return_value=_well_formed_ycap_output(),
        ):
            output_json = mcp_module.calculate_yield_change_attribution_pca_tool(
                curve_family="UST", target_tenor="10Y",
                start_date="2026-04-20", end_date="2026-04-30",
            )
        parsed = json.loads(output_json)
        assert "current_metrics" in parsed
        for k in (
            "time_series", "time_series_betas",
            "time_series_residual", "time_series_factors",
        ):
            assert k not in parsed


# ===========================================================================
# api/routes/rates/detail.py — explicit absence of /detail/yield-change-attribution-pca
# ===========================================================================

class TestNoFastApiRouteThisSprint:
    def test_no_route_for_yield_change_attribution_pca(self):
        from api.routes.rates import detail as detail_module
        import inspect
        handlers = [
            name for name, obj in inspect.getmembers(detail_module)
            if inspect.isfunction(obj) and name.endswith("_detail")
        ]
        assert "yield_change_attribution_pca_detail" not in handlers, (
            "yield_change_attribution_pca has nested input "
            "(Optional[PastedPcaLoadings]) and ships MCP-only this "
            "sprint per the v6 transport rule.  Adding a FastAPI "
            "route requires the POST/JSON-body decision documented "
            "in v6 plan Delta H first."
        )


# ===========================================================================
# Nested MCP transport — call_tool() end-to-end (BOTH input modes)
# ===========================================================================

class TestNestedMcpTransportExecution:
    """Carries forward the rolling_regression + half_life follow-up's
    lesson: schema-introspection success ≠ transport-execution
    success.  Pin both input paths through the FastMCP transport."""

    def test_call_tool_with_fit_inline_succeeds(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_yield_change_attribution_pca",
            return_value=_well_formed_ycap_output(),
        ) as mock_compute:
            args = {
                "curve_family": "UST",
                "target_tenor": "10Y",
                "start_date": "2026-04-20",
                "end_date": "2026-04-30",
                "pca_lookback_days": 1825,
                "n_components": 3,
                "change_frequency": "daily",
            }
            result = asyncio.run(
                mcp_module.mcp.call_tool(
                    "calculate_yield_change_attribution_pca_tool", args,
                )
            )
        assert isinstance(result, tuple) and len(result) == 2
        contents, _ = result
        parsed = json.loads(contents[0].text)
        assert "current_metrics" in parsed
        # Compute received a properly-constructed Pydantic input
        # with pasted_loadings=None (fit_inline path).
        assert mock_compute.call_count == 1
        params = mock_compute.call_args.kwargs["params"]
        assert params.pasted_loadings is None
        assert params.curve_family == "UST"
        assert params.target_tenor == "10Y"

    def test_call_tool_with_pasted_loadings_succeeds(self):
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        paste = _make_pasted_loadings()
        mock_engine = MagicMock(name="engine")
        with patch.object(
            mcp_module, "_get_engine", return_value=mock_engine,
        ), patch.object(
            mcp_module,
            "calculate_yield_change_attribution_pca",
            return_value=_well_formed_ycap_output(),
        ) as mock_compute:
            args = {
                "curve_family": "UST",
                "target_tenor": "10Y",
                "start_date": "2026-04-20",
                "end_date": "2026-04-30",
                "pasted_loadings": paste.model_dump(),
                "n_components": 3,
            }
            result = asyncio.run(
                mcp_module.mcp.call_tool(
                    "calculate_yield_change_attribution_pca_tool", args,
                )
            )
        assert isinstance(result, tuple)
        params = mock_compute.call_args.kwargs["params"]
        assert params.pasted_loadings is not None
        assert params.pasted_loadings.curve_family == "UST"
        assert params.pasted_loadings.n_observations_in_fit == 1250
        # Engine IS provided on the pasted path (compute still needs
        # the change-window fetch).
        assert mock_compute.call_args.kwargs["engine"] is mock_engine

    def test_call_tool_with_invalid_dates_surfaces_validation_error(self):
        """Schema-layer validator (start < end) must surface as a
        controlled validation error envelope, NOT a Python traceback."""
        from rates_agent.sovereign_bonds import mcp_server as mcp_module

        args = {
            "curve_family": "UST",
            "target_tenor": "10Y",
            "start_date": "2026-04-30",
            "end_date": "2026-04-30",  # NOT strictly after start
        }
        result = asyncio.run(
            mcp_module.mcp.call_tool(
                "calculate_yield_change_attribution_pca_tool", args,
            )
        )
        contents, _ = result
        parsed = json.loads(contents[0].text)
        assert "error" in parsed
        assert "before" in parsed["error"].lower() or \
            "strictly" in parsed["error"].lower()


# ===========================================================================
# CONFIG_PATH public symbol
# ===========================================================================

class TestConfigPathPublicSymbol:
    def test_config_path_via_package_init_and_compute_are_identical(self):
        from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca import (
            CONFIG_PATH as via_package,
        )
        from rates_agent.sovereign_bonds.tools.yield_change_attribution_pca.compute import (
            CONFIG_PATH as via_compute,
        )
        assert via_package == via_compute

    def test_load_returns_yield_change_attribution_pca_config(self):
        from shared.config import load_tool_config
        cfg = load_tool_config(YIELD_CHANGE_ATTRIBUTION_PCA_CONFIG_PATH)
        assert cfg.tool.name == "yield_change_attribution_pca_tool"
        assert cfg.tool.category == "quant_standard_analytic"
